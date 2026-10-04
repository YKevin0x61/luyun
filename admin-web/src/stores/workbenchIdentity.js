/** 工作台身份的运行时状态（票 04）：探针两套会话 → 定档 → 记忆 → 供顶栏切换。
 *
 *  判定规则与判据表在 `utils/workbenchIdentity.js`（纯函数，只降不升）；这里只负责
 *  「怎么知道会话有没有效」与「把结果放进响应式状态、按需落盘」。外壳与切换器都读它，
 *  于是两处看到的永远是同一个「此刻的身份」。
 *
 *  **这里不做权限判断**：会话探针只影响顶栏与导航面。页面能不能打开仍由路由守卫
 *  （`meta.audience`）与服务端页面墙说了算 —— 本票一个字都不改那两条链。
 */
import { defineStore } from 'pinia'
import { isLoggedIn } from '../utils/authStatus'
import { staffRequest, staffSessionState, STAFF_SESSION_PROBE_TIMEOUT_MS } from '../utils/hygieneStaff'
import {
  IDENTITY_ADMIN,
  IDENTITY_STAFF,
  loadWorkbenchIdentity,
  resolveWorkbenchIdentity,
  saveWorkbenchIdentity,
} from '../utils/workbenchIdentity'

/** 两套会话的有效性。`null` = 还没探过（页面壳先渲染，别把未探当成没有）。 */
function unknownState() {
  return { admin: null, staff: null }
}

export const useWorkbenchIdentityStore = defineStore('workbenchIdentity', {
  state: () => ({
    /** 此刻显示哪一档：'super' | 'staff' | null（认不出）。 */
    identity: null,
    /** 此刻哪些档**点得动**（= 有效会话给了哪几档）。 */
    available: [],
    /** 本机记住的那一档（落盘的值）。 */
    remembered: null,
    /** 探针结果：`null` 未探 / `true` 有效 / `false` 无效。 */
    sessions: unknownState(),
    /** 刚发生的降级提示（一次性：用户关掉、或切档之后清掉，不常驻）。 */
    notice: null,
    /** 员工姓名（拿不到就是空串，界面据此不显示空括号）。 */
    staffName: '',
    probing: false,
  }),
  getters: {
    /** 探针是否已给出结论（两套会话都有结果）。 */
    probed(state) {
      return state.sessions.admin !== null && state.sessions.staff !== null
    },
    canUseAdmin(state) {
      return state.available.includes(IDENTITY_ADMIN)
    },
    canUseStaff(state) {
      return state.available.includes(IDENTITY_STAFF)
    },
  },
  actions: {
    /**
     * 探一次两套会话，重新定档并落盘。
     *
     * 两套探针并发、各自 fail-closed（探不到按无效算），与守卫的判定同一套工具。
     * 只有**记忆的是超级管理员、那个会话失效、员工会话还在**时才出降级提示。
     */
    async refresh() {
      if (this.probing) return
      this.probing = true
      try {
        const [adminValid, staffState] = await Promise.all([
          isLoggedIn().catch(() => false),
          staffSessionState().catch(() => 'unauthenticated'),
        ])
        // `unknown`（断网 / 超时）不当作「确实没登录」，但也不够格当有效会话：
        // 定档按无效算，避免弱网下把员工的名义身份顶成超级管理员。
        const staffValid = staffState === 'ok'

        const next = resolveWorkbenchIdentity({
          adminValid,
          staffValid,
          saved: loadWorkbenchIdentity(),
        })

        this.sessions = { admin: adminValid, staff: staffValid }
        this.identity = next.identity
        this.available = next.available
        if (next.remembered !== this.remembered) {
          this.remembered = next.remembered
          if (next.remembered) saveWorkbenchIdentity(next.remembered)
        }
        if (next.downgraded) {
          this.notice = '管理端的登录已过期，已按员工身份显示。'
        }

        if (staffValid && !this.staffName) await this.loadStaffName()
      } finally {
        this.probing = false
      }
    },

    /**
     * 用户显式切档（顶栏那两格）。只改视图与记忆，**不改权限**：两套 cookie 与两条
     * 守卫链一个字节都不动。
     *
     * 点不动的那一档不做任何事 —— 界面上它本来就是禁用的，这里是同一条规矩的第二道。
     *
     * @param {'super'|'staff'} identity
     * @returns {boolean} 是否真的切过去了
     */
    switchTo(identity) {
      if (identity !== IDENTITY_ADMIN && identity !== IDENTITY_STAFF) return false
      if (!this.available.includes(identity)) return false
      this.identity = identity
      this.notice = null
      if (this.remembered !== identity) {
        this.remembered = identity
        saveWorkbenchIdentity(identity)
      }
      return true
    },

    dismissNotice() {
      this.notice = null
    },

    /** 会话没了之后把结论作废（登出时调）。
     *
     *  身份是**探针 + 记忆**算出来的结论，探针的结论在会话消失的那一刻就不成立了；
     *  不清的话，同一页应用里换个人登录再进工作台，顶栏还挂着上一个人的档 ——
     *  `WorkbenchIdentitySwitcher` 只在 `!probed` 时开场探针，而 `probed` 还是 true，
     *  于是那次纠正根本不会发生（除非整页刷新）。清成"还没探"是唯一诚实的值。
     *
     *  **不动 `remembered`**：那是这台设备记住的选择（spec 故事 4），不是会话结论。
     *  下一次 `refresh()` 照旧按它判"只降不升"。 */
    reset() {
      this.identity = null
      this.available = []
      this.sessions = unknownState()
      this.notice = null
      this.staffName = ''
    },

    /** 员工姓名（`/api/hygiene/staff/me` 的 `employee.name`）。拿不到就留空，不抛错。 */
    async loadStaffName() {
      try {
        const data = await staffRequest('/api/hygiene/staff/me', {
          timeoutMs: STAFF_SESSION_PROBE_TIMEOUT_MS,
        })
        // 与登录页同一套兜底：没填名字时用手机号，两者都没有才算拿不到。
        this.staffName = String(data?.employee?.name || data?.employee?.phone || '')
      } catch {
        this.staffName = ''
      }
      return this.staffName
    },
  },
})
