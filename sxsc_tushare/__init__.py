"""山西证券 Tushare 客户端。

官方 wheel 不在 PyPI，也不在 PTrade 安装包里。这里实现文档里的
``set_token`` / ``get_api(env="prd")``，请求发到山西证券 Python 网关。
"""

from __future__ import annotations

from sxsc_tushare.client import DataApi

__version__ = "0.1.0"

_TOKEN = ""
PRD_URL = "http://221.204.19.233:7172"
QA_URL = "http://10.5.42.71:7171"


def set_token(token: str) -> None:
    global _TOKEN
    _TOKEN = str(token or "").strip()


def get_token() -> str:
    return _TOKEN


def get_api(env: str = "prd", token: str = "", timeout: float = 30) -> DataApi:
    """仿真和本机 Python 用 ``prd``。``qa`` 是生产客户端内网，本机连不上。"""
    chosen = str(token or "").strip() or _TOKEN
    if not chosen:
        raise RuntimeError("缺少 token。先调用 sxsc_tushare.set_token，或给 get_api 传入 token。")
    urls = {"prd": PRD_URL, "qa": QA_URL}
    if env not in urls:
        raise ValueError(f"未知环境 {env!r}，只能是 prd 或 qa。")
    return DataApi(chosen, urls[env], timeout=timeout)
