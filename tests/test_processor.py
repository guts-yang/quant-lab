"""去极值和行业填充。不访问网络。"""

import pandas as pd
import pytest

from factors.processor import MAD_SCALE, fill_industry_median, winsorize_mad, zscore


def test_mad_pulls_the_outlier_back_to_the_bound():
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 100.0])
    clipped = winsorize_mad(values)
    assert clipped.iloc[:5].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]
    bound = 3 * MAD_SCALE * 1.5
    assert clipped.iloc[5] == pytest.approx(3.5 + bound)
    assert clipped.iloc[5] < 100


def test_industry_median_fills_only_missing_values():
    values = pd.Series([10.0, pd.NA, 4.0, 6.0, pd.NA], dtype="Float64")
    industry = pd.Series(["银行", "银行", "计算机", "计算机", pd.NA])
    filled = fill_industry_median(values, industry)
    assert filled.iloc[0] == 10
    assert filled.iloc[1] == 10
    assert filled.iloc[2] == 4
    assert filled.iloc[3] == 6
    assert filled.iloc[4] == 6


def test_zscore_uses_population_std_and_zero_scale_becomes_zero():
    scaled = zscore(pd.Series([0.0, 2.0]))
    assert scaled.tolist() == [-1.0, 1.0]
    assert zscore(pd.Series([3.0, 3.0])).tolist() == [0.0, 0.0]
