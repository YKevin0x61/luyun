/** 配方页面里「只有管理端才有」的那几个入口的判据（票 07）。
 *
 *  **票 07 之前**：`RecipeDetailView` 用 `/api/auth/status` 的 `logged_in` 算 `canEdit`，
 *  四个阅读页的顶栏还无条件渲染「管理后台」与「配方管理」两个入口 —— 未登录的扫码用户
 *  一点就被弹去登录页（navigation-audit 条目 4），而「谁能编辑配方」只由一个前端登录
 *  开关决定、界面上没有来源可查（条目 11）。
 *
 *  **现在**：判据是**工作台身份**（`stores/workbenchIdentity`）—— 读取、记忆、切换都在
 *  那一处，页面不再各自问一次登录状态。`identity === IDENTITY_ADMIN` 就是「这一档是
 *  超级管理员（共享账号）」。
 *
 *  两个刻意的取舍：
 *  - **探针没出结论时不显示**（`identity` 是 `null`）：宁可先不显示，也不给扫码进来的人
 *    一个点不通、还会把他弹去登录页的入口。管理端那几颗按钮晚半拍出现，比点错一次便宜。
 *  - **只降不升照旧**：这台设备记住的是员工档、而员工会话还在时，即便管理端会话也挂着
 *    也不显示管理入口（`resolveWorkbenchIdentity` 的规则）。要切回超级管理员，去套着
 *    工作台外壳的 `/workbench/kitchen/recipe` 列表页，顶栏那颗切换器在那儿。
 *    （员工会话已经不在时记忆值失去载体 —— 身份按唯一可用的那一档走，见那个函数的注释。）
 */
import { computed } from 'vue'
import { useWorkbenchIdentityStore } from '../stores/workbenchIdentity'
import { IDENTITY_ADMIN } from '../utils/workbenchIdentity'

export function useRecipeAdmin() {
  const identityStore = useWorkbenchIdentityStore()
  // 阅读 / 打印 / 印码三页是沉浸页，不套工作台外壳 —— 没有人替它们探会话，探针在这里
  // 开场（幂等：正在探的时候直接返回，同一页里几个组件共用这一份结果）。
  void identityStore.refresh()

  const isAdmin = computed(() => identityStore.identity === IDENTITY_ADMIN)
  return { identityStore, isAdmin }
}
