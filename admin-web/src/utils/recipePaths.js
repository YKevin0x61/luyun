/** 配方五页的地址（票 07：从独立域 `/recipe*` 搬进工作台的「后勤」组）。
 *
 *  **一处定义、多处消费**：配方五页自己（页内跳转、顶栏品牌链接、印码页拼岗位码地址）、
 *  登录回跳白名单（`utils/loginNext.js` 从 `RECIPE_READER_PATHS` / `isRecipeReaderPath`
 *  转发，不另写字面量）。各写一份字面量就是上一轮 `/hygiene` 搬家时漏改一处的老毛病 ——
 *  二维码印出去的地址与页面实际地址对不上，扫码的人进不去。
 *
 *  **不再被这一份派生**的两个旧消费点（都改了判据，别再照旧注释推理）：
 *  - PWA 归属：票 09 收敛成只按路径分家（`utils/pwaManifest.js`，`/workbench/*` 归工作台）；
 *  - 401 整页跳转豁免：票 10 起从页面清单派生（`router/pageRoutes.js` 的
 *    `skipsAdminLoginRedirect`，判据是那一行的 `audience`）。
 *
 *  页面清单（`router/pageRoutes.json`）仍是路径的唯一来源，且由
 *  `tests/test_spa_page_routes.py` 双向盯着；这里放的是**给运行时用的那份常量**
 *  （页面里写 `router-link` 时不该再抄一遍字面量）。
 */

/** 阅读面的落点：岗位列表（也是四个阅读页顶栏那颗「配方列表」）。`both`。 */
export const RECIPE_HOME_PATH = '/workbench/kitchen/recipe'
/** 沉浸阅读页（扫码扫到的就是这一页，带 `?slug=`）。`both`。 */
export const RECIPE_DETAIL_PATH = '/workbench/kitchen/recipe/detail'
/** A4 打印预览。`both`。 */
export const RECIPE_PRINT_PATH = '/workbench/kitchen/recipe/print'
/** 岗位二维码（印出来贴到岗位上）。**只给管理端那一档** —— 票 04 起它不再是「扫码即看的
 *  阅读面」：印码是店长布置岗位码的动作，员工是**扫码**的那一方（扫到的是上面那条 detail）。
 *  常量本身留着：列表页那颗「岗位二维码」入口靠它生成地址。 */
export const RECIPE_QR_PATH = '/workbench/kitchen/recipe/qr'
/** 配方管理：只给管理端那一档。 */
export const RECIPE_MANAGE_PATH = '/workbench/kitchen/recipe/manage'

/** 扫码即看的**三条**阅读面路径（印码页与管理页不在其中）。
 *
 *  这一份服务登录回跳白名单（`loginNext.js` 从这里转发，不另写字面量）。印码页票 04 起
 *  摘出去了 —— 把它算进阅读面，未登录访问它就会默认开员工栏（`resolveLoginTab` 的第二条
 *  判据），而印码是店长的活。页面清单里对应那三页的 `audience` 是同一个事实的另一面
 *  （都是 `both`：员工扫码进得去）。 */
export const RECIPE_READER_PATHS = [
  RECIPE_HOME_PATH,
  RECIPE_DETAIL_PATH,
  RECIPE_PRINT_PATH,
]

/** 站内路径是不是那三条阅读面之一（带 query / hash 的整串不算）。 */
export function isRecipeReaderPath(pathname) {
  return RECIPE_READER_PATHS.includes(String(pathname || ''))
}
