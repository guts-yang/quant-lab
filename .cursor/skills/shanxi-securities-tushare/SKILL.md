---
name: shanxi-securities-tushare
description: >-
  Fetches Shanxi Securities Tushare history through the repo client sxsc_tushare
  (env=prd, T-1 only). Use when the user asks to pull, export, or cache tushare,
  sxsc_tushare, 山西证券, A-share, financial, index, fund, futures, macro, or
  northbound data, or mentions TUSHARE_TOKEN, SXSC_TUSHARE_TOKEN, points, or
  token expiry. Research pulls use the broker token. TUSHARE_TOKEN is the
  separate official tushare.pro token and is not the default.
---

# 山西证券 Tushare

研究数据走本仓库的 `sxsc_tushare`，不要走公开版 `tushare`。只取 T-1 历史，没有实时行情，也不给买卖建议。

## 先确认环境

```bash
python .cursor/skills/shanxi-securities-tushare/scripts/check_env.py
```

`SXSC_TUSHARE_TOKEN` 缺失时停止，不要取数，也不要编造数据。Token 只放在根目录 `.env` 或环境变量里，不要写进代码、日志或 git。

## 用哪枚 token

| 变量 | 用途 |
| --- | --- |
| `SXSC_TUSHARE_TOKEN` | 默认。山西证券网关，`get_api(env="prd")` |
| `TUSHARE_TOKEN` | 公开版 tushare.pro，与上一枚不是同一枚，两边不通用 |

`TUSHARE_TOKEN` 在 2026-10-29 登记为 **2100 积分，有效期至 2026-11-03**。到期前不要用它做全市场或多年批量拉取。只有用户明确要公开版数据时才用它。已核对：`stock_basic`、`daily`、`stk_mins` 可调；`fina_indicator_vip` 无权限。

公开版的远程 MCP 在 `.cursor/mcp.json`，服务名 `tushareMcp`，传输是 SSE / Streamable HTTP。这个文件含 token，已被 gitignore。调用它会消耗上面的 2100 积分。研究数据仍然走券商客户端。

券商账户积分以 `.env` 的 `SXSC_TUSHARE_POINTS` 为准，不要和 2100 混用。

## 取数

```python
import os
import sxsc_tushare as sx

sx.set_token(os.environ["SXSC_TUSHARE_TOKEN"])
pro = sx.get_api(env="prd")
```

调用方式以 [通过 Python SDK 获取](http://221.204.19.233:7173/document/1?doc_id=131) 为准。本机和仿真只用 `prd`。`qa` 是生产客户端内网，本机连不上。

股票池、财报和行情缓存走 `core.tushare_provider.TushareProvider`，不要另写一套缓存。截面打分仍用 `python -m strategy.scorer`，本 skill 不负责分析。

接口名先查 [api-index.md](api-index.md)，再调用。日期用 `YYYYMMDD`。未来日期裁到最近一个已结束的交易日并告诉用户。`trade_date` 与 `start_date`/`end_date` 不要同时传。裸代码补全后缀后再请求：上证 `.SH`，深证 `.SZ`，北交所 `.BJ`。

默认窗口：最近 20 个交易日；「最近三个月」60 个交易日；「今年」从当年 1 月 1 日到 T-1；「财报」最近 8 个季度加最近年报。

频率大约每分钟 600 次。全市场循环时沿用 provider 的 0.35 秒间隔。财务接口先试 `*_vip`，返回无权限、接口不存在或未知数据源时，再按股票代码调用普通接口。

空表不一定是失败：可能是非交易日、尚未上市，或参数不对。权限错误要原样说明，不要改用另一枚 token 重试，除非用户要求。

## 交付

先给结论，再给口径（接口、日期、复权与否），然后是表或文件路径。导出放在 `data/`，该目录已被 gitignore。公告、新闻、研报和实时行情这个网关没有。
