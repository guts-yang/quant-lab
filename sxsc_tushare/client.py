"""山西证券 Tushare HTTP 客户端。"""

from __future__ import annotations

from functools import partial

import pandas as pd
import requests


class DataApi:
    def __init__(self, token: str, http_url: str, timeout: float = 30) -> None:
        self._token = token
        self._http_url = http_url
        self._timeout = timeout

    def query(self, api_name: str, fields: str = "", **kwargs: object) -> pd.DataFrame:
        response = requests.post(
            self._http_url,
            json={
                "api_name": api_name,
                "token": self._token,
                "params": kwargs,
                "fields": fields,
            },
            timeout=self._timeout,
            headers={"Connection": "close"},
        )
        response.raise_for_status()
        result = response.json()
        if result.get("code") != 0:
            raise Exception(result.get("msg") or f"{api_name} 调用失败")
        data = result.get("data") or {}
        columns = data.get("fields") or []
        items = data.get("items") or []
        return pd.DataFrame(items, columns=columns)

    def __getattr__(self, name: str):
        return partial(self.query, name)
