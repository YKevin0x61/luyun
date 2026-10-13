/** 工作台内容区的让位度量（票 03 / ADR 0106 决策 2 与 4）。
 *
 *  壳持有内容容器与下 inset：这一处只算**数**（今天只有下 inset 非 0），壳把结果套在唯一的
 *  内容容器上（`WorkbenchShell.vue` 的 `<main id="workbench-main">`），**不发布到
 *  `<html data-workbench>`** —— 那是给主题（颜色 / 字体 / 层级）用的层，度量不进全局
 *  命名空间，发布等于给壳外的消费者绑上（ADR 0106 决策 4）。
 *
 *  为什么是纯函数 + 输入：两个真实来源都量不到 —— 底栏的几何要布局，`env()` 要计算
 *  样式（jsdom 两样都没有）。所以「本屏有没有底栏 / 底栏多高 / 安全区多少」都是入参，
 *  壳在运行时喂真值，测试喂值断言算出来的数（票面「不要试图在 jsdom 里量真实几何」）。
 */

/** 底栏在内容区里占掉的高度：`WorkbenchTabBar.vue` 的 `.wb-tab { min-height: 56px }`
 *  加 1px 上边框 —— 也就是今天那条 `calc(57px + env(safe-area-inset-bottom, 0px))` 里的 57。
 *  改底栏的高度要同时看这里（它是壳的输入，不是壳猜的数）。 */
export const TAB_BAR_HEIGHT_PX = 57

/** 手机档断点。**必须与底栏自己那条 `@media (max-width: 720px)` 同值**：底栏在桌面档是
 *  `display: none`，那它就不该在内容区里占地方。两者一旦漂开，桌面档会凭空多出 57px 下留白
 *  （或手机档少了那 57px、最后一块内容被底栏压住）；护栏见
 *  `views/workbench/__tests__/shellMobileChrome.test.js`。 */
export const MOBILE_MAX_WIDTH_PX = 720

/** **内边距宽档的断点**（票 04）：共享样式表里那条给现场八页换一套内边距的
 *  `@media (min-width: 900px)`。它跟 720 那条不是一回事 —— 720 是底栏的档（手机 ↔ 桌面），
 *  900 是那一组样式自己的档（同一套页面在 721–899 之间仍吃 base 那份内边距）。
 *  改共享样式表那条媒体查询时，这个数要跟着改（护栏：`workbenchInsets.test.js` 里
 *  「宽档断点 900px 就是共享样式表那条媒体查询的数」+ `shellMobileChrome.test.js`）。 */
export const WIDE_INSET_MIN_WIDTH_PX = 900

/** 横向与上内边距都不给的那个值（0，或这一页铺满 —— 见 `contentInsetFor`）。 */
const NO_CONTENT_INSET = { top: '0px', inline: '0px' }

/** 内容区的**横向与上内边距**，按页面清单里的 `group` 查表（票 04 / ADR 0106 决策 3）。
 *
 *  三组数值不同是现状的事实，本票**严格对齐现状、不归一**：每一行的取值就是收口前那一处
 *  的原文，只是消费方从页面 / 组壳 / 共享样式表换成了壳。值可以是 CSS 表达式
 *  （`var()` / `clamp()` / `max()` 这类）：壳只负责把它套在内容容器的自定义属性上，
 *  解算式仍是浏览器的事（`env()` 也在那里解，所以这里写令牌 `--safe-*` 而不是再写一遍
 *  `env()`）。
 *
 *  **0 的那几组不是漏了**，是那几组今天的内边距本来就长在页面自己身上（每组行上的注释
 *  逐个写了出处）。`base` 是所有宽度都成立的那一档；`wide` 只在 ≥900px 顶掉它。
 *  一个组给一个值，所以「组内几页数值不同」的组只能给 0 —— 给非零值会把组里那些
 *  **自带内边距**的页推走（04 票实测：人事组会推 4–14px），那是改版面，不是收口。 */
export const GROUP_CONTENT_INSETS = {
  // 现场八页：原来在 `admin-web/public/hygiene-admin.css` 的 `.hygiene-admin .hy-main`
  //   base：`padding: --hy-page` 那一档 + 左右各自对安全区取 max
  //   ≥900px：`padding: clamp(1.1rem,2.2vw,2rem) clamp(1.2rem,3vw,2.6rem) 2rem` 的上 / 横向
  // 左右在壳上只有一根横向度量（分两根的话 ADR 0106 决策 4 那三根就不够了），
  // 所以写成两侧取最大：`env()` 解出来左右相等是常态（横屏刘海机也是两侧同值），
  // 写成 `max(左, 右)` 与逐侧取 max 在能观察到的机器上同值。
  floor: {
    base: {
      top: 'var(--hy-page)',
      inline: 'max(var(--hy-page), var(--safe-l), var(--safe-r))',
    },
    wide: {
      top: 'clamp(1.1rem, 2.2vw, 2rem)',
      inline: 'clamp(1.2rem, 3vw, 2.6rem)',
    },
  },
  // 工作台首页（`/workbench`，`views/workbench/WorkbenchHomeView.vue` 的 `.wbh`）：页面自己
  // 那条 `--hy-page`（带 18px 兜底）与安全区取 max 的写法，两个档通用（只有一条规则）。
  home: {
    base: {
      top: 'max(var(--hy-page, 18px), var(--safe-t))',
      inline: 'max(var(--hy-page, 18px), var(--safe-l), var(--safe-r))',
    },
  },
  // 人事六页 → 0：月历（内容在 `.gB-body` 的 4/16 上）、待办 / 班次表 / 人事提醒 / 加班统计
  // （根的 `18px 16px 40px`）都自带内边距；唯一不自带的花名册吃的是组壳那条
  // `:deep(.roster-page)`（票 05 从卫生搬过来时补的），那条随本票退场、值一字不差地落在
  // `views/hygiene/HygieneRosterView.vue` 自己的样式里 —— 组里五页数值一致、花名册另一样，
  // 给组一个非零值会把那五页推走。
  hr: { base: NO_CONTENT_INSET },
  // 我的四页 → 0：今天 / 整月 / 卫生三页的根是 0（横向与上在页头 `.tTop` 与内容容器
  // `.hy-work-main` 上）、加班与补钟那页的根自己带 `12px 14px 28px`。搬它们等于把页内
  // 别的几块一起推走（今天 / 整月 / 卫生三页的页内结构会当场错位）。
  me: { base: NO_CONTENT_INSET },
  // 后勤六页 → 0：配方那五页的根是裸 `<div>`（内容内边距来自 `recipe.css` 的
  // `.site-header-inner` / `.sop-*`，票面明确不碰 recipe.css）、备货计划的根是 0
  // （内容内边距由 `.card` 自己给）。
  kitchen: { base: NO_CONTENT_INSET },
  // 员工卫生那一页（`/workbench/me/clean`）→ 0：它的内容容器是共享样式表里的
  // `.hy-work-main`，而那个容器是 `width: min(100%, 46rem); margin: 0 auto` 的居中框 ——
  // 横向内边距搬到壳上，居中框会在窄了 32px 的可用宽度里重新居中，内容整列**左移 16px**
  // （1440 实测：内容 x 368 → 352；390 上居中框不 binding，两个效果正好抵消、位移 0）。
  hygiene: { base: NO_CONTENT_INSET },
}

/** 这一页该拿到的横向与上内边距（CSS 表达式）。
 *
 *  - `group` 来自页面清单那一行（壳按当前路径查）；认不出的组给 0，不猜。
 *  - `wide`：现在是不是 ≥900px（`WIDE_INSET_MIN_WIDTH_PX`）。组表没写 `wide` 那一档的
 *    组，宽档照 base 用（它今天也就一条规则）。
 *  - `flush`：这一页**铺满**（清单里的 `flush`，A4 预览那一类）—— 组里给什么都是 0
 *    （ADR 0106 决策 5；与「自带底栏」互不隐含：底下那条栏是谁的、内边距留不留，两件事）。 */
export function contentInsetFor(group, { wide = false, flush = false } = {}) {
  if (flush) return NO_CONTENT_INSET
  const row = GROUP_CONTENT_INSETS[group]
  if (!row) return NO_CONTENT_INSET
  return wide && row.wide ? row.wide : row.base
}

/** 本屏此刻**渲染了底栏**吗 —— 只看两件事，**不看 `audience`**：
 *   - `hasOwnBottomBar`：这一页自带底栏吗（页面清单里那一行的 `ownBottomBar`）。自带的话
 *     工作台那条整条不渲染（2026-10-08 用户裁定），也就谈不上让位；
 *   - `mobile`：现在是不是手机档（底栏只在 ≤720px 渲染）。
 *
 *  为什么刻意没有 `audience`：底栏**不是按身份渲染的** —— 管理端手机档同样有那条 57px
 *  底栏（2026-10-13 基线 `floor-zones-390.png` 实测）。按身份给管理端算 0 会当场少掉
 *  一段让位，滚到底时最后一块内容压在底栏下面。 */
export function tabBarOnScreen({ hasOwnBottomBar, mobile }) {
  return !hasOwnBottomBar && !!mobile
}

/** 内容区的下 inset（px）：底栏在屏上就是「底栏高 + 安全区」，否则 0。
 *
 *  与今天等价：手机档底栏在时 57px + 0（桌面 Chromium 的 `safe-area-inset-bottom` 是 0）、
 *  自带底栏的页 0、桌面档 0（今天桌面档本来就没有下留白）。 */
export function bottomInsetPx({
  barOnScreen,
  barHeightPx = TAB_BAR_HEIGHT_PX,
  safeBottomPx = 0,
}) {
  return barOnScreen ? barHeightPx + safeBottomPx : 0
}

/** 安全区原始值（px）：从令牌层读 `--safe-b`（`theme.workbench.css:44` 那里写着 `env()`），
 *  **别处不再写一遍 `env()`**。浏览器在计算值里就把 `env()` 解成 px 了：桌面 Chromium 是
 *  `0px`，带 home indicator 的机器是那一段的高度（实测：给 Chromium 盖一层 34px 的下安全区，
 *  `getComputedStyle(html).getPropertyValue('--safe-b')` 读回 `34px`）。
 *
 *  读不到（jsdom 不做计算样式、令牌层没挂、老 WebView 把 `env()` 原样留在自定义属性里）
 *  就是 0 —— 与今天 `env(…, 0px)` 的兜底同值，不是 NaN，也不会把 `calc()` 算成假数。
 *
 *  `computedStyleOf` 可注入，方便在没有 DOM 的环境里验证解析口径。 */
export function readSafeBottomPx(
  el = typeof document === 'undefined' ? null : document.documentElement,
  computedStyleOf = typeof globalThis.getComputedStyle === 'function'
    ? globalThis.getComputedStyle.bind(globalThis)
    : null,
) {
  if (!el || !computedStyleOf) return 0
  const raw = String(computedStyleOf(el).getPropertyValue('--safe-b') || '').trim()
  const px = /^([0-9]*\.?[0-9]+)px$/.exec(raw)
  return px ? Number(px[1]) : 0
}
