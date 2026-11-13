/** 工作台「任务」域的路径常量（2026-11-11 起）。
 *
 *  页面、导航、页内跳转与面包屑都从这一个文件取，不再各写一遍字面量 —— 三处注册
 *  （pageRoutes.json + router/index.js + main.py 的 SPA_PAGE_ROUTES）是分开改的，
 *  但前端的落点只认这里。
 */
export const TASKS_HOME_PATH = '/workbench/tasks'
export const TASKS_RECURRING_PATH = '/workbench/tasks/recurring'
export const MY_TASKS_PATH = '/workbench/staff/tasks'
