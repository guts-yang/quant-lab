"""已知净值上的回撤和卡玛。"""

import pandas as pd
import pytest

from core.metrics import calmar, max_drawdown, performance


def test_twenty_percent_drop_is_the_max_drawdown():
    equity = pd.Series([100.0, 80.0, 90.0])
    assert max_drawdown(equity) == pytest.approx(-0.2)
    assert calmar(0.15, -0.2) == pytest.approx(0.75)


def test_performance_reports_the_same_drawdown():
    curve = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-01-02", "2024-06-01", "2024-12-31"]),
            "equity": [100.0, 80.0, 90.0],
        }
    )
    stats = performance(curve)
    assert stats["max_drawdown"] == pytest.approx(-0.2)
    assert stats["calmar"] == pytest.approx(stats["cagr"] / 0.2)
