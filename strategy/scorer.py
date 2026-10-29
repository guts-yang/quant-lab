"""在排雷后的股票池里做截面秩打分，取出综合分最高的 5–8 只。"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

import pandas as pd

from factors.fundamental.growth import growth_factors
from factors.fundamental.quality import quality_factors
from factors.fundamental.value import value_factors
from factors.processor import prepare_factors

VALUE_FACTORS = ["ep_ttm", "bp", "dividend_yield"]
QUALITY_FACTORS = ["roe_ttm", "roic", "gross_margin_var"]
GROWTH_FACTORS = ["q_profit_yoy", "q_sales_yoy", "earnings_gap"]
FACTOR_COLUMNS = [*VALUE_FACTORS, *QUALITY_FACTORS, *GROWTH_FACTORS]
LOWER_IS_BETTER = {"gross_margin_var"}
QUALITY_WEIGHT = 0.4
VALUE_WEIGHT = 0.3
GROWTH_WEIGHT = 0.3


def score_cross_section(
    asof: pd.Timestamp,
    universe: pd.DataFrame,
    market: pd.DataFrame | None,
    balance_sheets: pd.DataFrame | None,
    indicators: pd.DataFrame | None,
    dividends: pd.DataFrame | None,
    prices: pd.DataFrame | None,
) -> pd.DataFrame:
    """返回候选池全部股票的因子、分组得分和综合分，按综合分从高到低排列。"""
    factors = assemble_factors(asof, universe, market, balance_sheets, indicators, dividends, prices)
    prepared = prepare_factors(factors, FACTOR_COLUMNS)
    prepared["quality_score"] = _group_score(prepared, QUALITY_FACTORS)
    prepared["value_score"] = _group_score(prepared, VALUE_FACTORS)
    prepared["growth_score"] = _group_score(prepared, GROWTH_FACTORS)
    prepared["composite"] = combine_group_scores(
        prepared["quality_score"],
        prepared["value_score"],
        prepared["growth_score"],
    )
    return prepared.sort_values(["composite", "ts_code"], ascending=[False, True], na_position="last").reset_index(drop=True)


def combine_group_scores(quality: pd.Series, value: pd.Series, growth: pd.Series) -> pd.Series:
    return QUALITY_WEIGHT * quality + VALUE_WEIGHT * value + GROWTH_WEIGHT * growth


def select_holdings(scores: pd.DataFrame, top_n: int = 8) -> pd.DataFrame:
    if not 5 <= int(top_n) <= 8:
        raise ValueError("top_n 必须在 5 到 8 之间")
    if scores.empty:
        return scores.reset_index(drop=True)
    ordered = scores.sort_values(["composite", "ts_code"], ascending=[False, True], na_position="last")
    return ordered.head(int(top_n)).reset_index(drop=True)


def score_candidates(
    provider: object,
    asof: pd.Timestamp,
    config: object | None = None,
) -> pd.DataFrame:
    """排雷后的全部候选和综合分，按综合分从高到低。打分公式与 select 之前相同。"""
    from core.provider import recent_period_ends
    from core.universe import build_universe

    asof = pd.Timestamp(asof).normalize()
    cleaned = build_universe(provider, asof, config)
    if cleaned.kept.empty:
        return pd.DataFrame(columns=["ts_code", "composite"])

    instruments = provider.get_instruments(asof)
    universe = cleaned.kept.copy()
    if instruments is not None and not instruments.empty and "industry" in instruments.columns:
        industry = instruments[["ts_code", "industry"]].copy()
        industry["ts_code"] = industry["ts_code"].astype(str)
        universe = universe.merge(industry.drop_duplicates("ts_code"), on="ts_code", how="left")

    periods = recent_period_ends(asof, n=12)
    return score_cross_section(
        asof,
        universe,
        provider.get_daily_basic(asof),
        provider.get_balance_sheets(periods),
        provider.get_factor_indicators(periods),
        provider.get_dividends(universe["ts_code"].astype(str).tolist()),
        provider.get_daily_prices(asof - pd.Timedelta(days=160), asof),
    )


def score_holdings(
    provider: object,
    asof: pd.Timestamp,
    top_n: int = 8,
    config: object | None = None,
) -> pd.DataFrame:
    return select_holdings(score_candidates(provider, asof, config), top_n)


def assemble_factors(
    asof: pd.Timestamp,
    universe: pd.DataFrame,
    market: pd.DataFrame | None,
    balance_sheets: pd.DataFrame | None,
    indicators: pd.DataFrame | None,
    dividends: pd.DataFrame | None,
    prices: pd.DataFrame | None,
) -> pd.DataFrame:
    codes = pd.Series(universe["ts_code"], copy=False).astype(str).drop_duplicates().reset_index(drop=True)
    frame = pd.DataFrame({"ts_code": codes})
    if "industry" in universe.columns:
        industry = universe[["ts_code", "industry"]].copy()
        industry["ts_code"] = industry["ts_code"].astype(str)
        frame = frame.merge(industry.drop_duplicates("ts_code"), on="ts_code", how="left")
    else:
        frame["industry"] = pd.NA
    frame = frame.merge(value_factors(asof, codes, market, balance_sheets, indicators, dividends), on="ts_code", how="left")
    frame = frame.merge(quality_factors(asof, codes, balance_sheets, indicators), on="ts_code", how="left")
    frame = frame.merge(growth_factors(asof, codes, indicators, prices), on="ts_code", how="left")
    return frame


def _group_score(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    ranks: list[pd.Series] = []
    for column in columns:
        if column not in frame.columns or frame[column].notna().sum() == 0:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if column in LOWER_IS_BETTER:
            ranks.append((-values).rank(method="average", pct=True))
        else:
            ranks.append(values.rank(method="average", pct=True))
    if not ranks:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")
    return pd.concat(ranks, axis=1).mean(axis=1, skipna=True)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="对排雷后的股票池做截面打分")
    parser.add_argument("--asof", required=True, help="截面日期，YYYYMMDD")
    parser.add_argument("--top", type=int, default=8, help="综合分最高的股票数，5 到 8")
    args = parser.parse_args(list(argv) if argv is not None else None)

    from core.tushare_provider import TushareProvider

    holdings = score_holdings(TushareProvider(), args.asof, top_n=args.top)
    _print_holdings(args.asof, holdings)


def _print_holdings(asof: pd.Timestamp, holdings: pd.DataFrame) -> None:
    print(f"asof={pd.Timestamp(asof):%Y%m%d} top={len(holdings)}")
    if holdings.empty:
        return
    columns = [name for name in ["ts_code", "composite", "quality_score", "value_score", "growth_score"] if name in holdings.columns]
    print(holdings[columns].to_string(index=False))


if __name__ == "__main__":
    main()
