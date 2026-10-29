# quant-lab

按公告日排雷，再按质量 0.4、价值 0.3、成长 0.3 取出综合分最高的 5–8 只。没有回测，也不下单。

## 命令

```bash
pip install -e ".[dev,web]"
python -m core.universe --asof YYYYMMDD
python -m strategy.scorer --asof YYYYMMDD --top 8
python -m strategy.publish --asof YYYYMMDD --top 8
streamlit run web/app.py
```

Streamlit Community Cloud 的入口是 `web/app.py`，依赖用 `requirements.txt`，不配置 Secrets。把 `web/snapshots/` 里的新日期推上去，网站才会更新。

## 数据

用 `SXSC_TUSHARE_TOKEN` 打山西证券网关，本机 `env=prd`。公开版 token 不能打这个网关。Token 只放 `.env`。接口只有 T-1，缓存写在 `data/cache/`。

## 目录

- `sxsc_tushare`：券商 HTTP 客户端
- `core`：契约、缓存、公告日、排雷
- `factors`、`strategy`：因子和打分
- `web`：只读页面，读 `web/snapshots/`
- `docs/ptrade`：终端手册和开通说明
- `backtest`、`ptrade`：尚未实现

## 规则

可用日用 `f_ann_date`，否则 `ann_date`。晚于截面日的报表不可见。阈值在 `CleanerConfig`。名称来自 `namechange`，不用今天的简称。EP、BP 用截面日总市值，不用现成 PE、PB。毛利率波动越低越好。

网站不读 token。有真实快照后，示例日不再出现。`pytest` 不访问网络。
