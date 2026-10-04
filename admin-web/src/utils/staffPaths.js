/** 员工手机端的页面归属，一处定义、三处消费：
 *  - `router/index.js` 的登录守卫（票 02 起按页面清单的 audience 判定，这些页是 `staff`；
 *    未登录落 `/login?next=<目标>`，面板按 `isStaffLandingPath` 强制开员工栏）；
 *  - `loginNext.shouldSkipLoginRedirect`：管理端 client 拿到 401 时不许把员工甩去 `/login`；
 *  - `pwaManifest`：这些页挂的是**工作台**那份清单与主题色（深青墨 `#0a1719`）—— 票 09 起
 *    员工侧界面（前缀 ∪ `STAFF_PHONE_EXACT`）与工作台前缀共用一个 App，不再有单独的
 *    「青绿员工清单」。
 *
 *  **票 03 起员工三页住在工作台的「我的」组里**（今天 `/workbench/me/today`、整月
 *  `/workbench/me/month`、卫生待办 `/workbench/me/clean`）：前缀就是 `/workbench/me`。
 *  服务端**不再为它单独免墙** —— 工作台的页面壳对任一会话都放行（票 02 的
 *  `_is_workbench_page` + `_has_staff_session`），员工自己的会话就是那把钥匙；
 *  `main.py` 里旧的 `/staff` 精确条目与 `/staff/` 前缀同时删掉（工作台是「进去要登录」
 *  的页面区）。`/workbench/hr/roster`、`/workbench/floor/zones` 这些店长页与这条前缀只差一个词，
 *  别被顺手带走。
 *
 *  **名单为什么分两层**（票 02）：员工自助注册页 `/register` 与前缀下的员工页不是一回事。
 *  - `isStaffPhonePath` = 前缀 ∪ 精确：这是「员工侧界面」。PWA 清单归属看它 —— 注册页
 *    装出来必须是青绿的员工应用；管理端 client 的 401 整页跳转豁免也看它 —— 注册页上
 *    本来就没有管理端会话，甩去 `/login` 等于把新员工挡在门外。
 *  - `isStaffLandingPath` = 只要前缀：这是「登录后的落点白名单」。`/register` **绝不能**
 *    进来 —— 注册成功还在等超级管理员批准、会话也不存在，把它当落点就是把员工送进一个
 *    自己进不去的页。所以 `?next=/register` 必须回落到员工默认落点。
 */
export const STAFF_PHONE_PREFIXES = ['/workbench/me']

/** 前缀罩不住、但确实属于员工侧界面的精确路径。只给「员工侧界面」用，不进落点白名单。 */
export const STAFF_PHONE_EXACT = ['/register']

/** 员工端入口路径：员工登录后的默认落点，也是花名册页发给新店员的那个地址（票 03 把
 *  员工首页从 `/staff/today` 搬进工作台之后，旧的 `/staff/*` 已是死路径）。
 *
 *  **一处定义、三处消费** —— 落点默认值（`loginNext.resolveStaffNext`）、花名册页的
 *  二维码/可复制链接（`HygieneRosterView`）、以及 PWA 员工清单的 `start_url`（票 09）
 *  必须是同一个值。各写一个字面量就是本次缺陷：`/hygiene` 搬走时漏改了一处。
 */
export const STAFF_ENTRY_PATH = '/workbench/me/today'

function underPrefix(path, prefix) {
  return path === prefix || path.startsWith(`${prefix}/`)
}

/** 员工登录后的落点白名单：员工前缀下的页才算（`/register` 不是）。 */
export function isStaffLandingPath(pathname) {
  const path = String(pathname || '')
  return STAFF_PHONE_PREFIXES.some((prefix) => underPrefix(path, prefix))
}

/** 员工侧界面：前缀名单 ∪ 精确名单（PWA 归属、401 豁免看这个）。 */
export function isStaffPhonePath(pathname) {
  const path = String(pathname || '')
  return isStaffLandingPath(path) || STAFF_PHONE_EXACT.includes(path)
}
