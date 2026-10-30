"""研究规格、冻结快照和因子诊断均使用合成数据。"""

import pandas as pd
import pytest

from research.config import CostScenario, make_config
from research.diagnostics import forward_labels, ic_summary, quantile_returns, rank_ic
from research.report import decision_gate
from research.snapshots import freeze_snapshots, load_snapshots, month_end_dates
from research.stress import default_scenarios


def test_config_requires_strictly_ordered_periods():
    with pytest.raises(ValueError, match="严格顺序"):
        make_config("20240101", "20240131", "20240131", "20240229")
    config = make_config("20240101", "20240131", "20240229", "20240331")
    assert config.scenario.top_n == 8
    with pytest.raises(ValueError, match="exit_rank"):
        CostScenario("bad", top_n=8, exit_rank=7)


def test_forward_label_waits_until_after_the_signal_date():
    dates = pd.bdate_range("2024-01-31", periods=4)
    bars = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 4 + ["000002.SZ"] * 4,
            "trade_date": list(dates) * 2,
            "close": [10, 11, 12, 13, 10, 9, 8, 7],
        }
    )
    snapshots = {
        dates[0]: pd.DataFrame(
            {"ts_code": ["000001.SZ", "000002.SZ"], "composite": [0.9, 0.1], "ep_ttm": [0.8, 0.2]}
        )
    }
    labels = forward_labels(snapshots, bars, holding_sessions=1)
    assert set(labels["entry_date"]) == {dates[1]}
    assert set(labels["exit_date"]) == {dates[2]}
    assert labels.loc[labels["ts_code"].eq("000001.SZ"), "forward_return"].iloc[0] == pytest.approx(12 / 11 - 1)
    ic = rank_ic(labels)
    assert ic.loc[ic["factor"].eq("composite"), "rank_ic"].iloc[0] == pytest.approx(1.0)
    assert ic_summary(ic).loc[lambda frame: frame["factor"].eq("composite"), "positive_month_ratio"].iloc[0] == 1.0
    assert set(quantile_returns(labels, quantiles=2)["quantile"]) == {1, 2}


class _Provider:
    def get_ohlc(self, start, end):
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": pd.to_datetime(["2024-01-31", "2024-02-29"]),
                "open": [10.0, 10.0],
                "high": [10.0, 10.0],
                "low": [10.0, 10.0],
                "close": [10.0, 10.0],
            }
        )


def test_freeze_snapshots_writes_complete_month_end_index(tmp_path):
    config = make_config("20240101", "20240131", "20240215", "20240331")

    def builder(provider, asof, top_n):
        scores = pd.DataFrame({"ts_code": ["000001.SZ"], "composite": [1.0]})
        return scores, scores, pd.DataFrame(columns=["ts_code", "reasons"]), {"top_n": top_n}

    index = freeze_snapshots(_Provider(), config, tmp_path, builder=builder)
    assert list(index["asof"]) == [pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29")]
    assert set(load_snapshots(tmp_path)) == {pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29")}
    assert month_end_dates(_Provider().get_ohlc(None, None), "20240101", "20240229")[-1] == pd.Timestamp("2024-02-29")


def test_decision_gate_preserves_evidence_insufficient_for_short_holdout():
    config = make_config("20240101", "20240131", "20240215", "20240331")
    walkforward = pd.DataFrame(
        {
            "period": ["holdout"],
            "strategy_calmar": [2.0],
            "strategy_max_drawdown": [-0.1],
        }
    )
    stress = pd.DataFrame({"scenario": ["baseline"], "estimated_cost_to_average_equity": [0.01]})
    decision, reasons = decision_gate(config, walkforward, stress)
    assert decision == "evidence_insufficient"
    assert "个月" in reasons[0]


def test_stress_scenarios_include_cost_and_buffer_cases():
    base = CostScenario("baseline")
    names = {scenario.name for scenario in default_scenarios(base)}
    assert {"high_slippage", "double_min_commission", "capital_20000", "top_5", "top_8_no_buffer"} <= names
