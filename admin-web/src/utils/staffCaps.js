/** 员工会话的能力键探针（2026-10-14）：路由守卫判「这一页员工能不能进」要用它。
 *
 *  背景：`pageRoutes.json` 的 `audience` 是三态（`admin` / `staff` / `both`），表达不了
 *  「店长专属页，但花名册上勾了某项能力的员工也能进」。人事四页（含考勤表）与损耗分析就是这一档
 *  （清单里的可选字段 `staffCap`）。守卫要回答「这个员工有没有那一项」，而它手上只有
 *  员工会话 cookie —— 判定只能走 `/api/hygiene/staff/session`，那一份投影里带
 *  `admin_caps`（`services/identity/accounts.py` 的 `_employee_from_row`）。
 *
 *  **为什么不缓存**：能力是权限，不是展示数据。缓存 15 秒听起来省一次请求，代价是
 *  超管收回能力后员工还能点进去 15 秒（接口会 403，但界面已经开了 —— 那正是
 *  「看起来有权限」的错觉）。这里每次导航现问一次：这条路径只会在「没有管理端会话、
 *  且这一页有 staffCap」时走到，员工日常也就人事那几页与损耗分析。
 *
 *  **三态而不是布尔**（与 `utils/hygieneStaff.js` 的 `staffSessionState` 同口径）：
 *  `unknown` 表示网络层问不到（断网 / 后端重启 / 超时），调用方不该把它当「没登录」
 *  —— 弱网下把员工甩回登录页，他会以为自己的账号出了问题。
 */
import { normalizeCaps } from './adminCaps'
import { staffSession, STAFF_SESSION_PROBE_TIMEOUT_MS } from './hygieneStaff'

/**
 * 探一次「员工会话 + 他的能力键」。
 *
 * @returns {Promise<{state: 'ok'|'unauthenticated'|'unknown', caps: string[]}>}
 *   - `ok`：员工会话有效，`caps` 是归一化后的能力键（认不出的丢掉，fail-closed）；
 *   - `unauthenticated`：服务端明确说没有员工会话；
 *   - `unknown`：网络层问不到 —— 调用方别据此判越权。
 */
export async function staffCapsProbe() {
  try {
    const { authenticated, employee } = await staffSession({
      timeoutMs: STAFF_SESSION_PROBE_TIMEOUT_MS,
    })
    if (!authenticated) return { state: 'unauthenticated', caps: [] }
    return {
      state: 'ok',
      caps: normalizeCaps(employee && employee.admin_caps),
    }
  } catch (_err) {
    return { state: 'unknown', caps: [] }
  }
}
