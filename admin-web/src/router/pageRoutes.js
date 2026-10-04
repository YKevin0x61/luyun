/** 页面清单（`pageRoutes.json`）的读取口 —— 加一页先改那张表，再在这里/路由里消费。
 *
 *  表是**唯一来源**：`path` / `title` / `group` / `audience` / `standalone` / `public`
 *  （字段含义见 JSON 里的 `_comment`）。前端路由注册的 meta（是否独立外壳 / 是否免登录）
 *  与页面标题都从它派生；后端 `main.py` 的页面路由清单与两张页面豁免表由
 *  `tests/test_spa_page_routes.py` 对着同一张表强校验。两边一起动的时候，
 *  只要表是对的，前端与后端就不会各说各话（这正是票 01 要消掉的那类漂移）。
 *
 *  身份（`audience`）这一票只登记、还没有消费者 —— 路由守卫按它判定是票 02 的事。
 */
import table from './pageRoutes.json'

export const PAGE_ROUTES = table.pages

const BY_PATH = new Map(PAGE_ROUTES.map((row) => [row.path, row]))

/** 按路径取表里那一行；不是页面时返回 null。 */
export function pageRow(path) {
  return BY_PATH.get(String(path == null ? '' : path)) || null
}

function requireRow(path) {
  const row = pageRow(path)
  if (!row) {
    // 不兜底、不给默认值：路由不在表里就是清单漏了一页，静默放过等于让它继续漂。
    throw new Error(
      `页面清单里没有 ${path}：加一页要先改 admin-web/src/router/pageRoutes.json`,
    )
  }
  return row
}

/** 页面标题（导航与 document.title 用的名字）。 */
export function pageTitle(path) {
  return requireRow(path).title
}

/** vue-router 的 meta：独立外壳（不渲染后台导航）与免登录（守卫放行）。 */
export function pageMeta(path) {
  const row = requireRow(path)
  return { standalone: row.standalone, public: row.public }
}
