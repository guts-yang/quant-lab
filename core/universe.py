"""把数据接口、公告日对齐和排雷串成一次截面。"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

import pandas as pd

from core.cleaner import CleanResult, CleanerConfig, UniverseCleaner, full_day_halt_codes
from core.pit import resolve_names
from core.provider import DataProvider, recent_period_ends


def build_universe(
    provider: DataProvider,
    asof: pd.Timestamp,
    config: CleanerConfig | None = None,
) -> CleanResult:
    asof = pd.Timestamp(asof).normalize()
    instruments = provider.get_instruments(asof)
    names = resolve_names(provider.get_name_history(), asof)
    instruments = instruments.merge(names, on="ts_code", how="left")
    halted = full_day_halt_codes(provider.get_suspensions(asof))
    instruments["is_suspended"] = instruments["ts_code"].isin(halted)

    bars = provider.get_daily_bars(asof - pd.Timedelta(days=90), asof - pd.Timedelta(days=1))
    # 十二个季度能覆盖最近两份年报。最新年报还没公告时，上一份仍在窗口里。
    periods = recent_period_ends(asof, n=12)
    balance_sheets = provider.get_balance_sheets(periods)
    indicators = provider.get_indicators(periods)
    return UniverseCleaner(config).filter(asof, instruments, bars, balance_sheets, indicators)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="按公告日对齐后，生成截面可交易股票池")
    parser.add_argument("--asof", required=True, help="截面日期，YYYYMMDD")
    args = parser.parse_args(list(argv) if argv is not None else None)

    from core.tushare_provider import TushareProvider

    asof = pd.Timestamp(args.asof)
    result = build_universe(TushareProvider(), asof)
    _print_result(asof, result)


def _print_result(asof: pd.Timestamp, result: CleanResult) -> None:
    print(f"asof={pd.Timestamp(asof):%Y%m%d} kept={len(result.kept)} rejected={len(result.rejected)}")
    counts: dict[str, int] = {}
    if result.rejected.empty:
        return
    for reasons in result.rejected["reasons"]:
        for reason in str(reasons).split(","):
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
    for reason in sorted(counts):
        print(f"{reason}={counts[reason]}")


if __name__ == "__main__":
    main()
