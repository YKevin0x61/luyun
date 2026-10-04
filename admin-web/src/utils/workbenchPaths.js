/** 工作台前缀：**一处定义**，服务端口径逐字对齐 `main.py` 的 `_is_workbench_page`。
 *
 *  谁是工作台的一员、谁的清单与 Service Worker 归工作台，都从这一个判据派生
 *  （票 09）。写法刻意是「等于根 **或** 带斜杠的前缀」而不是裸 `startsWith`：
 *  裸写法会把 `/workbenchxyz` 这种不存在的路径也当成工作台 —— 服务端页面墙放宽
 *  凭据、前端挂错清单都是从这儿开始漂的。
 *
 *  消费方：
 *  - `utils/pwaManifest.js`：归属判据（清单 / 图标 / 主题色 / Service Worker）；
 *  - `utils/workbenchCopy.js`：`WORKBENCH_HOME`（首页落点）引用这里，不再自己抄字面量。
 */
export const WORKBENCH_ROOT = '/workbench'

/** 这条路径是不是工作台前缀下的（根本身算）。 */
export function isWorkbenchPath(pathname) {
  const path = String(pathname || '')
  return path === WORKBENCH_ROOT || path.startsWith(`${WORKBENCH_ROOT}/`)
}
