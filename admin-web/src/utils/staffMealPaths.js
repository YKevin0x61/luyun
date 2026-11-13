/** 员工餐采购页（ADR 0135）：后勤组的一页 —— 月历 + 点某天进单。
 *
 * 落点常量与页面里的 router-link 用同一份（与 `recipePaths.js` / `wastagePaths.js`
 * 同一约定）；导航表（`utils/workbenchNav.js`）也从这里引，别在别处再写字面量。
 */
export const STAFF_MEAL_PATH = '/workbench/kitchen/staff-meal'
