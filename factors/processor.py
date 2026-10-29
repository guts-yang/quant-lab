"""截面预处理：先去极值，再按行业填缺失，最后做 Z-Score。"""

from __future__ import annotations

import pandas as pd

# 正态假定下，MAD 乘这个系数后与标准差同一量纲。
MAD_SCALE = 1.4826


def winsorize_mad(values: pd.Series, width: float = 3.0) -> pd.Series:
    """把超出中位数 ± width * 1.4826 * MAD 的值拉回边界。缺失值保持缺失。"""
    numeric = pd.to_numeric(values, errors="coerce")
    median = numeric.median()
    mad = (numeric - median).abs().median()
    if pd.isna(median) or pd.isna(mad) or mad == 0:
        return numeric
    bound = width * MAD_SCALE * float(mad)
    return numeric.clip(lower=median - bound, upper=median + bound)


def fill_industry_median(values: pd.Series, industry: pd.Series | None) -> pd.Series:
    """只填缺失值。没有行业，或该行业本身全缺失时，用整个候选池的中位数。"""
    numeric = pd.to_numeric(values, errors="coerce")
    universe_median = numeric.median()
    if industry is None:
        return numeric.fillna(universe_median)
    labels = pd.Series(industry, copy=False).astype("string")
    labels.index = numeric.index
    group_median = numeric.groupby(labels, dropna=True).transform("median")
    return numeric.fillna(group_median).fillna(universe_median)


def zscore(values: pd.Series) -> pd.Series:
    """总体标准差。标准差为 0 时整列记 0。整列都缺失时保持缺失。"""
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() == 0:
        return numeric
    center = numeric.mean()
    scale = numeric.std(ddof=0)
    if pd.isna(scale) or scale == 0:
        return numeric * 0
    return (numeric - center) / scale


def prepare_factors(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    prepared = frame.copy()
    industry = prepared["industry"] if "industry" in prepared.columns else None
    for column in columns:
        if column not in prepared.columns:
            continue
        clipped = winsorize_mad(prepared[column])
        filled = fill_industry_median(clipped, industry)
        prepared[column] = zscore(filled)
    return prepared
