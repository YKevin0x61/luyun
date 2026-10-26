# 新信息架构提案（三方向共用 · 随方向稿一起给用户确认）

## 一句话

取消「后台 / 工作台」两套壳的叫法，改成一个产品六个域；**同一批页面按身份裁剪**，而不是按设备切成两个 app。

## 旧 → 新 映射（39 页，不多不少）

| 旧路径 | 新路径 | 变化 |
|---|---|---|
| `/login` | `/login` | 不变；登录后按身份落到各自的「今天」 |
| `/register` | `/register` | 不变（员工自助注册） |
| `/workbench/forbidden` | `/forbidden` | 移出工作台前缀 |
| `/`（运营仪表盘） | `/business/overview` | 店长的经营实时面 |
| `/sales-report` | `/business/sales` | — |
| `/wecom-push` | `/business/push` | 从「系统」挪到「生意」：它是对外分发经营数据 |
| `/admin`（数据管理 49 表） | `/system/data` | 收进系统，只有店长可见 |
| `/logs` | `/system/logs` | — |
| `/settings` | `/system/settings` | — |
| `/workbench`（今天） | `/today` | **合并**：原仪表盘与工作台今天合成四身份各自的首屏 |
| `/workbench/hr/calendar` | `/people/calendar` | — |
| `/workbench/hr/inbox` | `/people/inbox` | — |
| `/workbench/hr/shifts` | `/people/shifts` | — |
| `/workbench/hr/roster` | `/people/roster` | — |
| `/workbench/hr/reminders` | `/people/reminders` | — |
| `/workbench/hr/overtime` | `/people/overtime` | — |
| `/workbench/hr/attendance` | `/people/attendance` | — |
| `/workbench/hr/holidays` | `/people/holidays` | — |
| `/workbench/floor/zones` | `/floor/zones` | — |
| `/workbench/floor/zone-items` | `/floor/zone-items` | — |
| `/workbench/floor/daily` | `/floor/daily` | — |
| `/workbench/floor/attire` | `/floor/attire` | — |
| `/workbench/floor/deep-clean` | `/floor/deep-clean` | — |
| `/workbench/floor/fix` | `/floor/fix` | — |
| `/workbench/floor/boards` | `/floor/boards` | — |
| `/workbench/floor/data` | `/floor/data` | — |
| `/workbench/floor/trend` | `/floor/trend` | — |
| `/workbench/kitchen/recipe` | `/kitchen/recipe` | — |
| `/workbench/kitchen/recipe/detail` | `/kitchen/recipe/detail` | — |
| `/workbench/kitchen/recipe/print` | `/kitchen/recipe/print` | — |
| `/workbench/kitchen/recipe/qr` | `/kitchen/recipe/qr` | — |
| `/workbench/kitchen/recipe/manage` | `/kitchen/recipe/manage` | — |
| `/workbench/kitchen/prep-plan` | `/kitchen/prep` | — |
| `/workbench/kitchen/loss` | `/business/loss` | **跨域搬家**：损耗分析是老板要看的经营指标，不是厨房操作页 |
| `/workbench/staff/today` | `/me` | — |
| `/workbench/staff/month` | `/me/month` | — |
| `/workbench/staff/overtime` | `/me/overtime` | — |
| `/workbench/staff/clean` | `/me/clean` | — |
| `/workbench/staff/loss` | `/me/loss` | 损耗登记留在员工手里 |

## 四身份的导航

**老板（周志远 · 手机）**
```
今天          → 经营脉搏（营业额/单量/客单/卫生一次通过率/损耗），只读
生意          → 经营总览 · 销售报表 · 损耗分析
现场          → 只有「卫生趋势」一页（其余现场页是作业页，不给）
```
看不到：人事（底薪/证件/生日）、厨房（配方/备货操作）、系统（数据管理/日志/配置）。

**店长（陈静 · 桌面 + 手机）**
```
今天 · 生意（4）· 人（8）· 现场（9）· 厨房（6）· 系统（3）
```

**厨师长（王国强 · 手机）**
```
今天
现场          → 日常验收 / 整改单 / 专项卫生 / 仪容仪表 / 红黑榜（不碰工作区与检查项配置）
厨房          → 岗位 / 配方详情 / 打印 / 备货计划
生意          → 只有「损耗分析」
```
看不到：人事、系统、经营总览与销售报表（那是老板/店长的数）。

**员工（李小雨 · 手机，底部 4 项）**
```
今天          我的班 + 今天要拍的卫生 + 待回应
我的班        整月 / 加班与补钟
卫生          日常 / 专项 / 整改回拍 / 待我验收
我的          资料 / 损耗登记 / 设置
```
与店长导航零重叠；扫码进配方阅读面（`/kitchen/recipe/detail`）仍走单独的只读通道。

## 我做的三个 IA 决策（请确认）

1. **「今天」合并两页**：原来的 `/`（运营仪表盘）和 `/workbench`（工作台今天）信息有 60% 重叠。合并后四身份各有自己的今天，区别在内容而不在页面。
2. **损耗分析跨域搬家**：从「厨房」搬到「生意」。理由是老板会看损耗（成本），而厨房那侧只留「备货计划」。
3. **「工作台」这个词从产品里消失**：它原本只是「员工/店长用的那半」的临时叫法，让同一个系统在两个壳里长得不一样。新 IA 里它只是 `/today`、`/people/*`、`/floor/*`、`/kitchen/*`。

## 落地影响（不在本轮做，但要提前知道）

- 路由全变：`main.py` 的 `SPA_PAGE_ROUTES` 与 `admin-web/src/router/pageRoutes.json` 要重写，`tests/test_spa_page_routes.py` 的契约测试跟着改；旧链接自然 404，`?next=` 迁移表要补。
- 四身份不是现有 `admin / staff` 两档：要新增角色字段与页面墙；本轮原型只演，不动后端。
- 每个页面仍保留独立 URL（可深链、可收藏），不做纯前端状态切换。
