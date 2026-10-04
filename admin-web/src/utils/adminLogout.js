/** 管理端会话的客户端登出 —— **全仓唯一的一处实现**（票 10 收敛掉的那一摊）。
 *
 *  以前管理端登出有四种写法，其中三种是**整页重载**：`NavBar.vue` 的
 *  `window.location.href = '/login'`、`useAccountSettings.js`、`useBackupCenter.js`，
 *  只有员工那侧走客户端路由。整页重载会把 Service Worker、实时连接、身份记忆之外
 *  的一切都丢掉，还要多一次冷启动（手机上尤其明显），而且三种写法都把原目标丢了。
 *  现在四个入口（后台导航 / 配置页 / 备份中心 / 工作台与配方阅读面的退出按钮，后者
 *  经 `composables/useWorkbenchLogout.js` 按身份派发）都调这一个动作。
 *
 *  **只做登出这一件事**：清会话缓存放在这里而不是交给调用方 —— 否则又多出一处得记得
 *  写的地方。会话没了而 `authStatus` 的 10 秒缓存还留着「已登录」，守卫会放行一页
 *  已经失效的会话。
 *
 *  员工那一档不走这里（两套会话各走各的门，ADR 0091）：`composables/useStaffLogout.js`
 *  打的是员工登出端点，还要清本机上传队列、有照片时先问一句。
 */
import { clearAuthStatusCache } from '../utils/authStatus'

/**
 * 退掉管理端会话并从客户端回登录页。
 *
 * 请求失败也照样离开：会话可能已经没了（cookie 过期、后端重启过），把人卡在一个
 * 已经无权的页上比多一次跳转更坏。
 *
 * @param {import('vue-router').Router} router SPA 的 router（客户端导航，不整页重载）
 * @param {string} [next] 退出前所在的那一页；不给就用 router 当前那一页（登录后回它）
 */
export async function logoutAdminSession(router, next) {
  try {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  } catch {
    // 会话可能已经没了；仍然把机器交出去。
  }
  clearAuthStatusCache()
  const target = String(next || router.currentRoute.value.fullPath || '')
  await router.replace({
    path: '/login',
    query: target.startsWith('/') ? { next: target } : {},
  })
}
