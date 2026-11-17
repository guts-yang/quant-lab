"""估值页图表用的数据框。只整理已发布的表，不重新估值。"""

from __future__ import annotations

import pandas as pd

SCENARIO_ORDER = ("bear", "base", "bull")
SCENARIO_LABELS = {"bear": "熊市", "base": "基准", "bull": "牛市"}


def security_label(name: str | None, code: str) -> str:
    """公司名称在前，代码放在括号里。没有名称时只留代码。"""
    company = str(name or "").strip()
    if company in {"", "None", "nan", "<NA>"}:
        return str(code)
    return f"{company}（{code}）"


def value_frame(scenarios: pd.DataFrame | None) -> pd.DataFrame:
    """熊、基准、牛的每股价值，顺序固定。"""
    columns = ["情景", "每股价值"]
    if scenarios is None or scenarios.empty or "scenario" not in scenarios.columns or "per_share" not in scenarios.columns:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for name in SCENARIO_ORDER:
        matched = scenarios.loc[scenarios["scenario"].astype(str).eq(name)]
        if matched.empty:
            continue
        value = _number(matched["per_share"].iloc[0])
        if value is None:
            continue
        rows.append({"情景": SCENARIO_LABELS[name], "每股价值": value})
    return pd.DataFrame(rows, columns=columns)


def quoted_price(assumptions: dict | None) -> float | None:
    fundamentals = (assumptions or {}).get("fundamentals") or {}
    return _number(fundamentals.get("price"))


def present_value_frame(scenarios: pd.DataFrame | None) -> pd.DataFrame:
    """基准情景的显式期现值与终值现值，单位是亿元。"""
    columns = ["构成", "现值"]
    if scenarios is None or scenarios.empty or "scenario" not in scenarios.columns:
        return pd.DataFrame(columns=columns)
    matched = scenarios.loc[scenarios["scenario"].astype(str).eq("base")]
    if matched.empty:
        return pd.DataFrame(columns=columns)
    row = matched.iloc[0]
    explicit = _number(row.get("pv_explicit"))
    terminal = _number(row.get("pv_terminal"))
    if explicit is None or terminal is None:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        [
            {"构成": "显式期", "现值": explicit / 1e8},
            {"构成": "终值", "现值": terminal / 1e8},
        ],
        columns=columns,
    )


def revenue_frame(forecast: pd.DataFrame | None) -> pd.DataFrame:
    """三情景收入路径，单位是亿元。没有 scenario 列时全部记为基准。"""
    columns = ["年份", "情景", "收入"]
    if forecast is None or forecast.empty or "revenue" not in forecast.columns or "year" not in forecast.columns:
        return pd.DataFrame(columns=columns)
    frame = forecast.copy()
    if "scenario" not in frame.columns:
        frame["scenario"] = "base"
    frame["情景"] = frame["scenario"].astype(str).map(SCENARIO_LABELS)
    frame = frame.dropna(subset=["情景"])
    frame["年份"] = pd.to_numeric(frame["year"], errors="coerce")
    frame["收入"] = pd.to_numeric(frame["revenue"], errors="coerce") / 1e8
    frame = frame.dropna(subset=["年份", "收入"])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    order = {label: index for index, label in enumerate(SCENARIO_LABELS.values())}
    frame = frame.assign(_order=frame["情景"].map(order)).sort_values(["_order", "年份"], kind="mergesort")
    return frame[columns].reset_index(drop=True)


def margin_frame(forecast: pd.DataFrame | None) -> pd.DataFrame:
    """基准情景净利率，仍是比率。"""
    columns = ["年份", "净利率"]
    base = _base_forecast(forecast)
    if base.empty or "margin" not in base.columns:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(
        {
            "年份": pd.to_numeric(base["year"], errors="coerce"),
            "净利率": pd.to_numeric(base["margin"], errors="coerce"),
        }
    ).dropna()
    return frame.sort_values("年份", kind="mergesort").reset_index(drop=True)


def fcfe_frame(forecast: pd.DataFrame | None) -> pd.DataFrame:
    """基准情景股权现金流，单位是亿元。"""
    columns = ["年份", "股权现金流"]
    base = _base_forecast(forecast)
    if base.empty or "fcfe" not in base.columns:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(
        {
            "年份": pd.to_numeric(base["year"], errors="coerce"),
            "股权现金流": pd.to_numeric(base["fcfe"], errors="coerce") / 1e8,
        }
    ).dropna()
    return frame.sort_values("年份", kind="mergesort").reset_index(drop=True)


def growth_anchor_frame(assumptions: dict | None) -> pd.DataFrame:
    """历史、基准、现价隐含三种收入增速。缺哪一项就少一根柱。"""
    columns = ["口径", "增速"]
    payload = assumptions or {}
    rows: list[dict[str, object]] = []
    history = _number(((payload.get("fundamentals") or {}).get("revenue_cagr_5y")))
    if history is not None:
        rows.append({"口径": "5年历史", "增速": history})
    base = _number(((payload.get("scenarios") or {}).get("base") or {}).get("average_growth"))
    if base is not None:
        rows.append({"口径": "基准情景", "增速": base})
    implied = payload.get("implied_growth") or {}
    implied_growth = _number(implied.get("average_growth")) if isinstance(implied, dict) else None
    if implied_growth is not None:
        rows.append({"口径": "现价隐含", "增速": implied_growth})
    return pd.DataFrame(rows, columns=columns)


def sensitivity_frame(sensitivity: pd.DataFrame | None) -> pd.DataFrame:
    """折现率 × 起始增速的长表。标签按数值排序，避免百分号字符串的字典序。"""
    columns = ["折现率", "起始增速", "每股价值", "折现率标签", "增速标签"]
    if sensitivity is None or sensitivity.empty:
        return pd.DataFrame(columns=columns)
    grid = sensitivity.copy()
    grid.index = pd.to_numeric(grid.index, errors="coerce")
    grid.columns = pd.to_numeric(grid.columns, errors="coerce")
    long = grid.stack(future_stack=True).rename("每股价值").reset_index()
    long.columns = ["折现率", "起始增速", "每股价值"]
    long["折现率"] = pd.to_numeric(long["折现率"], errors="coerce")
    long["起始增速"] = pd.to_numeric(long["起始增速"], errors="coerce")
    long["每股价值"] = pd.to_numeric(long["每股价值"], errors="coerce")
    long = long.dropna()
    if long.empty:
        return pd.DataFrame(columns=columns)
    long["折现率标签"] = long["折现率"].map(lambda value: f"{value:.1%}")
    long["增速标签"] = long["起始增速"].map(lambda value: f"{value:.1%}")
    return long[columns].reset_index(drop=True)


HISTORY_WINDOWS = {"近1年": 1, "近3年": 3}


def history_window(history: pd.DataFrame | None, asof: object, years: int) -> pd.DataFrame:
    """截到截面日为止的最近若干年。premium = (收盘价 − 内在价值) / 收盘价。"""
    columns = ["trade_date", "close", "intrinsic", "premium"]
    if history is None or history.empty or "trade_date" not in history.columns:
        return pd.DataFrame(columns=columns)
    frame = history.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["close"] = pd.to_numeric(frame.get("close"), errors="coerce")
    frame["intrinsic"] = pd.to_numeric(frame.get("intrinsic"), errors="coerce") if "intrinsic" in frame.columns else pd.NA
    frame = frame.dropna(subset=["trade_date", "close"])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    end = pd.Timestamp(asof).normalize() if asof else frame["trade_date"].max()
    start = end - pd.DateOffset(years=int(years))
    frame = frame.loc[frame["trade_date"].between(start, end)]
    frame = frame.sort_values("trade_date", kind="mergesort").drop_duplicates("trade_date", keep="last")
    close = frame["close"]
    intrinsic = frame["intrinsic"]
    frame["premium"] = (close - intrinsic) / close.where(close > 0)
    return frame[columns].reset_index(drop=True)


def history_position(history: pd.DataFrame | None, asof: object) -> dict[str, float] | None:
    """过去 3 年里，高估幅度大于或等于今天的交易日占比。缺内在价值的日期不计。"""
    window = history_window(history, asof, HISTORY_WINDOWS["近3年"])
    usable = window.dropna(subset=["premium"])
    if usable.empty:
        return None
    latest = usable.iloc[-1]
    share = float((usable["premium"] >= float(latest["premium"])).mean())
    return {
        "close": float(latest["close"]),
        "intrinsic": float(latest["intrinsic"]),
        "premium": float(latest["premium"]),
        "expensive_share": share,
    }


def history_headline(name: str, position: dict[str, float] | None) -> str | None:
    """三年位置的一句话。占比不随页面上的 1 年 / 3 年切换改变。"""
    if not position:
        return None
    share = position["expensive_share"]
    pct = f"{share:.0%}"
    if share < 0.5:
        return f"{name}比往常更贵。过去 3 年里，只有约 {pct} 的交易日达到或超过今天的高估幅度。"
    if share > 0.5:
        return f"{name}比往常更便宜。过去 3 年里，约 {pct} 的交易日达到或超过今天的高估幅度。"
    return f"{name}的估值处在过去 3 年的中间。约 {pct} 的交易日达到或超过今天的高估幅度。"


def gap_label(premium: float) -> str:
    """徽章文字。正数是现价里高出内在价值的部分。"""
    if abs(premium) < 0.005:
        return "与内在价值接近"
    if premium > 0:
        return f"{premium:.0%} 高估"
    return f"{abs(premium):.0%} 低估"


def price_value_lines(window: pd.DataFrame | None) -> pd.DataFrame:
    """股价实线和内在价值虚线共用的长表。"""
    columns = ["trade_date", "序列", "价格"]
    if window is None or window.empty:
        return pd.DataFrame(columns=columns)
    price = window.loc[window["close"].notna(), ["trade_date", "close"]].rename(columns={"close": "价格"})
    price["序列"] = "股价"
    intrinsic = window.loc[window["intrinsic"].notna(), ["trade_date", "intrinsic"]].rename(columns={"intrinsic": "价格"})
    intrinsic["序列"] = "内在价值"
    lines = pd.concat([price, intrinsic], ignore_index=True)
    return lines[columns]


def valuation_ranking(runs) -> pd.DataFrame:
    """把所有估值按基准情景相对现价的上行空间排序，最被低估的排最前。

    估值机会 = 基准每股价值 / 现价 − 1。现价或基准缺失的股票不进排名。
    """
    columns = ["公司", "代码", "截面日", "现价", "熊市", "基准", "牛市", "估值机会"]
    rows: list[dict[str, object]] = []
    for run in runs or []:
        assumptions = getattr(run, "assumptions", None) or {}
        fundamentals = assumptions.get("fundamentals") or {}
        price = _number(fundamentals.get("price"))
        name = str(assumptions.get("name") or "").strip()
        if name in {"", "None", "nan", "<NA>"}:
            name = str(getattr(run, "code", "") or "")
        scenarios = getattr(run, "scenarios", None)
        per_share: dict[str, float | None] = {}
        if scenarios is not None and not scenarios.empty and "scenario" in scenarios.columns:
            for label in ("bear", "base", "bull"):
                matched = scenarios.loc[scenarios["scenario"].astype(str).eq(label)]
                per_share[label] = _number(matched["per_share"].iloc[0]) if not matched.empty else None
        else:
            per_share = {"bear": None, "base": None, "bull": None}
        base = per_share.get("base")
        if price is None or price <= 0 or base is None:
            continue
        rows.append(
            {
                "公司": name,
                "代码": str(getattr(run, "code", "") or ""),
                "截面日": str(getattr(run, "asof", "") or ""),
                "现价": price,
                "熊市": per_share.get("bear"),
                "基准": base,
                "牛市": per_share.get("bull"),
                "估值机会": base / price - 1.0,
            }
        )
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    frame = frame.sort_values("估值机会", ascending=False, kind="stable").reset_index(drop=True)
    frame.insert(0, "排名", range(1, len(frame) + 1))
    return frame


def _base_forecast(forecast: pd.DataFrame | None) -> pd.DataFrame:
    if forecast is None or forecast.empty or "year" not in forecast.columns:
        return pd.DataFrame()
    if "scenario" not in forecast.columns:
        return forecast
    return forecast.loc[forecast["scenario"].astype(str).eq("base")]


def _number(value: object) -> float | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


# 只用来说明已经写进 assumptions 的折现率和转换率，页面不重算估值。
_DISCOUNT_FLOOR = 0.06
_DISCOUNT_CAP = 0.15
_CONVERSION_FLOOR = 0.5
_CONVERSION_CAP = 1.2


def calculation_process(
    assumptions: dict | None,
    forecast: pd.DataFrame | None = None,
    scenarios: pd.DataFrame | None = None,
) -> str:
    """基准情景的计算过程。数字来自已发布的假设、预测和情景表，缺数写「缺失」。"""
    payload = assumptions if isinstance(assumptions, dict) else {}
    fundamentals = _mapping(payload.get("fundamentals"))
    discount = _mapping(payload.get("discount"))
    implied = payload.get("implied_growth")
    implied = implied if isinstance(implied, dict) else {}
    template = str(payload.get("template") or "").strip()
    if not isinstance(forecast, pd.DataFrame):
        forecast = None
    if not isinstance(scenarios, pd.DataFrame):
        scenarios = None

    base = _published_scenario(payload, scenarios, "base")
    discount_rate = _number(base.get("discount_rate"))
    if discount_rate is None:
        discount_rate = _number(discount.get("cost_of_equity"))
    records = _year_records(forecast, discount_rate)
    name = _company_name(payload, fundamentals)
    code = str(payload.get("ts_code") or "").strip()
    sections = [
        _calc_intro(name, code, payload.get("asof")),
        _calc_now(fundamentals),
        _calc_revenue(base, records),
        _calc_margin_cash(template, fundamentals, base, records),
        _calc_discount(discount, base, records),
        _calc_terminal(template, base, records),
        _calc_per_share(fundamentals, base, records),
        _calc_aside(fundamentals, implied, _published_scenario(payload, scenarios, "bear"), _published_scenario(payload, scenarios, "bull")),
    ]
    return "\n\n".join(section for section in sections if section)


def _calc_intro(name: str, code: str, asof: object) -> str:
    who = security_label(name or None, code) if code else (name or "这家公司")
    when = _fmt_asof(asof)
    dated = f"在 {when} " if when else ""
    return (
        f"下面用{who}{dated}的已发布基准情景，把计算从现在的盈利写到每股价值。"
        "金额四舍五入到亿元或元。分年收入、利润和现金流来自已发布的预测，"
        "股权价值和每股价值来自已发布的情景表。"
    )


def _calc_now(fundamentals: dict) -> str:
    rows = [
        ["TTM 收入", _fmt_yi(fundamentals.get("revenue_ttm"))],
        ["TTM 归母净利", _fmt_yi(fundamentals.get("net_income_ttm"))],
        ["TTM 净利率", _fmt_pct(fundamentals.get("margin_ttm"))],
        ["TTM 自由现金流", _fmt_yi(fundamentals.get("fcf_ttm"))],
        ["自由现金流转换率", _fmt_pct(fundamentals.get("fcf_conversion"))],
        ["现价", _fmt_yuan(fundamentals.get("price"))],
        ["总股本", _fmt_shares(fundamentals.get("shares"))],
    ]
    return "\n\n".join(["**1. 现在赚多少**", _md_table(["项目", "数值"], rows)])


def _calc_revenue(base: dict, records: list[dict]) -> str:
    lines = ["**2. 未来各年收入与增速**"]
    lines.append(
        "基准情景的收入从最近十二个月出发，第 1 年用起始增速，最后一年收到终值增速，中间按年线性过渡。"
    )
    lines.append(r"$$g_t=g_1+\frac{t-1}{N-1}(g_N-g_1),\qquad Rev_t=Rev_{t-1}(1+g_t)$$")
    if not records:
        lines.append("分年收入缺失。")
        return "\n\n".join(lines)
    start = _number(base.get("growth_start"))
    if start is None:
        start = records[0]["growth"]
    terminal = _number(base.get("growth_terminal"))
    if terminal is None:
        terminal = records[-1]["growth"]
    average = _number(base.get("average_growth"))
    growths = [row["growth"] for row in records if row["growth"] is not None]
    if average is None and growths:
        average = sum(growths) / len(growths)
    last_year = _fmt_year(records[-1]["year"])
    bits = []
    if start is not None:
        bits.append(f"第 1 年增速 {_fmt_pct(start)}")
    if terminal is not None:
        bits.append(f"第 {last_year} 年增速 {_fmt_pct(terminal)}")
    if bits:
        lines.append("，".join(bits) + "。")
    if average is not None:
        lines.append(f"平均增速 {_fmt_pct(average)} 是这 {len(records)} 个年度增速的算术平均。")
    rows = [
        [_fmt_year(row["year"]), _fmt_pct(row["growth"]), _fmt_yi(row["revenue"])]
        for row in records
    ]
    lines.append(_md_table(["年", "增速", "收入"], rows))
    return "\n\n".join(lines)


def _calc_margin_cash(template: str, fundamentals: dict, base: dict, records: list[dict]) -> str:
    lines = ["**3. 净利率与股权现金流**"]
    if template == "fcfe":
        lines.append("这家公司用股权自由现金流模板。每年股权现金流 = 收入 × 净利率 × 转换率。")
        lines.append(r"$$m_t=m_0+\frac{t}{N}(m_N-m_0),\qquad FCFE_t=Rev_t\times m_t\times c$$")
    elif template:
        lines.append("这家公司用净利润模板：转换率取 1，退出倍数用市盈率。每年股权现金流等于归母净利。")
        lines.append(r"$$m_t=m_0+\frac{t}{N}(m_N-m_0),\qquad NI_t=Rev_t\times m_t$$")
    else:
        lines.append("假设里没有写模板。下面按预测表里的净利率和股权现金流列示。")
    margin_start = _number(base.get("margin_start"))
    margin_terminal = _number(base.get("margin_terminal"))
    if records:
        if margin_start is None:
            margin_start = records[0]["margin"]
        if margin_terminal is None:
            margin_terminal = records[-1]["margin"]
    if margin_start is not None or margin_terminal is not None:
        lines.append(
            f"净利率从 {_fmt_pct(margin_start)} 走到第 {_fmt_year(records[-1]['year']) if records else 'N'} 年的 {_fmt_pct(margin_terminal)}。"
        )
    lines.append(_conversion_sentence(template, fundamentals, base))
    if not records:
        lines.append("分年净利率和股权现金流缺失。")
        return "\n\n".join(line for line in lines if line)
    rows = [
        [
            _fmt_year(row["year"]),
            _fmt_pct(row["margin"]),
            _fmt_yi(row["net_income"]),
            _fmt_yi(row["fcfe"]),
        ]
        for row in records
    ]
    lines.append(_md_table(["年", "净利率", "归母净利", "股权现金流"], rows))
    return "\n\n".join(line for line in lines if line)


def _conversion_sentence(template: str, fundamentals: dict, base: dict) -> str:
    historical = _number(fundamentals.get("fcf_conversion"))
    used = _number(base.get("cash_conversion"))
    if template and template != "fcfe":
        if historical is None:
            return "报表上的自由现金流转换率缺失。净利润模板的路径使用转换率 1。"
        if used is None or abs(used - 1.0) < 1e-9:
            return f"报表上的自由现金流转换率是 {_fmt_pct(historical)}。净利润模板的路径使用转换率 1。"
        return (
            f"报表上的自由现金流转换率是 {_fmt_pct(historical)}。"
            f"基准情景表里的转换率是 {_fmt_pct(used)}。"
        )
    if historical is None and used is None:
        return "自由现金流转换率缺失。"
    if historical is None:
        return f"历史上的自由现金流转换率缺失。基准情景使用的转换率是 {_fmt_pct(used)}。"
    if used is None:
        return f"历史上的自由现金流转换率是 {_fmt_pct(historical)}。基准情景使用的转换率缺失。"
    if abs(historical - used) < 1e-6:
        return f"基准情景使用的转换率是 {_fmt_pct(used)}。"
    if abs(used - _CONVERSION_FLOOR) < 1e-6 or abs(used - _CONVERSION_CAP) < 1e-6:
        return (
            f"历史上的自由现金流转换率是 {_fmt_pct(historical)}。"
            "模型把转换率限制在 50% 到 120%，"
            f"基准情景使用 {_fmt_pct(used)}。"
        )
    return (
        f"历史上的自由现金流转换率是 {_fmt_pct(historical)}。"
        f"基准情景使用 {_fmt_pct(used)}。"
    )


def _calc_discount(discount: dict, base: dict, records: list[dict]) -> str:
    lines = ["**4. 折现率与各年现值**"]
    lines.append("折现率用资本资产定价模型，再限制在 6% 到 15%。")
    lines.append(r"$$r=r_f+\beta\times ERP$$")
    beta = _number(discount.get("beta"))
    risk_free = _number(discount.get("risk_free"))
    premium = _number(discount.get("equity_risk_premium"))
    used = _number(base.get("discount_rate"))
    if used is None:
        used = _number(discount.get("cost_of_equity"))
    raw = None
    if beta is not None and risk_free is not None and premium is not None:
        raw = risk_free + beta * premium
        lines.append(
            f"β = {beta:.2f}，无风险利率 rf = {risk_free:.2%}，股权风险溢价 ERP = {premium:.2%}。"
        )
        lines.append(
            "$$r="
            f"{risk_free * 100:.2f}\\%+{beta:.2f}\\times {premium * 100:.2f}\\%"
            f"={raw * 100:.2f}\\%$$"
        )
    else:
        lines.append("β、无风险利率或股权风险溢价缺失。")
    lines.append(_discount_bound_sentence(raw, used))
    if not records:
        lines.append("各年现值缺失。")
        return "\n\n".join(lines)
    lines.append(r"$$PV_t=\frac{CF_t}{(1+r)^t}$$")
    total = 0.0
    complete = True
    rows: list[list[str]] = []
    for row in records:
        if row["pv"] is None:
            complete = False
        else:
            total += row["pv"]
        rows.append([_fmt_year(row["year"]), _fmt_yi(row["fcfe"]), _fmt_yi(row["pv"])])
    if complete:
        rows.append(["合计", "", _fmt_yi(total)])
    lines.append(_md_table(["年", "股权现金流", "现值"], rows))
    if not complete:
        lines.append("缺折现率或现金流的年份，现值标为缺失。")
    return "\n\n".join(lines)


def _discount_bound_sentence(raw: float | None, used: float | None) -> str:
    if used is None:
        return "基准情景实际使用的折现率缺失。"
    used_text = f"{used:.2%}"
    if raw is None:
        return f"基准情景实际用 r = {used_text}。"
    raw_text = f"{raw:.2%}"
    if raw < _DISCOUNT_FLOOR - 1e-12 and abs(used - _DISCOUNT_FLOOR) <= 1e-9:
        return f"{raw_text} 低于下限，基准情景实际用 r = {used_text}。"
    if raw > _DISCOUNT_CAP + 1e-12 and abs(used - _DISCOUNT_CAP) <= 1e-9:
        return f"{raw_text} 高于上限，基准情景实际用 r = {used_text}。"
    if abs(raw - used) <= 1e-9:
        return f"{raw_text} 落在 6% 到 15% 以内，基准情景实际用 r = {used_text}。"
    return f"基准情景实际用 r = {used_text}。"


def _calc_terminal(template: str, base: dict, records: list[dict]) -> str:
    lines = ["**5. 终值**"]
    basis = str(base.get("exit_basis") or "").strip().lower()
    if basis not in {"ps", "pe"}:
        basis = "pe" if template and template != "fcfe" else ("ps" if template == "fcfe" else "")
    multiple = _number(base.get("exit_multiple"))
    last = records[-1] if records else None
    amount = None
    amount_name = "基数"
    if basis == "ps":
        amount_name = "收入"
        amount = None if last is None else last["revenue"]
        lines.append("终值用退出市销率，基数是最后一年的收入。")
        lines.append(r"$$TV_N=Rev_N\times P/S$$")
    elif basis == "pe":
        amount_name = "归母净利"
        amount = None if last is None else last["net_income"]
        lines.append("终值用退出市盈率，基数是最后一年的归母净利。")
        lines.append(r"$$TV_N=NI_N\times P/E$$")
    else:
        lines.append("退出口径缺失。")
    terminal = amount * multiple if amount is not None and multiple is not None else None
    year = _fmt_year(last["year"]) if last else "N"
    multiple_text = "退出倍数缺失" if multiple is None else f"退出倍数 {_fmt_multiple(multiple)} 倍"
    lines.append(f"第 {year} 年{amount_name} {_fmt_yi(amount)}，{multiple_text}，终值 {_fmt_yi(terminal)}。")
    terminal_pv = _number(base.get("pv_terminal"))
    weight = _number(base.get("terminal_weight"))
    equity = _resolved_equity(base, records)
    if weight is None and terminal_pv is not None and equity not in (None, 0.0):
        weight = terminal_pv / equity
    lines.append(f"终值现值 {_fmt_yi(terminal_pv)}，终值现值占股权价值 {_fmt_pct(weight)}。")
    return "\n\n".join(lines)


def _calc_per_share(fundamentals: dict, base: dict, records: list[dict]) -> str:
    lines = ["**6. 每股价值**"]
    lines.append(r"$$\text{每股价值}=\frac{\text{股权价值}}{\text{总股本}}$$")
    explicit = _resolved_explicit(base, records)
    terminal_pv = _number(base.get("pv_terminal"))
    equity = _resolved_equity(base, records)
    shares = _number(fundamentals.get("shares"))
    per_share = _number(base.get("per_share"))
    if per_share is None and equity is not None and shares is not None and shares > 0:
        per_share = equity / shares
    lines.append(
        f"显式期现值合计 {_fmt_yi(explicit)}，终值现值 {_fmt_yi(terminal_pv)}，股权价值 {_fmt_yi(equity)}。"
    )
    if per_share is not None and shares is not None and shares > 0:
        lines.append(f"总股本 {_fmt_shares(shares)}。基准每股价值 = 股权价值 / 总股本 = {_fmt_yuan(per_share)}。")
    else:
        lines.append(f"总股本 {_fmt_shares(shares)}。情景表里的基准每股价值是 {_fmt_yuan(per_share)}。")
    price = _number(fundamentals.get("price"))
    if per_share is None or price is None or price <= 0:
        lines.append("现价或基准每股价值缺失，算不出估值机会。")
        return "\n\n".join(lines)
    opportunity = per_share / price - 1.0
    lines.append(
        "估值机会 = 基准每股价值 / 现价 − 1 = "
        f"{per_share:.2f} / {price:.2f} − 1 = {opportunity:+.1%}。"
    )
    lines.append(_opportunity_sentence(opportunity))
    return "\n\n".join(lines)


def _calc_aside(fundamentals: dict, implied: dict, bear: dict, bull: dict) -> str:
    lines = ["**7. 熊市、牛市与反向 DCF**"]
    price = _number(fundamentals.get("price"))
    parts = []
    for label, row in (("熊市", bear), ("牛市", bull)):
        value = _number(row.get("per_share"))
        if value is None:
            parts.append(f"{label}每股价值缺失")
            continue
        if price is None or price <= 0:
            parts.append(f"{label}每股价值 {_fmt_yuan(value)}")
            continue
        upside = value / price - 1.0
        parts.append(f"{label}每股价值 {_fmt_yuan(value)}，相对现价 {upside:+.1%}")
    lines.append("；".join(parts) + "。同一组每股价值在三情景表里。")
    implied_line = _implied_sentence(implied)
    if implied_line:
        lines.append(implied_line)
    return "\n\n".join(lines)


def _implied_sentence(implied: dict) -> str:
    start = _number(implied.get("growth_start"))
    average = _number(implied.get("average_growth"))
    bits = []
    if start is not None:
        bits.append(f"第 1 年增速 {_fmt_pct(start)}")
    if average is not None:
        bits.append(f"预测期平均增速 {_fmt_pct(average)}")
    if not bits:
        return ""
    suffix = ""
    converged = implied.get("converged")
    if converged is False or converged == 0:
        suffix = "（触及求解边界，未收敛）"
    return "反向 DCF 的隐含增速：" + "，".join(bits) + suffix + "。"


def _opportunity_sentence(opportunity: float) -> str:
    signed = f"{opportunity:+.1%}"
    if signed in {"+0.0%", "-0.0%"}:
        return "若按这条基准路径，模型给出的每股价值与现价接近。"
    word = "高" if opportunity > 0 else "低"
    return f"若按这条基准路径，模型给出的每股价值比现价{word} {abs(opportunity):.1%}。"


def _resolved_explicit(base: dict, records: list[dict]) -> float | None:
    explicit = _number(base.get("pv_explicit"))
    if explicit is not None:
        return explicit
    values = [row["pv"] for row in records]
    if values and all(value is not None for value in values):
        return float(sum(values))
    return None


def _resolved_equity(base: dict, records: list[dict]) -> float | None:
    equity = _number(base.get("equity_value"))
    if equity is not None:
        return equity
    explicit = _resolved_explicit(base, records)
    terminal_pv = _number(base.get("pv_terminal"))
    if explicit is None or terminal_pv is None:
        return None
    return explicit + terminal_pv


def _year_records(forecast: pd.DataFrame | None, discount_rate: float | None) -> list[dict]:
    frame = _base_forecast(forecast)
    if frame.empty:
        return []
    rows: list[dict] = []
    ordered = frame.copy()
    ordered["year"] = pd.to_numeric(ordered["year"], errors="coerce")
    ordered = ordered.dropna(subset=["year"]).sort_values("year", kind="mergesort")
    has_published_pv = "pv_fcfe" in ordered.columns
    for raw in ordered.to_dict(orient="records"):
        year = _number(raw.get("year"))
        if year is None:
            continue
        revenue = _number(raw.get("revenue"))
        margin = _number(raw.get("margin"))
        net_income = _number(raw.get("net_income"))
        if net_income is None and revenue is not None and margin is not None:
            net_income = revenue * margin
        fcfe = _number(raw.get("fcfe"))
        pv = _number(raw.get("pv_fcfe")) if has_published_pv else None
        if pv is None and not has_published_pv and fcfe is not None and discount_rate is not None and discount_rate > -1:
            pv = fcfe / (1.0 + discount_rate) ** year
        rows.append(
            {
                "year": year,
                "growth": _number(raw.get("growth")),
                "revenue": revenue,
                "margin": margin,
                "net_income": net_income,
                "fcfe": fcfe,
                "pv": pv,
            }
        )
    return rows


def _published_scenario(assumptions: dict, scenarios: pd.DataFrame | None, name: str) -> dict:
    stored = _mapping(_mapping(assumptions.get("scenarios")).get(name))
    row = _scenario_row(scenarios, name)
    if not row:
        return stored
    merged = dict(stored)
    for key, value in row.items():
        if key == "scenario" or value is None:
            continue
        plain = value.item() if hasattr(value, "item") else value
        if plain is None or (isinstance(plain, float) and pd.isna(plain)):
            continue
        merged[key] = plain
    return merged


def _scenario_row(scenarios: pd.DataFrame | None, name: str) -> dict | None:
    if scenarios is None or scenarios.empty or "scenario" not in scenarios.columns:
        return None
    matched = scenarios.loc[scenarios["scenario"].astype(str).eq(name)]
    if matched.empty:
        return None
    return matched.iloc[0].to_dict()


def _company_name(assumptions: dict, fundamentals: dict) -> str:
    for source in (assumptions.get("name"), fundamentals.get("name")):
        text = str(source or "").strip()
        if text and text not in {"None", "nan", "<NA>"}:
            return text
    return ""


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _fmt_asof(value: object) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if text in {"", "None", "nan", "<NA>"}:
        return ""
    return text


def _fmt_yi(value: object) -> str:
    number = _number(value)
    if number is None:
        return "缺失"
    return f"{number / 1e8:.1f} 亿"


def _fmt_yuan(value: object) -> str:
    number = _number(value)
    if number is None:
        return "缺失"
    return f"{number:.2f} 元"


def _fmt_shares(value: object) -> str:
    number = _number(value)
    if number is None:
        return "缺失"
    return f"{number / 1e8:.2f} 亿股"


def _fmt_pct(value: object) -> str:
    number = _number(value)
    if number is None:
        return "缺失"
    return f"{number:.1%}"


def _fmt_multiple(value: object) -> str:
    number = _number(value)
    if number is None:
        return "缺失"
    return f"{number:.3f}"


def _fmt_year(value: object) -> str:
    number = _number(value)
    if number is None:
        return "缺失"
    if float(number).is_integer():
        return str(int(number))
    return f"{number:g}"


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    head = "| " + " | ".join(cell(item) for item in headers) + " |"
    rule = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(cell(item) for item in row) + " |" for row in rows]
    return "\n".join([head, rule, *body])
