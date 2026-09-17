# 设计文档

`docs/` 按主题分组：入口文档留在本层，领域说明进 `pos/`、`prep/`、`hygiene/`。

除少数白名单文件外，本目录默认**不进公开仓**（规则见根 `.gitignore` 的 `docs/*` 段）——公开仓只带发行契约与部分 ADR。

## 入口

| 文档 | 内容 |
|------|------|
| [RELEASE_AND_DEPLOY.md](./RELEASE_AND_DEPLOY.md) | **发行规范与部署流程**（发版清单、Bootstrap、店内升级/回滚；入口文档） |
| [release-asset-layout.md](./release-asset-layout.md) | GitHub Release 前端资产契约（Admin / KDS tar.gz） |
| [MULTI_STORE_PLAN.md](./MULTI_STORE_PLAN.md) | **多店 / 多人 / 三方接入改造方案**（规划中，含性能地基实测与迁移路径） |
| [adr/](./adr/) | 架构决策记录（编号 0001–0083）；仓库布局规则见 [ADR 0012](./adr/0012-repo-layout.md) |
| [agents/](./agents/) | agent 工作流：issue tracker / triage 标签 / domain 文档 |
| [superpowers/](./superpowers/) | 历史实施计划（`plans/`）与设计稿（`specs/`），按日期命名，只读不改 |
| [archive/](./archive/) | 一次性报告目录（勿提交真实营业数据；见 [archive/README.md](./archive/README.md)） |
| [kds-intro.html](./kds-intro.html) | KDS 介绍页（静态 HTML） |

## POS 采集与数据口径（`pos/`）

| 文档 | 内容 |
|------|------|
| [pos/POS_SCRAPER.md](./pos/POS_SCRAPER.md) | POS 采集系统运作说明（组合根、登录会话、堂食差分、外卖取消、对账、下游 nudge） |
| [pos/POS_MITM_RESEARCH.md](./pos/POS_MITM_RESEARCH.md) | 龙管家 2.0 MITM 抓包：登录、实时桌态、逐桌占用/点菜明细 |
| [pos/CY7MM_API_REFERENCE.md](./pos/CY7MM_API_REFERENCE.md) | cy7mm 路由 / API 名录 / 采集字段参考（附 [pos/cy7mm_routes_apis.json](./pos/cy7mm_routes_apis.json)） |
| [pos/DATA_AND_SALES.md](./pos/DATA_AND_SALES.md) | 数据采集与销售报表架构说明（含已归档 CLI；采集细节以 POS_SCRAPER 为准） |
| [pos/DATA_REVENUE.md](./pos/DATA_REVENUE.md) | 订单行营业额 SQL 口径（`config.py` 有注释指向本文） |
| [pos/SETTLED_BILL_DETAIL_FIELDS.md](./pos/SETTLED_BILL_DETAIL_FIELDS.md) | 已结账单明细字段说明（对应 settled_details_*.json） |

## 备货预测（`prep/`）

| 文档 | 内容 |
|------|------|
| [prep/PREP_PLAN.md](./prep/PREP_PLAN.md) | 备货计划功能设计与实现笔记（决议见 ADR 0028 / 0029） |
| [prep/PREP_REVENUE_NOWCAST.md](./prep/PREP_REVENUE_NOWCAST.md) | 营业额 nowcast 冠军模型（ADR 0030） |
| [prep/PREP_NEIGHBOR_BLEND.md](./prep/PREP_NEIGHBOR_BLEND.md) | 日内邻居日融合（ADR 0031） |
| [prep/PREP_PREOPEN_NEIGHBOR.md](./prep/PREP_PREOPEN_NEIGHBOR.md) | 开店前邻居信号（ADR 0032） |
| [prep/PREP_ITEM_MIX_NEIGHBOR.md](./prep/PREP_ITEM_MIX_NEIGHBOR.md) | 单品结构邻居信号（ADR 0033） |
| [prep/PREP_CONSUMPTION_RESIDUAL.md](./prep/PREP_CONSUMPTION_RESIDUAL.md) | 消耗残差（ADR 0034） |
| [prep/PREP_DISH_COOCCUR_REMAINING.md](./prep/PREP_DISH_COOCCUR_REMAINING.md) | 菜品共现与剩余量（ADR 0035） |
| [prep/PREP_BASE_WEIGHT_CALIBRATION.md](./prep/PREP_BASE_WEIGHT_CALIBRATION.md) | 基础权重标定（ADR 0036） |

后 7 篇是候选信号的评估笔记，原型产物在 `scripts/prototype_prep_revenue_nowcast/out/`（`report.vN.html` + `results.vN.json`）。这些笔记里指向的 `VERDICT.vN.md` 已不存在，是既有死链。

## 卫生检查（`hygiene/`）

| 文档 | 内容 |
|------|------|
| [hygiene/HYGIENE_UX_RESEARCH.md](./hygiene/HYGIENE_UX_RESEARCH.md) | 员工手机卫生检查：行业交互/任务逻辑（拍照闭环、整改 CAPA、厨房操作约束） |

日常启动与 API 说明见项目根目录 [README.md](../README.md)。
