/** 员工手机端的页面归属，一处定义、三处消费：
 *  - `router/index.js` 的 `staffAuth` 守卫（未登录落 `/hygiene/login`）；
 *  - `loginNext.shouldSkipLoginRedirect`：管理端 client 拿到 401 时不许把员工甩去 `/login`；
 *  - `pwaManifest`：这些页挂的是员工端那份清单与主题色（青绿），不是管理端（深色）。
 *
 * 服务端照着改的是 `main.py` 的 `HTML_AUTH_EXACT` / `HTML_AUTH_PREFIXES`
 * （`/hygiene`、`/hygiene/...`、`/today`、`/today/`）—— 两边要一起动。
 * `/hygiene-roster`、`/hygiene-zones` 这些是**管理端**页面，别被前缀顺手带走。
 */
export const STAFF_PHONE_PREFIXES = ['/hygiene', '/today']

export function isStaffPhonePath(pathname) {
  const path = String(pathname || '')
  return STAFF_PHONE_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  )
}
