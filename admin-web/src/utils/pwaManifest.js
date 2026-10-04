import { isStaffPhonePath } from './staffPaths.js'

/** 员工手机端的三个页面（`/workbench/me/today`、`/workbench/me/month`、
 *  `/workbench/me/clean`，票 03 起整体住在工作台的「我的」组里）共用一个清单：
 *  一个入口一套登录。
 *  这里必须认那条前缀 —— 否则员工在那一页装出来的是管理端应用（深色主题 + 厨务管家
 *  管理清单）。清单本身仍分三份是票 09 要收的事（三份 `scope` 都是 `/`）。
 *  哪些路径算员工端只在 `staffPaths.js` 写一次（名单还要给 401 那条用）。
 *
 *  **`/login` 一条路径装两种身份**（票 07）：装出来是哪份清单得看面板当下停在哪一栏，
 *  所以判据是「pathname + 面板身份」两件事。判据只有两处来源，本文件不重算：
 *  - 面板身份（`'staff'` / `'admin'` / 未知）由 `utils/loginNext.js` 的
 *    `resolveLoginTab` 给出，调用方算好传进来（`App.vue` 跟路由、`LoginView.vue` 跟点击）；
 *  - 路径归属仍由 `staffPaths.js` 给出。
 *  身份未知时 `/login` 兜底管理端那份（跟 `index.html` 的默认清单一致）。
 *  清单自己的 `start_url` 是员工入口 `STAFF_ENTRY_PATH`（票 07 从 `/today` 订正过来）。 */
const ROLE_MANIFESTS = [
  {
    role: 'hygiene',
    matches: (path, panelTab) =>
      isStaffPhonePath(path) || (path === '/login' && panelTab === 'staff'),
    manifest: '/pwa/manifests/hygiene.webmanifest',
    appleTouchIcon: '/pwa/icons/hygiene-192.png',
    themeColor: '#16a34a',
  },
  {
    role: 'recipe',
    matches: (pathname) => pathname === '/recipe' || pathname.startsWith('/recipe/'),
    manifest: '/pwa/manifests/recipe.webmanifest',
    appleTouchIcon: '/pwa/icons/recipe-192.png',
    themeColor: '#d97706',
  },
  {
    role: 'admin',
    matches: () => true,
    manifest: '/pwa/manifests/admin.webmanifest',
    appleTouchIcon: '/pwa/icons/admin-192.png',
    themeColor: '#0a0d16',
  },
]

/** 按路径（+ `/login` 上的面板身份）选清单。`panelTab` 非法 / 缺失按「身份未知」处理。 */
export function selectPwaManifest(pathname, panelTab) {
  const path = String(pathname || '/')
  const tab = panelTab === 'staff' || panelTab === 'admin' ? panelTab : null
  return ROLE_MANIFESTS.find((entry) => entry.matches(path, tab)) || ROLE_MANIFESTS.at(-1)
}

export function applyPwaManifest(pathname, panelTab, documentRef = globalThis.document) {
  const selected = selectPwaManifest(pathname, panelTab)
  if (!documentRef || typeof documentRef.getElementById !== 'function') return selected

  const manifestLink = documentRef.getElementById('app-manifest')
  if (manifestLink) manifestLink.setAttribute('href', selected.manifest)

  const appleIcon = documentRef.getElementById('app-apple-touch-icon')
  if (appleIcon) appleIcon.setAttribute('href', selected.appleTouchIcon)

  const themeMeta = documentRef.querySelector('meta[name="theme-color"]')
  if (themeMeta) themeMeta.setAttribute('content', selected.themeColor)

  return selected
}

export { ROLE_MANIFESTS }
