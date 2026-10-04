/** 管理端会话的客户端登出（工作台那一票要补的入口，票 10 会把它与员工那侧收敛成一条）。
 *
 *  今天仓库里管理端登出有四种写法，其中两种是**整页重载**（`NavBar.vue` 的
 *  `window.location.href = '/login'`、`api/client.js` 401 兜底那条）—— 票 10 要收敛的正是
 *  这一摊。工作台是子应用，登出必须走**客户端路由**：整页重载会把 Service Worker、实时
 *  连接、身份记忆之外的一切都丢掉，还要多一次冷启动（手机上尤其明显）。所以这里给
 *  工作台一条客户端登出，落点与员工那侧一样带 `?next=`（登录后回原处）。
 *
 *  **只做登出这一件事**：清会话缓存由调用方（`clearAuthStatusCache`）负责的话，就又多出
 *  一处得记得写的地方，所以放在这里 —— 会话没了而 `authStatus` 的 10 秒缓存还留着
 *  「已登录」，守卫会放行一页已经失效的会话。
 *
 *  **票 10 接手点**：这一条与 `composables/useStaffLogout.js` 是同一个动作的两半，
 *  统一时把两边都收进一个出口（客户端路由 + 带原目标），`components/workbench/
 *  WorkbenchExitButton.vue` 与 `components/staff/StaffExitButton.vue` 各自转发即可。
 */
import { clearAuthStatusCache } from '../utils/authStatus'

/**
 * 退掉管理端会话并从客户端回登录页。
 *
 * 请求失败也照样离开：会话可能已经没了（cookie 过期、后端重启过），把人卡在一个
 * 已经无权的页上比多一次跳转更坏。
 *
 * @param {import('vue-router').Router} router
 * @param {string} next 退出前所在的那一页（登录后回它）
 */
export async function logoutAdminSession(router, next) {
  try {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  } catch {
    // 会话可能已经没了；仍然把机器交出去。
  }
  clearAuthStatusCache()
  const target = String(next || '')
  await router.replace({
    path: '/login',
    query: target.startsWith('/') ? { next: target } : {},
  })
}
