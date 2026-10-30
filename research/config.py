"""研究规格：时间切分和默认策略参数在运行前固定。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from backtest.engine import COMMISSION_RATE, INITIAL_CAPITAL, MIN_COMMISSION, SLIPPAGE, STAMP_TAX
from strategy.risk_control import EXIT_RANK


@dataclass(frozen=True)
class Period:
    """闭区间。相邻阶段不能重叠。"""

    name: str
    start: pd.Timestamp
    end: pd.Timestamp

    def __post_init__(self) -> None:
        start = pd.Timestamp(self.start).normalize()
        end = pd.Timestamp(self.end).normalize()
        if end < start:
            raise ValueError(f"{self.name} 的结束日早于开始日")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "start": self.start.strftime("%Y%m%d"), "end": self.end.strftime("%Y%m%d")}


@dataclass(frozen=True)
class CostScenario:
    name: str
    commission_rate: float = COMMISSION_RATE
    min_commission: float = MIN_COMMISSION
    stamp_tax: float = STAMP_TAX
    slippage: float = SLIPPAGE
    capital: float = INITIAL_CAPITAL
    top_n: int = 8
    exit_rank: int = EXIT_RANK

    def __post_init__(self) -> None:
        if not 5 <= self.top_n <= 8:
            raise ValueError("top_n 必须在 5 到 8 之间")
        if self.exit_rank < self.top_n:
            raise ValueError("exit_rank 不能小于 top_n")
        if min(self.commission_rate, self.min_commission, self.stamp_tax, self.slippage, self.capital) < 0:
            raise ValueError("成本和资金不能为负")


@dataclass(frozen=True)
class ResearchConfig:
    """基线策略的研究契约。锁定样本外不允许用于参数选择。"""

    development: Period
    validation: Period
    holdout: Period
    holding_sessions: int = 21
    quantiles: int = 5
    scenario: CostScenario = field(default_factory=lambda: CostScenario("baseline"))
    benchmark_code: str = "000300.SH"
    min_holdout_months: int = 12
    min_holdout_calmar: float = 0.0
    max_holdout_drawdown: float = -0.20
    max_cost_to_average_equity: float = 0.05

    def __post_init__(self) -> None:
        periods = (self.development, self.validation, self.holdout)
        for left, right in zip(periods[:-1], periods[1:], strict=True):
            if left.end >= right.start:
                raise ValueError("开发、验证和锁定样本外区间必须严格顺序且不重叠")
        if self.holding_sessions < 1:
            raise ValueError("holding_sessions 至少为 1")
        if self.quantiles < 2:
            raise ValueError("quantiles 至少为 2")
        if self.min_holdout_months < 1 or self.max_holdout_drawdown > 0:
            raise ValueError("研究决策门参数无效")

    @property
    def start(self) -> pd.Timestamp:
        return self.development.start

    @property
    def end(self) -> pd.Timestamp:
        return self.holdout.end

    @property
    def periods(self) -> tuple[Period, Period, Period]:
        return self.development, self.validation, self.holdout

    def as_dict(self) -> dict[str, object]:
        return {
            "development": self.development.as_dict(),
            "validation": self.validation.as_dict(),
            "holdout": self.holdout.as_dict(),
            "holding_sessions": self.holding_sessions,
            "quantiles": self.quantiles,
            "scenario": asdict(self.scenario),
            "benchmark_code": self.benchmark_code,
            "price_return_only": True,
            "min_holdout_months": self.min_holdout_months,
            "min_holdout_calmar": self.min_holdout_calmar,
            "max_holdout_drawdown": self.max_holdout_drawdown,
            "max_cost_to_average_equity": self.max_cost_to_average_equity,
        }


def make_config(
    start: str | pd.Timestamp,
    development_end: str | pd.Timestamp,
    validation_end: str | pd.Timestamp,
    end: str | pd.Timestamp,
    **kwargs: object,
) -> ResearchConfig:
    """CLI 和测试的简写；分段边界次日自动成为下一段开始。"""
    development_end = pd.Timestamp(development_end).normalize()
    validation_end = pd.Timestamp(validation_end).normalize()
    if validation_end <= development_end:
        raise ValueError("开发、验证和锁定样本外区间必须严格顺序且不重叠")
    return ResearchConfig(
        development=Period("development", pd.Timestamp(start), development_end),
        validation=Period("validation", development_end + pd.Timedelta(days=1), validation_end),
        holdout=Period("holdout", validation_end + pd.Timedelta(days=1), pd.Timestamp(end)),
        **kwargs,
    )
