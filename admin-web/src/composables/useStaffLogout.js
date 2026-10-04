import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useImageUploadQueueStore } from '../stores/imageUploadQueue'
import { useWorkbenchIdentityStore } from '../stores/workbenchIdentity'
import { staffRequest } from '../utils/hygieneStaff'

/**
 * 员工端退出登录：三张员工页共用一套（由 `components/staff/StaffExitButton.vue`
 * 挂在各自顶栏的右上角处）。
 *
 * 退出会清掉本机上传队列（同一台手机换人用必须清），但店员可能是手滑点到的：
 * 队列里还有照片时先问一句，别让他白拍一轮。
 *
 * 退出后落 `/login` 的员工栏，并把当前页作为 `next` 带上——重新登录回原处。
 * （面板那侧 `resolveStaffNext` 只认员工端路径，`/workbench/me/*` 在白名单里。）
 */
export function useStaffLogout() {
  const route = useRoute()
  const router = useRouter()
  const imageUploads = useImageUploadQueueStore()
  const identityStore = useWorkbenchIdentityStore()

  const loggingOut = ref(false)
  const confirmOpen = ref(false)
  const queuedCount = computed(
    () => imageUploads.activeTasks.length + imageUploads.failedTasks.length,
  )

  /** 点按钮：队列里有照片就先弹确认，否则直接退。 */
  function ask() {
    if (loggingOut.value) return
    if (queuedCount.value) {
      confirmOpen.value = true
      return
    }
    void logout()
  }

  function cancel() {
    confirmOpen.value = false
  }

  async function logout() {
    if (loggingOut.value) return
    confirmOpen.value = false
    loggingOut.value = true
    imageUploads.clearTasksByTransport('staff')
    try {
      await staffRequest('/api/hygiene/staff/logout', { method: 'POST' })
    } catch {
      // 会话可能已经没了；仍然把手机交出去（离开员工端）。
    }
    await router.replace({ path: '/login', query: { next: route.fullPath } })
    // 工作台身份的结论跟着会话作废（同一个动作的另一档在 `useWorkbenchLogout` 里做）：
    // 换个人登录是客户端路由、不整页刷新，而身份的探针只在"还没探过"时开场 ——
    // 不清的话，下一个人进工作台时顶栏还挂着上一个人的档与姓名。
    identityStore.reset()
  }

  return { loggingOut, confirmOpen, queuedCount, ask, cancel, logout }
}
