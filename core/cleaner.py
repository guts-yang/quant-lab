"""股票池硬性排雷。只接收已经取好的表，不访问网络。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.pit import latest_visible
from core.provider import FINANCIAL_COMP_TYPES, _code, annual_indicators

REASON_LISTED = "listed_lt_180"
REASON_DELISTED = "delisted"
REASON_ST = "st"
REASON_DELISTING = "delisting"
REASON_SUSPENDED = "suspended"
REASON_ILLIQUID_HISTORY = "illiquid_history"
REASON_ILLIQUID = "illiquid"
REASON_MISSING_BALANCE = "missing_balance_sheet"
REASON_EQUITY = "non_positive_equity"
REASON_GOODWILL = "goodwill"
REASON_LEVERAGE = "leverage"
REASON_PROFIT = "negative_deducted_profit"

REASON_ORDER = [
    REASON_LISTED,
    REASON_DELISTED,
    REASON_ST,
    REASON_DELISTING,
    REASON_SUSPENDED,
    REASON_ILLIQUID_HISTORY,
    REASON_ILLIQUID,
    REASON_MISSING_BALANCE,
    REASON_EQUITY,
    REASON_GOODWILL,
    REASON_LEVERAGE,
    REASON_PROFIT,
]

_KEPT_COLUMNS = ["ts_code", "name", "list_date", "delist_date"]
_REJECTED_COLUMNS = [*_KEPT_COLUMNS, "reasons"]
_CONSOLIDATED_REPORTS = {"1", "5"}


@dataclass(frozen=True)
class CleanerConfig:
    min_list_days: int = 180
    liquidity_window: int = 20
    min_liquidity_obs: int = 10
    liquidity_quantile: float = 0.10
    goodwill_to_equity_max: float = 0.30
    debt_to_asset_max: float = 0.80
    apply_leverage_filter_to_financials: bool = True


@dataclass(frozen=True)
class CleanResult:
    kept: pd.DataFrame
    rejected: pd.DataFrame


class UniverseCleaner:
    def __init__(self, config: CleanerConfig | None = None) -> None:
        self.config = config or CleanerConfig()

    def filter(
        self,
        asof: pd.Timestamp,
        instruments: pd.DataFrame,
        bars: pd.DataFrame,
        balance_sheets: pd.DataFrame,
        indicators: pd.DataFrame,
    ) -> CleanResult:
        asof = pd.Timestamp(asof).normalize()
        if instruments is None or instruments.empty:
            return CleanResult(
                pd.DataFrame(columns=_KEPT_COLUMNS),
                pd.DataFrame(columns=_REJECTED_COLUMNS),
            )

        universe = _prepare_instruments(instruments)
        flags = pd.DataFrame({"ts_code": universe["ts_code"]})
        flags[REASON_LISTED] = _listed_too_short(universe, asof, self.config.min_list_days)
        flags[REASON_DELISTED] = _delisted(universe, asof)
        st, retiring = _name_flags(universe["name"])
        flags[REASON_ST] = st.to_numpy()
        flags[REASON_DELISTING] = retiring.to_numpy()
        flags[REASON_SUSPENDED] = universe["is_suspended"].fillna(False).astype(bool).to_numpy()
        flags = flags.merge(_liquidity_flags(asof, bars, universe["ts_code"], self.config), on="ts_code", how="left")
        flags = flags.merge(
            _fundamental_flags(asof, universe["ts_code"], balance_sheets, indicators, self.config),
            on="ts_code",
            how="left",
        )
        for reason in REASON_ORDER:
            flags[reason] = flags[reason].fillna(False).astype(bool)
        flags["reasons"] = flags.apply(_reasons_for_row, axis=1)

        output = universe[_KEPT_COLUMNS].merge(flags[["ts_code", "reasons"]], on="ts_code", how="left")
        output = output.sort_values("ts_code", kind="mergesort").reset_index(drop=True)
        kept = output.loc[output["reasons"].eq(""), _KEPT_COLUMNS].reset_index(drop=True)
        rejected = output.loc[output["reasons"].ne(""), _REJECTED_COLUMNS].reset_index(drop=True)
        return CleanResult(kept=kept, rejected=rejected)


def full_day_halt_codes(suspensions: pd.DataFrame | None) -> set[str]:
    """全天停牌的代码。suspend_timing 有盘中时段的不计入。"""
    if suspensions is None or suspensions.empty or "ts_code" not in suspensions.columns:
        return set()
    frame = suspensions
    if "suspend_type" in frame.columns:
        kind = frame["suspend_type"].map(_code)
        frame = frame.loc[kind.isna() | kind.eq("S")]
    if frame.empty:
        return set()
    if "suspend_timing" not in frame.columns:
        return set(frame["ts_code"].astype(str))
    text = frame["suspend_timing"].astype("string").str.strip()
    blank = frame["suspend_timing"].isna() | text.isna() | text.eq("") | text.str.lower().isin(["none", "nan", "<na>"])
    return set(frame.loc[blank, "ts_code"].astype(str))


def _prepare_instruments(instruments: pd.DataFrame) -> pd.DataFrame:
    frame = instruments.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    if "name" not in frame.columns:
        frame["name"] = pd.NA
    if "list_date" not in frame.columns:
        frame["list_date"] = pd.NaT
    if "delist_date" not in frame.columns:
        frame["delist_date"] = pd.NaT
    if "is_suspended" not in frame.columns:
        frame["is_suspended"] = False
    frame = frame.drop_duplicates("ts_code", keep="last")
    return frame.reset_index(drop=True)


def _listed_too_short(universe: pd.DataFrame, asof: pd.Timestamp, min_list_days: int) -> pd.Series:
    listed = pd.to_datetime(universe["list_date"], errors="coerce")
    age = (asof - listed).dt.days
    return listed.isna() | age.lt(min_list_days)


def _delisted(universe: pd.DataFrame, asof: pd.Timestamp) -> pd.Series:
    delist = pd.to_datetime(universe["delist_date"], errors="coerce")
    return delist.notna() & delist.le(asof)


def _name_flags(names: pd.Series) -> tuple[pd.Series, pd.Series]:
    text = names.astype("string")
    st = text.str.upper().str.contains("ST", regex=False, na=False)
    retiring = text.str.contains("退", regex=False, na=False)
    return st, retiring


def _liquidity_flags(
    asof: pd.Timestamp,
    bars: pd.DataFrame | None,
    codes: pd.Series,
    config: CleanerConfig,
) -> pd.DataFrame:
    result = pd.DataFrame({"ts_code": codes.to_numpy()})
    result[REASON_ILLIQUID_HISTORY] = True
    result[REASON_ILLIQUID] = False
    if bars is None or bars.empty:
        return result

    frame = bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame = frame[frame["trade_date"].notna() & frame["trade_date"].lt(asof) & frame["amount"].notna()]
    if frame.empty:
        return result

    dates = sorted(frame["trade_date"].drop_duplicates().tolist())[-config.liquidity_window :]
    frame = frame[frame["trade_date"].isin(dates)]
    stats = frame.groupby("ts_code", sort=False)["amount"].agg(obs="count", mean_amount="mean")
    eligible = stats["obs"] >= config.min_liquidity_obs
    percent = stats.loc[eligible, "mean_amount"].rank(method="min", pct=True)
    illiquid = set(percent[percent <= config.liquidity_quantile].index.astype(str))
    short_history = set(stats.index[~eligible].astype(str))
    missing = set(codes.astype(str)) - set(stats.index.astype(str))

    result[REASON_ILLIQUID_HISTORY] = result["ts_code"].isin(short_history | missing)
    result[REASON_ILLIQUID] = result["ts_code"].isin(illiquid)
    return result


def _fundamental_flags(
    asof: pd.Timestamp,
    codes: pd.Series,
    balance_sheets: pd.DataFrame | None,
    indicators: pd.DataFrame | None,
    config: CleanerConfig,
) -> pd.DataFrame:
    latest_balance = latest_visible(
        balance_sheets if balance_sheets is not None else pd.DataFrame(),
        asof,
        allowed_report_types=_CONSOLIDATED_REPORTS,
    )
    annual = annual_indicators(indicators if indicators is not None else pd.DataFrame())
    latest_annual = latest_visible(annual, asof)

    out = pd.DataFrame({"ts_code": codes.to_numpy()})
    balance = _balance_view(latest_balance)
    merged = out.merge(balance, on="ts_code", how="left", indicator=True)
    has_row = merged["_merge"].eq("both")
    assets = pd.to_numeric(merged["total_assets"], errors="coerce")
    liability = pd.to_numeric(merged["total_liab"], errors="coerce")
    equity = pd.to_numeric(merged["equity"], errors="coerce")
    goodwill = pd.to_numeric(merged["goodwill"], errors="coerce").fillna(0.0)
    usable = has_row & assets.notna() & assets.gt(0) & liability.notna()

    merged[REASON_MISSING_BALANCE] = ~usable
    merged[REASON_EQUITY] = has_row & (equity.isna() | equity.le(0))
    goodwill_ratio = goodwill.div(equity.where(equity.gt(0)))
    merged[REASON_GOODWILL] = has_row & equity.gt(0) & goodwill_ratio.gt(config.goodwill_to_equity_max)

    leverage = usable & liability.div(assets.where(assets.gt(0))).gt(config.debt_to_asset_max)
    if not config.apply_leverage_filter_to_financials:
        financial = merged["comp_type"].map(_code).isin(FINANCIAL_COMP_TYPES)
        leverage = leverage & ~financial.fillna(False)
    merged[REASON_LEVERAGE] = leverage

    profit = _profit_view(latest_annual)
    merged = merged.merge(profit, on="ts_code", how="left")
    merged[REASON_PROFIT] = pd.to_numeric(merged["profit_dedt"], errors="coerce").lt(0)
    columns = [
        "ts_code",
        REASON_MISSING_BALANCE,
        REASON_EQUITY,
        REASON_GOODWILL,
        REASON_LEVERAGE,
        REASON_PROFIT,
    ]
    return merged[columns]


def _balance_view(latest_balance: pd.DataFrame) -> pd.DataFrame:
    columns = ["ts_code", "goodwill", "equity", "total_assets", "total_liab", "comp_type"]
    if latest_balance.empty:
        return pd.DataFrame(columns=columns)
    frame = latest_balance.copy()
    if "comp_type" not in frame.columns:
        frame["comp_type"] = pd.NA
    return frame[columns].drop_duplicates("ts_code", keep="last")


def _profit_view(latest_annual: pd.DataFrame) -> pd.DataFrame:
    if latest_annual.empty or "profit_dedt" not in latest_annual.columns:
        return pd.DataFrame(columns=["ts_code", "profit_dedt"])
    return latest_annual[["ts_code", "profit_dedt"]].drop_duplicates("ts_code", keep="last")


def _reasons_for_row(row: pd.Series) -> str:
    return ",".join(reason for reason in REASON_ORDER if bool(row[reason]))
