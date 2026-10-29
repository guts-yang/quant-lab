"""公告日对齐与曾用名区间。不访问网络。"""

import pandas as pd

from core.pit import latest_visible, resolve_names, visible_history


def test_report_announced_after_asof_is_invisible():
    reports = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "ann_date": ["2024-08-20", "2024-10-30"],
            "end_date": ["2024-06-30", "2024-09-30"],
            "equity": [100.0, 10.0],
        }
    )
    visible = latest_visible(reports, "2024-10-01")
    assert list(visible["equity"]) == [100.0]


def test_restatement_stays_hidden_until_its_own_announcement():
    reports = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "ann_date": ["2024-04-20", "2024-04-20"],
            "f_ann_date": ["2024-04-20", "2024-08-20"],
            "end_date": ["2023-12-31", "2023-12-31"],
            "profit_dedt": [100.0, 50.0],
            "update_flag": ["0", "1"],
        }
    )
    before = latest_visible(reports, "2024-06-01")
    after = latest_visible(reports, "2024-09-01")
    assert list(before["profit_dedt"]) == [100.0]
    assert list(after["profit_dedt"]) == [50.0]


def test_actual_announcement_overrides_an_earlier_ann_date():
    reports = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "ann_date": ["2024-04-20"],
            "f_ann_date": ["2024-08-20"],
            "end_date": ["2023-12-31"],
            "equity": [1.0],
        }
    )
    assert latest_visible(reports, "2024-06-01").empty


def test_announcement_before_period_end_is_dropped():
    reports = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "ann_date": ["2024-03-01", "2024-03-31"],
            "end_date": ["2024-03-31", "2024-03-31"],
            "equity": [1.0, 2.0],
        }
    )
    visible = latest_visible(reports, "2024-05-01")
    assert list(visible["equity"]) == [2.0]


def test_only_consolidated_and_pre_adjustment_reports_are_kept():
    reports = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
            "ann_date": ["2024-04-20", "2024-04-20", "2024-04-20"],
            "end_date": ["2023-12-31", "2023-12-31", "2023-12-31"],
            "report_type": ["4", "6", 1],
            "equity": [9.0, 8.0, 3.0],
        }
    )
    visible = latest_visible(reports, "2024-05-01", allowed_report_types={"1", "5"})
    assert list(visible["equity"]) == [3.0]


def test_pre_adjustment_report_is_used_before_the_restatement():
    reports = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "ann_date": ["2024-04-20", "2024-08-20"],
            "end_date": ["2023-12-31", "2023-12-31"],
            "report_type": ["5", "1"],
            "equity": [10.0, 4.0],
        }
    )
    visible = latest_visible(reports, "2024-05-01", allowed_report_types={"1", "5"})
    assert list(visible["equity"]) == [10.0]


def test_newer_period_replaces_older_period():
    reports = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "ann_date": ["2024-04-20", "2024-08-20"],
            "end_date": ["2023-12-31", "2024-06-30"],
            "equity": [1.0, 2.0],
        }
    )
    visible = latest_visible(reports, "2024-09-01")
    assert visible.iloc[0]["end_date"] == pd.Timestamp("2024-06-30")
    assert list(visible["equity"]) == [2.0]


def test_resolve_names_uses_inclusive_intervals():
    history = pd.DataFrame(
        {
            "ts_code": ["600848.SH", "600848.SH"],
            "name": ["ST自仪", "自仪股份"],
            "start_date": ["20061026", "20070514"],
            "end_date": ["20070513", pd.NA],
        }
    )
    assert resolve_names(history, "20070513").iloc[0]["name"] == "ST自仪"
    assert resolve_names(history, "20070514").iloc[0]["name"] == "自仪股份"


def test_visible_history_keeps_older_quarters():
    reports = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
            "ann_date": ["2024-04-20", "2024-08-20", "2024-10-30"],
            "end_date": ["2023-12-31", "2024-06-30", "2024-09-30"],
            "q_dtprofit": [10.0, 12.0, 99.0],
        }
    )
    history = visible_history(reports, "2024-09-01")
    assert set(history["end_date"]) == {pd.Timestamp("2023-12-31"), pd.Timestamp("2024-06-30")}
    assert list(latest_visible(reports, "2024-09-01")["q_dtprofit"]) == [12.0]


def test_resolve_names_empty_history():
    assert resolve_names(pd.DataFrame(), "2024-12-31").empty
