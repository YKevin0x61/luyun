/**
 * `/settings` 配置页的**分节契约**（ADR 0099）—— 壳与五个分节之间唯一的一份接口定义。
 *
 * 配置页 7 节并成 5 节（采集 / 状态 / 数据 / 系统 / 账号）之后，壳（`views/SetupView.vue`）
 * 只负责返回主页、页头状态摘要、5 项导航与分节切换；每一节的内容各自住在一个组件文件里：
 *
 *   views/settings/CollectSection.vue  采集 = 原「POS 凭据」+「运行配置」
 *   views/settings/StatusSection.vue   状态 = 原「系统健康状态」（只读）
 *   views/settings/DataSection.vue     数据 = 原「备份中心」+「数据库凭据」
 *   views/settings/SystemSection.vue   系统 = 原「系统更新」（版本 / 自检 / 迁移 / 发行目录 / 作业 / 历史 / GitHub）
 *   views/settings/AccountSection.vue  账号 = 原「账号与 API Token」
 *
 * ## 每个分节的固定契约
 *
 * - **props `active: Boolean`**：本分节现在是不是当前分节。分节**自取数据**——在 `active`
 *   翻成 `true` 时（`watch(..., { immediate: true })`）拉自己那几个只读端点，壳不再替它们
 *   调 `load*`。这样"切换分节才取数"的既有行为不变，而 5 个分节谁都不需要知道别人的存在。
 * - **emits**：只有数据节抛 `data-restored`（整库恢复之后，采集节的 POS 凭据与运行配置
 *   要在服务端数据变化后重取；壳把它翻成采集节的 `refreshKey` 计数）。其余分节**不抛事件**
 *   —— 它们不与壳交换状态。
 * - **就地反馈**：写操作的提示渲染在**自己这一节内**（分节自持的 `showAlert/clearAlert`
 *   提示条），不再有页面级的全局 alert —— 反馈贴着自己那一节的按钮，而不是飘在整页顶部。
 * - **弹窗（inject）**：四个弹窗的**框**在壳里（含 Esc 关闭），**状态与文案在分节里**；
 *   分节通过 `inject(SETTINGS_MODAL_HOST)` 拿到宿主并 `register(kind, controller)`，
 *   见下面的四个形状。两步确认是一条共用通道：任何人 `host.requestConfirm(opts, onConfirm)`
 *   即可弹出（确认状态属于 `composables/useBackupCenter.js`，本次不动 composable，
 *   所以那条通道由数据节在挂载时注册进来）。
 *
 * ## 为什么把弹窗留在壳里
 *
 * 四个手写弹窗（两步确认 / 恢复完成 / 重置数据库密码 / Token 已生成）原来都在
 * `SetupView.vue`，并且都是 `v-if` + `.modal-overlay` 的同一套皮；A19 的实测是"四个都不响应
 * Esc"。拆节时若各节各自复制一份弹窗皮与 `useEscapeClose`，就等于把"同一产品两种行为"
 * 再放大成五份。所以：**框只有一份（壳），文案与动作跟着分节走**。
 *
 * ## 注册的四个形状（kind → controller）
 *
 * 共用字段：`isOpen(): boolean`（Esc 与 `v-if` 用）、`close(): void`（取消 = 什么都不发生）。
 *
 * - `'confirm'` 两步确认（数据节注册，采集/账号节也用它）：
 *     `state`（`confirmState`：title/message/details/danger/confirmLabel/checkboxes）、
 *     `checked`（勾选状态，checkbox 的 v-model 目标）、`ready(): boolean`（必勾项是否齐）、
 *     `submit(): void`（确认按钮）、`request(opts, onConfirm)`（`requestConfirm`）
 * - `'importSuccess'` 恢复完成：`state`（`importSuccessModal`：message/sessionInvalidated）、
 *     `copy(): { title, confirmLabel }`
 * - `'dbReset'` 重置数据库密码：`state`（`dbResetConfirm`：password/error）、
 *     `copy(): { title, message, fieldLabel, placeholder, toggleLabel, confirmLabel }`、
 *     `isPasswordVisible(): boolean`、`togglePassword(): void`、`busy(): boolean`、`submit()`
 * - `'token'` Token 已生成：`state`（`tokenModal`：plaintext）、
 *     `copy(): { title, message, copyLabel, confirmLabel }`、`copyToken(): void`
 *
 * `copy()` 写成函数而不是字符串：弹窗文案里有随状态变的片段（登录用户名、env 文件路径、
 * "重新登录 / 确认"），函数在渲染时现算，Vue 才能把里面的响应式依赖追到。
 */

import { reactive } from 'vue'

/** 弹窗宿主的注入键。壳 `provide`，分节 `inject`。 */
export const SETTINGS_MODAL_HOST = Symbol('settings:modal-host')

/** 壳能渲染的四种弹窗；`register` 只认这四个键。 */
const MODAL_KINDS = ['confirm', 'importSuccess', 'dbReset', 'token']

/**
 * 弹窗宿主：四个槽位一开始都是 `null`，分节挂载时注册进来（`reactive` 是为了让壳在
 * 注册落地后重渲染出弹窗——分节的 `setup()` 在壳的同一次渲染里跑，注册是异步于那次
 * 渲染的）。
 */
export function createSectionModalHost() {
  const host = reactive({
    confirm: null,
    importSuccess: null,
    dbReset: null,
    token: null,
  })

  /** 注册（重复注册即替换）。未知 kind 直接忽略并留一条警告，便于定位写错的分节。 */
  function register(kind, controller) {
    if (!MODAL_KINDS.includes(kind)) {
      console.warn(`[settings] 未知弹窗种类：${kind}`)
      return
    }
    host[kind] = controller
  }

  /**
   * 两步确认的共用入口：转发给当前注册的两步确认控制器。调用方只给"要确认什么"
   * 与"确认后做什么"，弹窗本体由壳渲染。
   */
  function requestConfirm(opts, onConfirm) {
    host.confirm?.request(opts, onConfirm)
  }

  // 挂到同一个 reactive 对象上再返回：壳读 `modals.confirm` 时才有依赖可追，
  // 否则 `{ ...host }` 会把注册前的 null 拷成一次性快照。
  return Object.assign(host, { register, requestConfirm })
}
