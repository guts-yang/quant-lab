"""AlphaSpread 式 DCF 估值。model 是纯函数引擎，data 负责公告日对齐，scenario 编排产物。"""

from valuation.assumptions import (
    CompanyFundamentals,
    EstimatesProvider,
    FileEstimatesProvider,
    IndustryStats,
    ValuationConfig,
    derive_base_inputs,
    derive_scenarios,
    select_template,
)
from valuation.model import DcfInputs, DcfResult, ImpliedGrowth, dcf_value, reverse_dcf, sensitivity_grid
from valuation.scenario import ValuationReport, render_report, run_valuation, write_outputs

__all__ = [
    "CompanyFundamentals",
    "DcfInputs",
    "DcfResult",
    "EstimatesProvider",
    "FileEstimatesProvider",
    "ImpliedGrowth",
    "IndustryStats",
    "ValuationConfig",
    "ValuationReport",
    "dcf_value",
    "derive_base_inputs",
    "derive_scenarios",
    "render_report",
    "reverse_dcf",
    "run_valuation",
    "select_template",
    "sensitivity_grid",
    "write_outputs",
]
