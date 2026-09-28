# 一个登录面板，两种身份，两套会话

管理后台（`/login`，`admin_user` 那个恒 `id=1` 的共享账号）与员工端（`/hygiene/login` + `/hygiene/register`，`hygiene_employees` 花名册）各有自己的登录面板、路由与会话 cookie（`luyun_session` / `luyun_staff_session`，`api/auth.py:108-120`、`api/hygiene.py:280-297`）：两套文案、两套视觉、两份「记住密码」逻辑写了两遍，员工和店长还各自面对一个入口。本次把**入口**收成一个，**身份与会话仍分两套**。

**决策**：`/login` 一个面板，显式两个 Tab（管理员 / 员工）承载两种身份的登录；后端 `sessions` 与 `hygiene_staff_sessions` 两张表、两个 cookie、两条守卫链一律不动。

- **不做账号形态自动判别，也不做统一登录 API**：员工用手机号、超级管理员用用户名，凭据键不同（`UNIQUE(tenant_id, phone)` vs `admin_user` 恒 `id=1`），两张身份表没有可共用的字段，唯一的现成复用点是 `services/password_hash.py` 的 `sha256$bcrypt`。合并会话要同时改 `identify_ws`、hub 的 `allowed_topics` / `_staff_owns_scope` 与全部鉴权依赖，那是把安全边界重做一遍，不是整合面板。
- **登录页的状态不变**：首次初始化管理员（`/api/auth/init`）留在 `/login`；员工自助注册独立到 `/register`（`public + standalone`，不要求登录）；已登录确认面板与 `?switch=1` 换账号保留；`/login` 记住上次选的 Tab（复用 `utils/loginPrefs.js` 的 admin/staff 命名空间），员工手机一打开就落在员工 Tab 上。
- **落点按身份互斥**：`?next=` 只在员工身份下认 `/staff/*`，管理员身份不认 `/staff/*` 与 `/login`。员工未登录访问 `/staff/*` 时守卫跳 `/login?next=<目标>`，且 `next` 属 `/staff/*` 时强制开在员工 Tab —— 现状 `router/index.js:90` 不带 `next`，三个员工页各自写了一遍 401 落点。
- **路由重整**：员工端 `/hygiene`、`/today`、`/today/month` → `/staff/clean`、`/staff/today`、`/staff/month`；管理端八个卫生页 `/hygiene-roster`…`/hygiene-attire` → `/hygiene/roster`…`/hygiene/attire`，`/hygiene` 本路径删掉、不再有页面；系统配置页 `/setup` → `/settings`（它一直不是初始化页）。员工端名单拆成两份：PWA 归属 `['/staff', '/register']`，登录后落点白名单只有 `['/staff']` —— `utils/staffPaths.js` 现在一份名单喂三处，而 `/register` 落在员工前缀之外。
- **服务端两张豁免表跟着重建，这是本次唯一的安全要害**：`HTML_AUTH_EXACT` 加 `/staff`、`/register`（`/settings` **不许**加——它是需要会话的页面，加进去就是对未登录访客放行 POS 凭据 / 数据库凭据 / API Token；首次初始化在 `/login`，跟它无关），`HTML_AUTH_PREFIXES` 加 `/staff/` 并**必须删掉 `/hygiene/`**（`main.py:695`）—— 员工登录页以前是靠这段前缀免登录墙的，改完之后 `/hygiene/` 底下全是管理端页面，忘了删就是八个卫生页对未登录者放行。反代两个文件不用动：六前缀 allow-list 之外一律兜底反代给 uvicorn。
- **PWA 安装身份跟着 Tab 走**：`selectPwaManifest` 现在只吃 pathname，扩成「路径 + 面板身份」，员工 Tab 挂 `hygiene.webmanifest`、管理员 Tab 挂 `admin.webmanifest`；`hygiene.webmanifest` 的 `start_url` 由 `/today` 改成 `/staff/today`（两份清单 `scope` 都是 `/`，身份本来就靠 manifest `id` 区分）。
- **员工端 WS 连接显式声明身份**：合并面板后「同一浏览器同时持两套 cookie」从罕见变成顺手可得，而 `identify_ws`（`api/security.py:125-146`）的优先级是 admin 先于 staff —— 员工页会被判成 admin，`allowed_topics` 全开、`_staff_owns_scope` 失效（`services/realtime/hub.py:57-88`）。改成「显式声明 > admin cookie > `?token` > staff cookie」，员工页连接时带声明；单 cookie 场景行为不变，KDS 的 `?token` 链路不受影响。

**旧 URL 不做兼容**，这是明确取舍：`/today`、`/hygiene*`、`/hygiene-roster` 等一律从 `SPA_PAGE_ROUTES`、两张豁免表、vue-router 里删掉，不加 302、不加前端 redirect 别名、也不加进 service worker 的 `navigateFallbackDenylist`（`admin-web/vite.config.js:26-34`）。后果要认：已安装的卫生 PWA 把 `start_url=/today` 烧在客户端，而 SW 的 `navigateFallback: '/index.html'` 会先把导航吃掉 —— 新构建生效后点老图标进的是**没有 `/today` 路由的空壳**（vue-router 没有 catch-all），不是登录墙的 302，空壳里也没有任何指向新地址的线索。所以门店要重新把 `/login` 发到员工手上，并指导他们删掉旧图标重装。

**Considered options**：两个登录页各自保留（那就留着「视觉不一致 + 两个入口」的原始痛点）；统一成一套会话与身份（见上，属于安全边界重做）；旧 URL 保留跳转别名或加进 SW 的 denylist（能消掉空壳，本次为控制改动面放弃，代价转成一次门店 ops）。顺带订正 `docs/adr/0013` 里 `hygiene staff at /hygiene` 的漂移，以及 `deploy/README.md`、`docs/RELEASE_AND_DEPLOY.md` 把 `/setup` 描述成首次初始化流程的过期说法。
