"""Tushare Pro 适配。拉取结果写入 data/cache，重复运行不重复扣积分。"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pandas as pd

from core.provider import (
    BALANCE_COLUMNS,
    BAR_COLUMNS,
    INDICATOR_COLUMNS,
    INSTRUMENT_COLUMNS,
    NAME_COLUMNS,
    SUSPEND_COLUMNS,
    annual_indicators,
    filter_periods,
    normalize_balance_sheet,
    normalize_bars,
    normalize_indicator,
    normalize_instruments,
    normalize_name_history,
    normalize_suspensions,
    select_index_snapshot,
)

logger = logging.getLogger(__name__)

_BALANCE_FIELDS = (
    "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,"
    "goodwill,total_hldr_eqy_exc_min_int,total_assets,total_liab"
)
_INDICATOR_FIELDS = "ts_code,ann_date,end_date,profit_dedt"
_PERMISSION_MARKERS = ("权限", "积分")
_RATE_MARKERS = ("每分钟", "最多访问", "访问频率", "too many", "timeout", "超时")


class TusharePermissionError(PermissionError):
    """当前积分调不了 vip 接口。"""


class TushareProvider:
    def __init__(
        self,
        token: str | None = None,
        cache_dir: str | Path = "data/cache",
        pause_seconds: float = 0.35,
        pro: object | None = None,
    ) -> None:
        self._token = token
        self.cache_dir = Path(cache_dir)
        self.pause_seconds = pause_seconds
        self._pro = pro

    def get_instruments(self, asof: pd.Timestamp) -> pd.DataFrame:
        asof = pd.Timestamp(asof).normalize()
        basic = self._stock_basic()
        listed = basic["list_date"].isna() | basic["list_date"].le(asof)
        return basic.loc[listed].reset_index(drop=True)

    def get_name_history(self) -> pd.DataFrame:
        path = self.cache_dir / "namechange.parquet"
        cached = _read_fresh(path, max_age_days=7)
        if cached is not None:
            return cached

        frames: list[pd.DataFrame] = []
        this_year = pd.Timestamp.today().year
        for year in range(1990, this_year + 1):
            raw = self._query(
                "namechange",
                start_date=f"{year}0101",
                end_date=f"{year}1231",
                fields="ts_code,name,start_date,end_date,ann_date,change_reason",
            )
            if not raw.empty:
                frames.append(raw)
            time.sleep(self.pause_seconds)
        history = normalize_name_history(_concat(frames))
        history = history.drop_duplicates(["ts_code", "name", "start_date", "end_date"])
        _write(history, path)
        return history

    def get_suspensions(self, trade_date: pd.Timestamp) -> pd.DataFrame:
        trade_date = pd.Timestamp(trade_date).normalize()
        key = trade_date.strftime("%Y%m%d")
        path = self.cache_dir / "suspend" / f"{key}.parquet"
        cached = _read(path)
        if cached is not None:
            return cached
        raw = self._query("suspend_d", suspend_type="S", trade_date=key)
        frame = normalize_suspensions(raw)
        _write(frame, path)
        return frame

    def get_daily_bars(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        start = pd.Timestamp(start).normalize()
        end = pd.Timestamp(end).normalize()
        frames: list[pd.DataFrame] = []
        for trade_date in self._open_dates(start, end):
            key = pd.Timestamp(trade_date).strftime("%Y%m%d")
            path = self.cache_dir / "daily" / f"{key}.parquet"
            cached = _read(path)
            if cached is None:
                raw = self._query("daily", trade_date=key, fields="ts_code,trade_date,amount")
                cached = normalize_bars(raw)
                _write(cached, path)
                time.sleep(self.pause_seconds)
            frames.append(cached)
        return _concat(frames, BAR_COLUMNS)

    def get_index_members(self, index_code: str, asof: pd.Timestamp) -> pd.DataFrame:
        asof = pd.Timestamp(asof).normalize()
        start = asof - pd.Timedelta(days=120)
        path = self.cache_dir / "index_weight" / f"{index_code}_{start:%Y%m%d}_{asof:%Y%m%d}.parquet"
        cached = _read(path)
        if cached is None:
            cached = self._query(
                "index_weight",
                index_code=index_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=asof.strftime("%Y%m%d"),
            )
            _write(cached, path)
        return select_index_snapshot(cached, asof)

    def get_balance_sheets(self, periods: Sequence[str]) -> pd.DataFrame:
        return self._financials(
            periods,
            vip_api="balancesheet_vip",
            code_api="balancesheet",
            fields=_BALANCE_FIELDS,
            cache_name="balancesheet",
            normalize=normalize_balance_sheet,
            columns=BALANCE_COLUMNS,
        )

    def get_indicators(self, periods: Sequence[str]) -> pd.DataFrame:
        frame = self._financials(
            periods,
            vip_api="fina_indicator_vip",
            code_api="fina_indicator",
            fields=_INDICATOR_FIELDS,
            cache_name="fina_indicator",
            normalize=normalize_indicator,
            columns=INDICATOR_COLUMNS,
        )
        return annual_indicators(frame)

    def _financials(
        self,
        periods: Sequence[str],
        *,
        vip_api: str,
        code_api: str,
        fields: str,
        cache_name: str,
        normalize: Callable[[pd.DataFrame], pd.DataFrame],
        columns: list[str],
    ) -> pd.DataFrame:
        period_list = [str(period) for period in periods]
        cached, missing = self._read_period_cache(cache_name, period_list)
        if not missing:
            return _concat(cached, columns)
        try:
            fetched: list[pd.DataFrame] = []
            for period in missing:
                raw = self._query(vip_api, period=period, fields=fields)
                frame = normalize(raw)
                _write(frame, self._period_path(cache_name, period))
                fetched.append(frame)
                time.sleep(self.pause_seconds)
        except (TusharePermissionError, AttributeError) as exc:
            logger.info("%s 不可用（%s），改为按股票代码拉取并缓存", vip_api, exc)
            history = self._financials_by_code(code_api, fields, cache_name, normalize, columns)
            sliced = filter_periods(history, period_list)
            for period in period_list:
                _write(filter_periods(sliced, [period]), self._period_path(cache_name, period))
            return sliced
        return _concat([*cached, *fetched], columns)

    def _financials_by_code(
        self,
        code_api: str,
        fields: str,
        cache_name: str,
        normalize: Callable[[pd.DataFrame], pd.DataFrame],
        columns: list[str],
    ) -> pd.DataFrame:
        codes = self._stock_basic()["ts_code"].astype(str).tolist()
        frames: list[pd.DataFrame] = []
        folder = self.cache_dir / cache_name / "by_code"
        for index, code in enumerate(codes, start=1):
            path = folder / f"{code}.parquet"
            cached = _read(path)
            if cached is None:
                raw = self._query(code_api, ts_code=code, fields=fields)
                cached = normalize(raw)
                _write(cached, path)
                time.sleep(self.pause_seconds)
            frames.append(cached)
            if index % 200 == 0 or index == len(codes):
                logger.info("%s 已覆盖 %s/%s", code_api, index, len(codes))
        return _concat(frames, columns)

    def _stock_basic(self) -> pd.DataFrame:
        path = self.cache_dir / "stock_basic.parquet"
        cached = _read_fresh(path, max_age_days=1)
        if cached is not None:
            return cached
        frames: list[pd.DataFrame] = []
        for status in ("L", "D", "P"):
            raw = self._query(
                "stock_basic",
                exchange="",
                list_status=status,
                fields="ts_code,list_date,delist_date,list_status",
            )
            if not raw.empty:
                frames.append(raw)
            time.sleep(self.pause_seconds)
        basic = normalize_instruments(_concat(frames)).drop_duplicates("ts_code", keep="last")
        _write(basic, path)
        return basic

    def _open_dates(self, start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
        path = self.cache_dir / "trade_cal" / f"{start:%Y%m%d}_{end:%Y%m%d}.parquet"
        cached = _read(path)
        if cached is None:
            raw = self._query(
                "trade_cal",
                exchange="SSE",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                is_open="1",
            )
            if raw.empty or "cal_date" not in raw.columns:
                cached = pd.DataFrame(columns=["cal_date"])
            else:
                cached = pd.DataFrame({"cal_date": pd.to_datetime(raw["cal_date"])})
            _write(cached, path)
        if cached.empty:
            return []
        return sorted(pd.to_datetime(cached["cal_date"]).tolist())

    def _read_period_cache(self, cache_name: str, periods: list[str]) -> tuple[list[pd.DataFrame], list[str]]:
        cached: list[pd.DataFrame] = []
        missing: list[str] = []
        for period in periods:
            frame = _read(self._period_path(cache_name, period))
            if frame is None:
                missing.append(period)
            else:
                cached.append(frame)
        return cached, missing

    def _period_path(self, cache_name: str, period: str) -> Path:
        return self.cache_dir / cache_name / "period" / f"{period}.parquet"

    def _api(self) -> object:
        if self._pro is None:
            _load_dotenv()
            token = self._token or os.environ.get("TUSHARE_TOKEN", "")
            if not token:
                raise RuntimeError("缺少 TUSHARE_TOKEN。复制 .env.example 为 .env 后填入，或直接设置环境变量。")
            import tushare as ts

            self._pro = ts.pro_api(token)
        return self._pro

    def _query(self, api_name: str, **kwargs: object) -> pd.DataFrame:
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                result = getattr(self._api(), api_name)(**kwargs)
            except (TusharePermissionError, AttributeError):
                raise
            except Exception as exc:
                text = str(exc)
                if _is_permission(text):
                    raise TusharePermissionError(text) from exc
                if _is_rate_limit(text) and attempt < 5:
                    time.sleep(min(60.0, 2**attempt + self.pause_seconds))
                    last_error = exc
                    continue
                raise
            if result is None:
                return pd.DataFrame()
            return result
        raise RuntimeError(f"{api_name} 连续触发频控") from last_error


def _is_permission(message: str) -> bool:
    return any(marker in message for marker in _PERMISSION_MARKERS)


def _is_rate_limit(message: str) -> bool:
    lowered = message.lower()
    return any(marker in message or marker in lowered for marker in _RATE_MARKERS)


def _load_dotenv() -> None:
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _read(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_parquet(path)


def _read_fresh(path: Path, max_age_days: float) -> pd.DataFrame | None:
    if not path.exists():
        return None
    age_seconds = time.time() - path.stat().st_mtime
    if age_seconds >= max_age_days * 86400:
        return None
    return pd.read_parquet(path)


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _concat(frames: list[pd.DataFrame], columns: list[str] | None = None) -> pd.DataFrame:
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return pd.DataFrame(columns=columns or [])
    return pd.concat(usable, ignore_index=True)
