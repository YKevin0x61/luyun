/** 备货计划那一页的地址（票 08：从管理后台的 `/prep-plan` 搬进工作台的「后勤」组）。
 *
 *  **一处定义、四处消费**：工作台导航里那一项（`utils/workbenchNav.js`）、页面清单
 *  （`router/pageRoutes.json`，唯一的路径来源）、登录回跳白名单（`utils/loginNext.js`
 *  的 `LEGACY_NEXT_PATHS`）。各家再写一份字面量就是配方搬家时那种"二维码印出去的地址
 *  与页面实际地址对不上"的老毛病。
 *
 *  与 `recipePaths.js` 分开是**有意**的：配方那五页有自己的清单（印码页生成地址、
 *  PWA 归属判据都要用），备货计划只有一页，混进去只会让下次改配方的人以为它也归配方管。
 */

/** 后勤组里备货计划那一页。`both`：管理端与员工都进得去（员工那一档是只读的）。 */
export const PREP_PLAN_PATH = '/workbench/kitchen/prep-plan'
