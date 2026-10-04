/** 工作台与配方阅读面的退出入口：**按此刻的身份**把这一下交给对应的那条登出。
 *
 *  工作台是子应用、页页都是独立外壳（`standalone`），原先这些页面一个退出按钮都没有 ——
 *  员工在自己的手机上进得来、退不出去，店长也一样（spec 故事 37 / 47）。配方阅读面
 *  四个沉浸页同理，挂的是 `components/recipe/RecipeExitButton.vue`。
 *
 *  **行为只有一处**：员工那一档原样交给 `composables/useStaffLogout.js`（上传队列的确认
 *  与清理都在它那儿），店长那一档交给 `utils/adminLogout.js` 的客户端登出 —— 票 10 之后
 *  那一处就是**全仓唯一的管理端登出实现**（后台导航、配置页、备份中心也都调它），
 *  这个 composable 只是「身份 → 哪条出口」的唯一映射。三颗按钮
 *  （工作台 / 员工端 / 配方阅读面）都只是皮。
 *
 *  两套会话各走各的门（ADR 0091 的立场：不合并会话、不做账号形态判别）：
 *  - 店长那一档 → `utils/adminLogout.js` 的客户端登出（POST `/api/auth/logout` + 路由跳转）；
 *  - 员工那一档 → `composables/useStaffLogout.js`（它还要清本机上传队列、有照片时先问一句）。
 *
 *  身份还没探出来（`null`）时**什么也不做**：那会儿还不知道该退哪一套会话，
 *  猜错就是把另一套会话留着、人却以为退了。
 *
 *  **退完要把工作台身份的结论作废**（`stores/workbenchIdentity` 的 `reset()`）：探针的
 *  结论在会话消失的那一刻就不成立了，而切换器只在"还没探过"时开场探针 —— 不清的话，
 *  同一页应用里换个人登录（客户端路由，不整页刷新）再进工作台，顶栏会一直挂着上一个人
 *  的档。两档的作废点各在自己那条出口上：店长这一档在这里，员工那一档在
 *  `useStaffLogout.logout()`（理由见下面 `exit()` 里的注释）。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWorkbenchIdentityStore } from '../stores/workbenchIdentity'
import { logoutAdminSession } from '../utils/adminLogout'
import { IDENTITY_ADMIN, IDENTITY_STAFF } from '../utils/workbenchIdentity.js'
import { useStaffLogout } from './useStaffLogout'

export function useWorkbenchLogout() {
  const route = useRoute()
  const router = useRouter()
  const identityStore = useWorkbenchIdentityStore()
  const identity = computed(() => identityStore.identity)

  // 员工那半整条复用（上传队列的确认框与清理由它负责，别在这儿再写一遍）。
  const staff = useStaffLogout()

  /** 点了退出：员工那一档且队列里还有照片 → 先弹确认；其余情况直接退。
   *  `ask()` 会把「要不要确认」与「真退」分开，店长那侧没有队列，直接走 exit。 */
  function ask() {
    if (identity.value === IDENTITY_STAFF) {
      staff.ask()
      return
    }
    void exit()
  }

  /** 真退（员工那侧的确认框回调也接这儿）。 */
  async function exit() {
    if (identity.value === IDENTITY_STAFF) {
      // 员工那半的「身份结论作废」落在 `useStaffLogout.logout()` 里：`ask()` 那条
      // 「队列里没有照片就直接退」的路不经过这里（它直接调 `staff.ask()`），作废只有
      // 写在那边才两条路都盖得住。
      await staff.logout()
      return
    }
    if (identity.value === IDENTITY_ADMIN) {
      await logoutAdminSession(router, route.fullPath)
      identityStore.reset()
    }
  }

  return {
    identity,
    ask,
    exit,
    /** 员工那一档的「还有照片没传完」确认框（店长那侧恒为 false）。 */
    confirmOpen: computed(() => identity.value === IDENTITY_STAFF && staff.confirmOpen.value),
    queuedCount: staff.queuedCount,
    cancel: staff.cancel,
    loggingOut: staff.loggingOut,
  }
}
