import { STAFF_PHONE_EXACT } from './staffPaths.js'
import { WORKBENCH_ROOT, isWorkbenchPath } from './workbenchPaths.js'

/** App 归属判据（票 09 收敛）：**两份清单，按路径分家**。
 *
 *  今天之前是三份清单、且 admin 与 hygiene 两份的 `scope` 都是 `/`、只靠 `id` 区分
 *  —— 一个 scope 里塞两个 App，装出来哪个全看当下页面挂的是哪份 `<link rel=manifest>`，
 *  于是真正的工作台（`/workbench/*`）装出来是深色的「厨务管家管理」。实测记录见
 *  `.scratch/workbench-subapp/issues/09-workbench-pwa.md` 票尾。
 *
 *  收敛后的判据只有两档：
 *  - **工作台**：`/workbench` 与 `/workbench/*`（`workbenchPaths.js`，与服务端页面墙同一个
 *    口径），外加员工侧界面的精确条目（`/register`，见下）。清单 `start_url` 与 `scope`
 *    都锁在 `/workbench`，Service Worker 也从 `/workbench/sw.js` 提供（scope `/workbench`）。
 *  - **管理端**：其余全部 —— `/login` 与 `/`、`/admin`、`/sales-report`、`/wecom-push`、
 *    `/logs`、`/settings` 这一个系统管理面（`/register` 除外，它归工作台）。清单 `scope`
 *    仍是 `/`（它要覆盖管理面那些平铺路径），但工作台页面不再挂它。
 *
 *  `/register`（员工自助注册）不在工作台前缀里，但它是员工侧界面（`staffPaths.js` 的
 *  `STAFF_PHONE_EXACT`）：新人在注册页「添加到主屏幕」，装出来必须是员工接下来要用的
 *  那个应用（打开即 `/workbench`，按身份渲染），挂成深色管理端只会让人找不到自己该去哪。
 *
 *  `/login` **不再**随面板栏位换清单（票 07 的行为）：它一条路径装两种身份，判据吃不下
 *  「当下停在哪一栏」这件事，票 09 按票面口径把它整个划给管理端。
 */
const ROLE_MANIFESTS = [
  {
    role: 'workbench',
    matches: (path) => isWorkbenchPath(path) || STAFF_PHONE_EXACT.includes(path),
    manifest: '/pwa/manifests/workbench.webmanifest',
    appleTouchIcon: '/pwa/icons/workbench-192.png',
    themeColor: '#0a1719',
    // Service Worker 从工作台路径下提供。脚本在 `/workbench/sw.js` → 默认最大 scope 是
    // `/workbench/`，而 `/workbench`（无尾斜杠，start_url 与首页）**不在它的路径前缀里**
    // —— 服务端必须发 `Service-Worker-Allowed: /workbench`，注册才不会被 SecurityError 拒掉
    // （实测：.scratch/workbench-subapp/pwa-lab/exp-b-sw-scope.json）。
    serviceWorker: '/workbench/sw.js',
    serviceWorkerScope: WORKBENCH_ROOT,
  },
  {
    role: 'admin',
    matches: () => true,
    manifest: '/pwa/manifests/admin.webmanifest',
    appleTouchIcon: '/pwa/icons/admin-192.png',
    themeColor: '#0a0d16',
    serviceWorker: '/sw.js',
    serviceWorkerScope: '/',
  },
]

/** 按路径选 App（票 09 起判据只看路径；多的参数 JS 直接忽略，老调用方不会炸）。 */
export function selectPwaManifest(pathname) {
  const path = String(pathname || '/')
  return ROLE_MANIFESTS.find((entry) => entry.matches(path)) || ROLE_MANIFESTS.at(-1)
}

export function applyPwaManifest(pathname, documentRef = globalThis.document) {
  const selected = selectPwaManifest(pathname)
  if (!documentRef || typeof documentRef.getElementById !== 'function') return selected

  const manifestLink = documentRef.getElementById('app-manifest')
  if (manifestLink) manifestLink.setAttribute('href', selected.manifest)

  const appleIcon = documentRef.getElementById('app-apple-touch-icon')
  if (appleIcon) appleIcon.setAttribute('href', selected.appleTouchIcon)

  const themeMeta = documentRef.querySelector('meta[name="theme-color"]')
  if (themeMeta) themeMeta.setAttribute('content', selected.themeColor)

  return selected
}

export { ROLE_MANIFESTS, WORKBENCH_ROOT }
