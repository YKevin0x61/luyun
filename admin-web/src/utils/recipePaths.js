/** 配方五页的地址（票 07：从独立域 `/recipe*` 搬进工作台的「后勤」组）。
 *
 *  **一处定义、四处消费**：四个页面自己（页内跳转与品牌链接）、印码页生成的岗位码地址、
 *  登录回跳白名单（`utils/loginNext.js` 的 `RECIPE_READER_PATHS`）、PWA 归属判据
 *  （`utils/pwaManifest.js` 的 recipe 那一档）。各写一份字面量就是上一轮 `/hygiene` 搬家时
 *  漏改一处的老毛病 —— 二维码印出去的地址与页面实际地址对不上，扫码的人进不去。
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
/** 岗位二维码（印出来贴到岗位上）。`both`。 */
export const RECIPE_QR_PATH = '/workbench/kitchen/recipe/qr'
/** 配方管理：只给管理端那一档。 */
export const RECIPE_MANAGE_PATH = '/workbench/kitchen/recipe/manage'

/** 扫码即看的四条阅读面路径（管理页不在其中）。
 *
 *  三个地方的判据是这一份：登录回跳白名单、401 整页跳转豁免、PWA 归属。
 *  `loginNext.js` 从这里转发出去 —— 它以前自己抄了一份，跟页面清单漂过。 */
export const RECIPE_READER_PATHS = [
  RECIPE_HOME_PATH,
  RECIPE_DETAIL_PATH,
  RECIPE_PRINT_PATH,
  RECIPE_QR_PATH,
]

/** 站内路径是不是那四条阅读面之一（带 query / hash 的整串不算）。 */
export function isRecipeReaderPath(pathname) {
  return RECIPE_READER_PATHS.includes(String(pathname || ''))
}
