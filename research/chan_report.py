"""缠论日线波段的分段研究。只读取已经冻结的确认事件，不改基本面报告。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from backtest.engine import (
    DEFAULT_COSTS,
    INITIAL_CAPITAL,
    LOT_SIZE,
    TradingCosts,
    buy_cash,
    execution_price,
)
from core.metrics import performance
from research.config import Period
from research.walkforward import benchmark_curve
from strategy.chan.bars import BI_STANDARD, CHAN_VERSION
from strategy.chan.simulate import MAX_POSITIONS, simulate_chan


@dataclass(frozen=True)
class ChanResearchConfig:
    """缠论波段的研究契约。锁定样本不参与买卖定义的修改。"""

    development: Period
    validation: Period
    holdout: Period
    capital: float = INITIAL_CAPITAL
    max_positions: int = MAX_POSITIONS
    costs: TradingCosts = field(default_factory=TradingCosts)
    benchmark_code: str = "000300.SH"
    min_holdout_months: int = 12
    max_holdout_drawdown: float = -0.20
    max_cost_to_average_equity: float = 0.05
    min_holdout_sells: int = 5
    max_winner_share: float = 0.80

    def __post_init__(self) -> None:
        periods = (self.development, self.validation, self.holdout)
        for left, right in zip(periods[:-1], periods[1:], strict=True):
            if left.end >= right.start:
                raise ValueError("开发、验证和锁定样本外区间必须严格顺序且不重叠")
        if self.max_positions < 1 or self.min_holdout_months < 1 or self.max_holdout_drawdown > 0:
            raise ValueError("缠论研究参数无效")
        if not 0 < self.max_winner_share <= 1:
            raise ValueError("单笔收益集中度门槛必须在 0 和 1 之间")

    @property
    def periods(self) -> tuple[Period, Period, Period]:
        return self.development, self.validation, self.holdout

    def as_dict(self) -> dict[str, object]:
        return {
            "development": self.development.as_dict(),
            "validation": self.validation.as_dict(),
            "holdout": self.holdout.as_dict(),
            "capital": self.capital,
            "max_positions": self.max_positions,
            "costs": asdict(self.costs),
            "benchmark_code": self.benchmark_code,
            "chan_core": CHAN_VERSION,
            "bi_standard": BI_STANDARD,
            "price_return_only": True,
            "min_holdout_months": self.min_holdout_months,
            "max_holdout_drawdown": self.max_holdout_drawdown,
            "max_cost_to_average_equity": self.max_cost_to_average_equity,
            "min_holdout_sells": self.min_holdout_sells,
            "max_winner_share": self.max_winner_share,
        }


def make_chan_config(
    start: str | pd.Timestamp,
    development_end: str | pd.Timestamp,
    validation_end: str | pd.Timestamp,
    end: str | pd.Timestamp,
    **kwargs: object,
) -> ChanResearchConfig:
    """分段边界的次日自动成为下一段开始。"""
    development_end = pd.Timestamp(development_end).normalize()
    validation_end = pd.Timestamp(validation_end).normalize()
    if validation_end <= development_end:
        raise ValueError("开发、验证和锁定样本外区间必须严格顺序且不重叠")
    return ChanResearchConfig(
        development=Period("development", pd.Timestamp(start), development_end),
        validation=Period("validation", development_end + pd.Timedelta(days=1), validation_end),
        holdout=Period("holdout", validation_end + pd.Timedelta(days=1), pd.Timestamp(end)),
        **kwargs,
    )


def run_chan_research(
    bars: pd.DataFrame,
    events: pd.DataFrame,
    index_bars: pd.DataFrame,
    config: ChanResearchConfig,
    root: str | Path,
    universe: pd.DataFrame | None = None,
) -> dict[str, object]:
    """写入独立目录。调用方传入的事件必须已经按收盘前缀确认。"""
    root = Path(root)
    report_path = root / "report.md"
    if report_path.exists():
        raise FileExistsError(f"研究报告已存在：{report_path}")
    root.mkdir(parents=True, exist_ok=True)
    events.to_parquet(root / "events.parquet", index=False)
    records: list[dict[str, object]] = []
    for period in config.periods:
        result = simulate_chan(
            bars,
            events,
            index_bars,
            period.start,
            period.end,
            capital=config.capital,
            max_positions=config.max_positions,
            costs=config.costs,
        )
        benchmark = benchmark_curve(index_bars, period, config.capital)
        equal = equal_weight_equity(
            bars,
            period.start,
            period.end,
            config.capital,
            config.costs,
            _universe_codes(universe, period, bars),
        )
        directory = root / "walkforward" / period.name
        directory.mkdir(parents=True, exist_ok=True)
        result.equity.to_parquet(directory / "strategy_equity.parquet", index=False)
        result.trades.to_parquet(directory / "trades.parquet", index=False)
        benchmark.to_parquet(directory / "benchmark_equity.parquet", index=False)
        equal.to_parquet(directory / "equal_weight_equity.parquet", index=False)
        turnover, cost_ratio = _cost_stats(result.equity, result.trades, config.costs)
        sells = int(result.trades["side"].eq("sell").sum()) if not result.trades.empty else 0
        records.append(
            {
                "period": period.name,
                **{f"strategy_{key}": value for key, value in result.metrics.items()},
                **{f"benchmark_{key}": value for key, value in performance(benchmark).items()},
                **{f"equal_weight_{key}": value for key, value in performance(equal).items()},
                "trades": int(len(result.trades)),
                "sells": sells,
                "annualized_turnover": turnover,
                "estimated_cost_to_average_equity": cost_ratio,
                "winner_share": _winner_share(result.trades),
                "strategy_end": _end_equity(result.equity),
                "equal_weight_end": _end_equity(equal),
            }
        )
    summary = pd.DataFrame(records)
    walk_root = root / "walkforward"
    walk_root.mkdir(parents=True, exist_ok=True)
    summary.to_parquet(walk_root / "summary.parquet", index=False)
    decision, reasons = decision_gate(config, summary)
    payload = {
        "config": config.as_dict(),
        "decision": decision,
        "reasons": reasons,
        "price_return_only": True,
        "known_gaps": [
            "买卖点使用 chan-lun-core 笔级简化口径，不是第 101 课的精确二类点。",
            "价格收益不含复权、分红和公司行动。",
            "涨跌停按前收 10% 判断，没有覆盖 20% 和 ST 板块，也没有建模排队成交。",
        ],
    }
    (root / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(_render(payload, summary), encoding="utf-8")
    return {"decision": decision, "reasons": reasons, "summary": summary}


def decision_gate(config: ChanResearchConfig, summary: pd.DataFrame) -> tuple[str, list[str]]:
    """开发段不改规则。验证段可以否决，锁定段只做一次验收。"""
    holdout = summary.loc[summary["period"].eq("holdout")]
    if holdout.empty:
        return "evidence_insufficient", ["没有锁定样本外净值。"]
    row = holdout.iloc[0]
    months = max(
        (config.holdout.end.year - config.holdout.start.year) * 12
        + config.holdout.end.month
        - config.holdout.start.month
        + 1,
        0,
    )
    reasons: list[str] = []
    if months < config.min_holdout_months:
        reasons.append(f"锁定样本外仅 {months} 个月，少于门槛 {config.min_holdout_months} 个月。")
    drawdown = row["strategy_max_drawdown"]
    if pd.isna(drawdown) or float(drawdown) < config.max_holdout_drawdown:
        reasons.append("锁定样本外最大回撤超过预先设定门槛。")
    cost_ratio = row["estimated_cost_to_average_equity"]
    if pd.isna(cost_ratio) or float(cost_ratio) > config.max_cost_to_average_equity:
        reasons.append("成本占平均资产比例超过预先设定门槛，或成本结果缺失。")
    if int(row["sells"]) < config.min_holdout_sells:
        reasons.append("锁定样本卖出次数不足以覆盖佣金，也不能判断收益是否集中在少数交易。")
    outperformed = float(row["strategy_end"]) > float(row["equal_weight_end"])
    winner = row["winner_share"]
    if outperformed and pd.notna(winner) and float(winner) > config.max_winner_share:
        reasons.append("相对同池等权持有的优势集中在少数交易。")
    if reasons:
        return ("evidence_insufficient" if months < config.min_holdout_months else "reject_current_definition"), reasons
    return "continue_validation", ["确认事件、成本和锁定样本门槛通过；仍需复权和成交可行性验证。"]


def equal_weight_equity(
    bars: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    capital: float,
    costs: TradingCosts = DEFAULT_COSTS,
    codes: list[str] | None = None,
) -> pd.DataFrame:
    """同池等权买入持有。首日按收盘价建仓，之后只按收盘价盯市。"""
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    frame = bars.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    dates = [pd.Timestamp(day) for day in sorted(frame.loc[frame["trade_date"].between(start, end), "trade_date"].dropna().unique())]
    if not dates:
        return pd.DataFrame(columns=["trade_date", "equity"])
    first = frame.loc[frame["trade_date"].eq(dates[0])].dropna(subset=["close"])
    available = set(first["ts_code"])
    selected = sorted(available if codes is None else [str(code) for code in codes if str(code) in available])
    cash = float(capital)
    held: dict[str, int] = {}
    if selected:
        target = float(capital) / len(selected)
        for code in selected:
            price = float(first.loc[first["ts_code"].eq(code), "close"].iloc[-1])
            if price <= 0:
                continue
            shares = _lots(target / execution_price(price, "buy", costs))
            while shares >= LOT_SIZE:
                spent, _fill = buy_cash(price, shares, costs)
                if spent <= cash + 1e-9:
                    cash -= spent
                    held[code] = shares
                    break
                shares -= LOT_SIZE
    marks: dict[str, float] = {}
    curve: list[dict[str, object]] = []
    for day in dates:
        current = frame.loc[frame["trade_date"].eq(day)]
        for row in current.itertuples(index=False):
            if pd.notna(row.close) and float(row.close) > 0:
                marks[str(row.ts_code)] = float(row.close)
        value = cash + sum(shares * marks.get(code, 0.0) for code, shares in held.items())
        curve.append({"trade_date": day, "equity": value})
    return pd.DataFrame(curve)


def _universe_codes(universe: pd.DataFrame | None, period: Period, bars: pd.DataFrame) -> list[str] | None:
    if universe is None:
        return None
    if universe.empty or "ts_code" not in universe.columns or "asof" not in universe.columns:
        return []
    members = universe.copy()
    members["ts_code"] = members["ts_code"].astype(str)
    members["asof"] = pd.to_datetime(members["asof"], errors="coerce").dt.normalize()
    window = members.loc[members["asof"].between(period.start, period.end)]
    if not window.empty:
        return sorted(window["ts_code"].unique())
    prior = members.loc[members["asof"].le(period.start)]
    if prior.empty:
        return []
    last = prior["asof"].max()
    return sorted(prior.loc[prior["asof"].eq(last), "ts_code"].unique())


def _cost_stats(equity: pd.DataFrame, trades: pd.DataFrame, costs: TradingCosts) -> tuple[float, float]:
    if trades is None or trades.empty or equity is None or equity.empty:
        return 0.0, 0.0
    trade_value = pd.to_numeric(trades["price"], errors="coerce") * pd.to_numeric(trades["shares"], errors="coerce")
    commissions = trade_value.map(lambda amount: max(costs.min_commission, abs(float(amount)) * costs.commission_rate))
    taxes = trade_value.where(trades["side"].eq("sell"), 0.0) * costs.stamp_tax
    slippage = trade_value * costs.slippage
    estimated = float((commissions + taxes + slippage).sum())
    average = float(pd.to_numeric(equity["equity"], errors="coerce").mean())
    sessions = max(len(equity), 1)
    turnover = float(trade_value.sum()) / average * 252 / sessions if average > 0 else float("nan")
    ratio = estimated / average if average > 0 else float("nan")
    return turnover, ratio


def _winner_share(trades: pd.DataFrame) -> float:
    """已实现盈利里，单只股票所占的最大比例。没有盈利时为 0。"""
    if trades is None or trades.empty:
        return 0.0
    lots: dict[str, list[list[float]]] = {}
    profit: dict[str, float] = {}
    ordered = trades.sort_values(["trade_date", "ts_code"])
    for row in ordered.itertuples(index=False):
        book = lots.setdefault(str(row.ts_code), [])
        if row.side == "buy":
            book.append([float(row.shares), float(row.price)])
            continue
        left = int(row.shares)
        gained = 0.0
        while left > 0 and book:
            take = min(left, int(book[0][0]))
            gained += take * (float(row.price) - book[0][1])
            book[0][0] -= take
            left -= take
            if book[0][0] <= 0:
                book.pop(0)
        profit[str(row.ts_code)] = profit.get(str(row.ts_code), 0.0) + gained
    winners = [value for value in profit.values() if value > 0]
    if not winners:
        return 0.0
    return float(max(winners) / sum(winners))


def _end_equity(equity: pd.DataFrame) -> float:
    if equity is None or equity.empty or "equity" not in equity.columns:
        return 0.0
    value = pd.to_numeric(equity["equity"], errors="coerce").dropna()
    if value.empty:
        return 0.0
    return float(value.iloc[-1])


def _lots(shares: float) -> int:
    if shares <= 0:
        return 0
    return int(float(shares) // LOT_SIZE) * LOT_SIZE


def _render(summary: dict[str, object], walkforward: pd.DataFrame) -> str:
    reasons = "\n".join(f"- {reason}" for reason in summary["reasons"])
    table = "没有回测结果。" if walkforward.empty else "```csv\n" + walkforward.to_csv(index=False) + "```"
    gaps = "\n".join(f"- {item}" for item in summary["known_gaps"])
    return f"""# 缠论日线波段研究报告

## 结论

`{summary["decision"]}`

{reasons}

## 研究规格

```json
{json.dumps(summary["config"], ensure_ascii=False, indent=2)}
```

## 策略、沪深300与同池等权

{table}

## 已知缺口

{gaps}
"""
