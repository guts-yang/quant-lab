"""股票池排雷。合成数据，不访问网络。"""

import pandas as pd

from core.cleaner import CleanerConfig, UniverseCleaner, full_day_halt_codes

ASOF = "2024-12-31"


def _instrument(ts_code: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_code": ts_code,
        "name": "正常公司",
        "list_date": "2000-01-01",
        "delist_date": pd.NaT,
        "is_suspended": False,
    }
    row.update(overrides)
    return row


def _balance(
    ts_code: str,
    *,
    goodwill: float | None = 0.0,
    equity: float | None = 100.0,
    assets: float | None = 100.0,
    liability: float | None = 20.0,
    ann: str = "2024-10-30",
    end: str = "2024-09-30",
    report_type: str = "1",
    comp_type: str = "1",
) -> dict[str, object]:
    return {
        "ts_code": ts_code,
        "ann_date": ann,
        "end_date": end,
        "goodwill": goodwill,
        "equity": equity,
        "total_assets": assets,
        "total_liab": liability,
        "report_type": report_type,
        "comp_type": comp_type,
    }


def _annual(ts_code: str, profit: float = 10.0, ann: str = "2024-04-20", end: str = "2023-12-31") -> dict[str, object]:
    return {"ts_code": ts_code, "ann_date": ann, "end_date": end, "profit_dedt": profit}


def _bars(amounts: dict[str, float], *, sessions: int = 20, short: dict[str, int] | None = None) -> pd.DataFrame:
    asof = pd.Timestamp(ASOF)
    dates = [asof - pd.Timedelta(days=offset) for offset in range(sessions, 0, -1)]
    short = short or {}
    rows: list[dict[str, object]] = []
    for code, amount in amounts.items():
        used = dates[-short[code] :] if code in short else dates
        for trade_date in used:
            rows.append({"ts_code": code, "trade_date": trade_date, "amount": amount})
    return pd.DataFrame(rows)


def _run(
    instruments: list[dict[str, object]],
    balances: list[dict[str, object]],
    indicators: list[dict[str, object]] | None = None,
    amounts: dict[str, float] | None = None,
    config: CleanerConfig | None = None,
    short: dict[str, int] | None = None,
):
    if indicators is None:
        indicators = [_annual(row["ts_code"]) for row in instruments]
    if amounts is None:
        amounts = {str(row["ts_code"]): 1_000_000.0 for row in instruments}
    return UniverseCleaner(config).filter(
        ASOF,
        pd.DataFrame(instruments),
        _bars(amounts, short=short),
        pd.DataFrame(balances),
        pd.DataFrame(indicators),
    )


def _reasons(result, ts_code: str) -> str:
    matched = result.rejected.loc[result.rejected["ts_code"].eq(ts_code), "reasons"]
    if matched.empty:
        return ""
    return str(matched.iloc[0])


def test_listing_age_uses_calendar_days():
    asof = pd.Timestamp(ASOF)
    result = _run(
        [
            _instrument("000001.SZ", list_date=asof - pd.Timedelta(days=179)),
            _instrument("000002.SZ", list_date=asof - pd.Timedelta(days=180)),
            _instrument("000003.SZ", list_date=pd.NaT),
        ],
        [_balance("000001.SZ"), _balance("000002.SZ"), _balance("000003.SZ")],
    )
    assert _reasons(result, "000001.SZ") == "listed_lt_180"
    assert _reasons(result, "000003.SZ") == "listed_lt_180"
    assert "000002.SZ" in set(result.kept["ts_code"])


def test_delisted_suspended_st_and_retiring_names():
    result = _run(
        [
            _instrument("000001.SZ", delist_date=ASOF),
            _instrument("000002.SZ", is_suspended=True),
            _instrument("000003.SZ", name="*ST平安"),
            _instrument("000004.SZ", name="退市海润"),
            _instrument("000005.SZ", name="平安银行"),
        ],
        [_balance(code) for code in ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"]],
    )
    assert _reasons(result, "000001.SZ") == "delisted"
    assert _reasons(result, "000002.SZ") == "suspended"
    assert _reasons(result, "000003.SZ") == "st"
    assert _reasons(result, "000004.SZ") == "delisting"
    assert "000005.SZ" in set(result.kept["ts_code"])


def test_intraday_suspension_is_not_a_full_day_halt():
    suspensions = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "suspend_type": ["S", "S"],
            "suspend_timing": [None, "09:30-10:00"],
        }
    )
    assert full_day_halt_codes(suspensions) == {"000001.SZ"}


def test_liquidity_percentile_includes_names_that_fail_other_rules():
    instruments = []
    balances = []
    amounts = {}
    for index in range(1, 21):
        code = f"{index:06d}.SZ"
        name = f"ST测试{index}" if index <= 10 else f"正常{index}"
        instruments.append(_instrument(code, name=name))
        balances.append(_balance(code))
        amounts[code] = float(index)
    result = _run(instruments, balances, amounts=amounts)

    assert "000011.SZ" in set(result.kept["ts_code"])
    assert _reasons(result, "000001.SZ") == "st,illiquid"
    assert _reasons(result, "000002.SZ") == "st,illiquid"
    assert _reasons(result, "000003.SZ") == "st"
    assert "illiquid" not in _reasons(result, "000011.SZ")


def test_short_trading_history_is_separate_from_the_bottom_decile():
    result = _run(
        [_instrument("000001.SZ"), _instrument("000002.SZ")],
        [_balance("000001.SZ"), _balance("000002.SZ")],
        amounts={"000001.SZ": 100.0, "000002.SZ": 50.0},
        short={"000002.SZ": 9},
    )
    assert "000001.SZ" in set(result.kept["ts_code"])
    assert _reasons(result, "000002.SZ") == "illiquid_history"


def test_goodwill_leverage_and_negative_annual_profit():
    result = _run(
        [
            _instrument("000001.SZ"),
            _instrument("000002.SZ"),
            _instrument("000003.SZ"),
            _instrument("000004.SZ"),
            _instrument("000005.SZ"),
        ],
        [
            _balance("000001.SZ", goodwill=31),
            _balance("000002.SZ", goodwill=30),
            _balance("000003.SZ", goodwill=None),
            _balance("000004.SZ", liability=81),
            _balance("000005.SZ", liability=80),
        ],
        [
            _annual("000001.SZ", profit=1),
            _annual("000002.SZ", profit=1),
            _annual("000003.SZ", profit=1),
            _annual("000004.SZ", profit=-1),
            _annual("000005.SZ", profit=0),
        ],
    )
    assert _reasons(result, "000001.SZ") == "goodwill"
    assert "000002.SZ" in set(result.kept["ts_code"])
    assert "000003.SZ" in set(result.kept["ts_code"])
    assert _reasons(result, "000004.SZ") == "leverage,negative_deducted_profit"
    assert "000005.SZ" in set(result.kept["ts_code"])


def test_missing_annual_report_does_not_reject():
    result = _run(
        [_instrument("000001.SZ")],
        [_balance("000001.SZ")],
        indicators=[_annual("000001.SZ", profit=-5, ann="2024-10-30", end="2024-09-30")],
    )
    assert "000001.SZ" in set(result.kept["ts_code"])


def test_missing_balance_sheet_and_non_positive_equity():
    result = _run(
        [_instrument("000001.SZ"), _instrument("000002.SZ"), _instrument("000003.SZ")],
        [
            _balance("000002.SZ", equity=0),
            _balance("000003.SZ", assets=None, liability=None),
        ],
    )
    assert _reasons(result, "000001.SZ") == "missing_balance_sheet"
    assert _reasons(result, "000002.SZ") == "non_positive_equity"
    assert "missing_balance_sheet" in _reasons(result, "000003.SZ")


def test_unannounced_statements_cannot_change_the_filter():
    result = _run(
        [_instrument("000001.SZ")],
        [
            _balance("000001.SZ", goodwill=0, ann="2024-08-20", end="2024-06-30"),
            _balance("000001.SZ", goodwill=80, ann="2025-04-30", end="2024-12-31"),
        ],
        indicators=[
            _annual("000001.SZ", profit=5, ann="2023-04-20", end="2022-12-31"),
            _annual("000001.SZ", profit=-100, ann="2025-04-30", end="2024-12-31"),
        ],
    )
    assert "000001.SZ" in set(result.kept["ts_code"])


def test_parent_only_report_does_not_count_as_a_balance_sheet():
    result = _run(
        [_instrument("000001.SZ")],
        [_balance("000001.SZ", report_type="6")],
    )
    assert _reasons(result, "000001.SZ") == "missing_balance_sheet"


def test_multiple_reasons_are_all_retained():
    result = _run(
        [_instrument("000001.SZ", name="*ST退市")],
        [_balance("000001.SZ", goodwill=50)],
        indicators=[_annual("000001.SZ", profit=-3)],
    )
    assert _reasons(result, "000001.SZ") == "st,delisting,goodwill,negative_deducted_profit"


def test_financial_leverage_can_be_turned_off():
    instruments = [_instrument("000001.SZ")]
    balances = [_balance("000001.SZ", assets=100, liability=92, equity=8, comp_type="2")]
    blocked = _run(instruments, balances)
    allowed = _run(
        instruments,
        balances,
        config=CleanerConfig(apply_leverage_filter_to_financials=False),
    )
    assert _reasons(blocked, "000001.SZ") == "leverage"
    assert "000001.SZ" in set(allowed.kept["ts_code"])


def test_empty_universe():
    result = UniverseCleaner().filter(ASOF, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    assert result.kept.empty
    assert result.rejected.empty
