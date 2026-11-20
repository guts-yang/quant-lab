"""主估值缺输入时的待审兜底。

审核文件 `valuation/fallback_approvals.json` 不存在，或没有对应条目时，兜底不进入估值。
格式::

    {"approvals": [{"ts_code": "000034.SZ", "field": "growth_start", "value": 0.093}]}

`value` 是用户批准后才使用的数。建议值 `proposed` 只能来自该公司另一条财报口径；没有就为 null。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

STATUS_PENDING = "pending_review"
STATUS_APPROVED = "approved"

DEFAULT_APPROVALS_PATH = Path(__file__).resolve().parent / "fallback_approvals.json"
APPROVALS_DISPLAY = "valuation/fallback_approvals.json"

RATE_FIELDS = {
    "growth_start",
    "margin_start",
    "margin_terminal",
    "cash_conversion",
    "stable_roe",
    "stable_cash_conversion",
    "bear_growth_start",
    "bull_growth_start",
    "bear_margin_terminal",
    "bull_margin_terminal",
}

FIELD_LABELS = {
    "growth_start": "起始增速",
    "margin_start": "起始净利率",
    "margin_terminal": "终值净利率",
    "cash_conversion": "高成长期现金转换率",
    "stable_roe": "稳定期 ROE",
    "stable_cash_conversion": "稳定期现金转换率",
    "revenue_base": "TTM 收入",
    "shares": "总股本",
    "bear_growth_start": "熊市起始增速",
    "bull_growth_start": "牛市起始增速",
    "bear_margin_terminal": "熊市终值净利率",
    "bull_margin_terminal": "牛市终值净利率",
}


@dataclass(frozen=True)
class FallbackItem:
    field: str
    reason: str
    proposed: float | None
    status: str = STATUS_PENDING
    approved_value: float | None = None
    computed: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "reason": self.reason,
            "proposed": self.proposed,
            "status": self.status,
            "approved_value": self.approved_value,
            "computed": self.computed,
        }


def load_approvals(path: str | Path | None = None) -> dict[tuple[str, str], float]:
    """返回 {(代码, 字段): 批准值}。文件缺失或格式不对时当作没有批准。"""
    target = Path(path) if path is not None else DEFAULT_APPROVALS_PATH
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("approvals") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return {}
    found: dict[tuple[str, str], float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("ts_code") or "").strip()
        field = str(entry.get("field") or "").strip()
        value = entry.get("value")
        if not code or not field or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        found[(code, field)] = float(value)
    return found


def lookup_approval(approvals: dict[tuple[str, str], float], ts_code: str, field: str) -> float | None:
    return approvals.get((str(ts_code), field))


def resolve_input(
    field: str,
    computed: float | None,
    *,
    reason: str,
    proposed: float | None,
    ts_code: str,
    approvals: dict[tuple[str, str], float],
    block_computed: bool = False,
) -> tuple[float | None, FallbackItem | None]:
    """模型能用的数直接返回。不能用时，只有审核文件里的批准值可以进入估值。"""
    usable = computed is not None and not block_computed
    if usable:
        return float(computed), None
    approved = lookup_approval(approvals, ts_code, field)
    if approved is not None:
        return approved, FallbackItem(
            field=field,
            reason=reason,
            proposed=proposed,
            status=STATUS_APPROVED,
            approved_value=approved,
            computed=None if computed is None else float(computed),
        )
    return None, FallbackItem(
        field=field,
        reason=reason,
        proposed=proposed,
        status=STATUS_PENDING,
        computed=None if computed is None else float(computed),
    )


def approvals_display(path: str | Path | None) -> str:
    if path is None:
        return APPROVALS_DISPLAY
    return Path(path).as_posix()


def format_quantity(field: str, value: float | None) -> str:
    if value is None:
        return "无"
    if field == "revenue_base":
        return f"{value / 1e8:.1f} 亿"
    if field == "shares":
        return f"{value / 1e8:.2f} 亿股"
    return f"{value:.1%}"


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field)
