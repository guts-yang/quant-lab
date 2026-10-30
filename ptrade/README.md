# ptrade

放到山西证券 PTrade 客户端里的日线策略，以及本机用来准备选股文件的导出脚本。研究进程不下单。终端手册在 `docs/ptrade/`，不在这个目录。

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 把本目录标成 Python 包，方便测试导入里面的纯函数。 |
| `export_book.py` | 在本机按截面日打分，写出 `fundamental_book.json`。文件里是排名，不是委托。 |
| `fundamental_low_turnover.py` | 复制到 PTrade 后作为日线策略运行。盘前读上面的 JSON，只做整手调仓、止损和涨跌停保护。 |

导出：

```bash
python -m ptrade.export_book --asof YYYYMMDD --top 8 --out fundamental_book.json
```

把生成的 JSON 放到 PTrade 研究目录。策略文件的频率选日线。上海代码在研究侧是 `.SH`，进客户端后改成 `.SS`。日线 `handle_data` 在盘后触发，实盘委托可能到下一交易日才成交。
