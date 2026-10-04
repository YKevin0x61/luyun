/** 工作台的退出入口：**按此刻的身份**把这一下交给对应的那条登出（票 06）。
 *
 *  工作台是子应用、页页都是独立外壳（`standalone`），原先这十一页一个退出按钮都没有 ——
 *  员工在自己的手机上进得来、退不出去，店长也一样（spec 故事 37 / 47）。
 *
 *  **行为只有一处**：员工那一档原样交给 `composables/useStaffLogout.js`（上传队列的确认
 *  与清理都在它那儿），店长那一档交给 `utils/adminLogout.js` 的客户端登出 —— 这个
 *  composable 只是「身份 → 哪条出口」的唯一映射。工作台的按钮
 *  （`components/workbench/WorkbenchExitButton.vue`）与员工端原有的那颗
 *  （`components/staff/StaffExitButton.vue`，它直接用 `useStaffLogout`）都只是皮。
 *  票 10 会把四处登出收敛成一条路径，届时改这两个文件，不用再翻五个壳。
 *
 *  两套会话各走各的门（ADR 0091 的立场：不合并会话、不做账号形态判别）：
 *  - 店长那一档 → `utils/adminLogout.js` 的客户端登出（POST `/api/auth/logout` + 路由跳转）；
 *  - 员工那一档 → `composables/useStaffLogout.js`（它还要清本机上传队列、有照片时先问一句）。
 *
 *  身份还没探出来（`null`）时**什么也不做**：那会儿还不知道该退哪一套会话，
 *  猜错就是把另一套会话留着、人却以为退了。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWorkbenchIdentityStore } from '../stores/workbenchIdentity'
import { logoutAdminSession } from '../utils/adminLogout'
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
    if (identity.value === 'staff') {
      staff.ask()
      return
    }
    void exit()
  }

  /** 真退（员工那侧的确认框回调也接这儿）。 */
  async function exit() {
    if (identity.value === 'staff') {
      await staff.logout()
      return
    }
    if (identity.value === 'super') {
      await logoutAdminSession(router, route.fullPath)
    }
  }

  return {
    identity,
    ask,
    exit,
    /** 员工那一档的「还有照片没传完」确认框（店长那侧恒为 false）。 */
    confirmOpen: computed(() => identity.value === 'staff' && staff.confirmOpen.value),
    queuedCount: staff.queuedCount,
    cancel: staff.cancel,
    loggingOut: staff.loggingOut,
  }
}
