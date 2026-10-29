# 山西证券 PTrade 开通与下载

整理自开通邮件和山西证券官网（2026-10-29）。账号、token、积分只放在仓库根目录 `.env`，本文不写密钥。`.env` 已被 gitignore。

## 开通结果

程序化交易报备已审核通过，已开通 PTrade 交易权限，并已申请开通山西证券 PTrade 量化交易系统权限和 Tushare 数据调用权限。

| 项目 | 取值 |
| --- | --- |
| 正式交易系统登录账号 | `.env` 的 `PTRADE_PROD_ACCOUNT` |
| 正式交易系统登录密码 | `.env` 的 `PTRADE_PROD_PASSWORD`（与券商交易密码相同） |
| Tushare 正式 token | `.env` 的 `SXSC_TUSHARE_TOKEN` |
| Tushare 正式账户积分 | `.env` 的 `SXSC_TUSHARE_POINTS` |
| 仿真系统登录账号 | `.env` 的 `PTRADE_SIM_ACCOUNT` |
| 仿真系统登录及交易密码 | `.env` 的 `PTRADE_SIM_PASSWORD`（身份证后 6 位数字，不含字母） |

本地 Python 和仿真客户端调 `sxsc_tushare` 使用 `env=prd`。生产客户端使用 `env=qa`，那个地址在终端内网，本机连不上。调用方式以 [通过 Python SDK 获取](http://221.204.19.233:7173/document/1?doc_id=131) 为准。接口只有 T-1 日历史，没有实时行情。安装说明和官方包在 `docs/tushare/`。

## 客户端下载

官网：<https://www.i618.com.cn/main/companybusi/wealth/quantitativetrading/ptrade/index.shtml>

页面上的「软件下载」由接口动态加载（`funcNo=741004`，`software_type=3`）。2026-10-29 查到的直链：

| 客户端 | 版本 | 大小 | 更新日期 | 下载 |
| --- | --- | --- | --- | --- |
| PTrade 仿真客户端 | V202507-00-027 | 212M | 2026-10-12 | <https://dn2-ctc.i618.com.cn/download/PTrade1.0-ClientV202507-00-027-C020-10969-FZ(山西-FZ).zip> |
| PTrade 生产客户端 | V202507-00-027 | 211M | 2026-10-19 | <https://dn2-ctc.i618.com.cn/download/PTrade1.0-ClientV202507-00-027-C021-10969-SC(山西).zip> |

开通邮件里的生产和仿真下载页是同一个官网地址。打不开时，把链接复制到浏览器地址栏再打开。安装包约 210MB，不放进本仓库。

## 帮助文档

开通邮件：

1. 生产系统客户端下载：见上面官网和直链。
2. 仿真系统客户端下载：见上面官网和直链。
3. 系统使用资料见邮件附件。本目录已有：
   - `1.PTrade交易系统操作手册V202301-00（山证）.pdf`
   - `PTrade新版API帮助文档.pdf`

   Tushare 数据库说明在 `docs/tushare/Tushare数据库使用.pdf`。
4. 山西证券 PTrade 生产交易系统 API：<https://ptrade.i618.com.cn:7766/hub/help/api>
5. 山西证券 PTrade 仿真交易系统 API：<https://ptrade-fz.i618.com.cn:7766/hub/help/api>

官网同页还挂了这两份在线 API 文档。知识文档栏目：<https://www.i618.com.cn/main/companybusi/wealth/quantitativetrading/ptradedoc/index.shtml>

## 官网产品说明

PTrade 面向高净值个人和主动管理型产品，包含程序化交易（策略投研、回测、交易）、日内回转、快速交易、投机套利和异常交易风控。

交易特色：股票、两融、期权、期货等基础交易；算法交易、套利、移动止盈、智能 T0；功能与策略定制；网格、抢单、批量埋单、盘口扫单；策略编写、回测、仿真和实盘。

开通路径：山证「汇通启富」APP → 首页业务办理 → 网签 PTrade 使用协议。券商确认网签并满足开通条件后发开通邮件。

咨询：客服 95573、（0351）95573；邮箱 95573@i618.com.cn；企微群「山西证券 PTrade 业务咨询群」。
