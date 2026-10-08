/** Hygiene roster copy and permission helpers. Rules stay on the server. */

import { hasCap } from './adminCaps'
import { supportsNativeCameraCapture } from './cameraCapabilities'
import { WORKBENCH_TITLE, workbenchDocumentTitle } from './workbenchCopy'

// 「卫生权限」这一档（普通员工 / 管理员）**还留着**，但它现在只是显示用的人话标签：
// 花名册拿它当下拉的选项，员工端「我的」拿它显示一行字。**判据一律看 `admin_caps`**
// （`adminCaps.js` 那十项开关）—— 同档的两个人可以开完全不同的开关，拿这个字符串判
// 就会把没放权的人一起放进来。2026-10-05 用户裁定，见 `utils/adminCaps.js` 的说明。
export const HYGIENE_PERMISSIONS = ['普通员工', '管理员']
export const HYGIENE_SHIFTS = ['白班', '夜班']
export const HYGIENE_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
export const HYGIENE_FIX_TYPES = ['卫生', '摆放', '标签']

// 这一组（卫生七页）是「工作台」里的**现场**那一组（2026-10-04 合并，票 05 按分组落位）：
// 品牌三件套跟着子系统走，页面上不再自称另一个系统。名字只写一次，这里引 `workbenchCopy` 那份。
// 花名册（票 05 起在人事组的 `/workbench/hr/roster`）**不在这张表里** —— 它是人事页，
// 外壳也跟着换了；这里只留现场组的 rail。
export const HYGIENE_BRAND_MARK = '台'
export const HYGIENE_BRAND_TITLE = WORKBENCH_TITLE
export const HYGIENE_BRAND_TAGLINE = '卫生 · 对照实拍验收'
export const HYGIENE_ADMIN_NAV = [
  { path: '/workbench/floor/zones', title: '卫生工作区', shortTitle: '工作区', icon: 'layout-grid', code: 'ZONES' },
  { path: '/workbench/floor/daily', title: '日常验收', shortTitle: '日常', icon: 'check-circle', code: 'DAILY' },
  // 仪容仪表（票 12）：按人拍，名单由排班给（休假的与没排到的不在表上）。
  { path: '/workbench/floor/attire', title: '仪容仪表', shortTitle: '仪容', icon: 'sparkles', code: 'ATTIRE' },
  { path: '/workbench/floor/deep-clean', title: '专项卫生', shortTitle: '专项', icon: 'calendar', code: 'DEEP' },
  { path: '/workbench/floor/fix', title: '整改单', shortTitle: '整改', icon: 'siren', code: 'FIX' },
  { path: '/workbench/floor/boards', title: '红黑榜', shortTitle: '榜', icon: 'star', code: 'BOARDS' },
  { path: '/workbench/floor/data', title: '数据与照片', shortTitle: '数据', icon: 'folder', code: 'ARCHIVE' },
  // 卫生趋势（2026-10-05 用户裁定）：现场那七页都是"当下"，数据页是台账 —— 没有一处回答
  // "这周比上周好还是差"，这一页就是那个回答。
  // **往现场组加页要动两处**：`router/pageRoutes.json`（清单）与**这里**（rail）。这份名单是
  // **手写**的，不像人事壳那样从清单派生（`workbenchPagesOf`）—— 只登记清单的话，页面敲 URL
  // 进得去、rail 里却没有入口，`groupDoors.test.js` 有断言盯着这个缺口。
  { path: '/workbench/floor/trend', title: '卫生趋势', shortTitle: '趋势', icon: 'trending-up', code: 'TREND' },
]

export const HYGIENE_STAFF_TABS = [
  { id: 'inbox', title: '待办', icon: 'inbox' },
  { id: 'deep', title: '专项', icon: 'calendar' },
  { id: 'fix', title: '整改', icon: 'siren' },
  { id: 'boards', title: '榜', icon: 'star' },
  { id: 'me', title: '我', icon: 'settings' },
]

export const HYGIENE_BACK_TO_ADMIN_LABEL = '后台'

/** 这一组（七页）的路径判据。
 *
 *  **不能再只看前缀**：票 05 之后工作台按组排（`/workbench/hr/*` 人事、`/workbench/floor/*`
 *  现场、`/workbench/me/*` 我的），只比 `/workbench` 前缀会把它们一起算成现场页 ——
 *  那会让它们套上"内部自己滚动"的壳（`.page-body-hygiene`），人事组与员工页的吸顶窄栏
 *  就废了。所以只认**清单里那七条**。
 *  （`/workbench/hr/roster` 票 05 起是人事页，也不在清单里；`/register` 这些员工侧页面
 *  本来就不在。）
 */
export function isHygieneAdminPath(pathname) {
  const path = String(pathname || '')
  return HYGIENE_ADMIN_NAV.some((item) => path === item.path || path.startsWith(`${item.path}/`))
}

/** 页面标题：`<页面名> · 工作台`。规格在 `workbenchCopy.js` 写一次，这里只转发
 *  （三个壳与配方阅读面共用同一份写法）。 */
export function hygieneDocumentTitle(pageName) {
  return workbenchDocumentTitle(pageName)
}

export function isAllowedHygienePermission(permission) {
  return HYGIENE_PERMISSIONS.includes(permission)
}

export function isAllowedHygieneShift(shift) {
  return HYGIENE_SHIFTS.includes(shift)
}

export function hygienePermissionLabel(permission) {
  if (permission === '管理员') return '管理员'
  return '普通员工'
}

export function hygieneShiftLabel(shift) {
  if (shift === '夜班') return '夜班'
  if (shift === '白班') return '白班'
  return '未选'
}

export function hygieneWeekdayLabel(weekday) {
  const index = Number(weekday)
  if (Number.isInteger(index) && index >= 0 && index < HYGIENE_WEEKDAYS.length) {
    return HYGIENE_WEEKDAYS[index]
  }
  return ''
}

export function hasLiveCamera() {
  if (supportsNativeCameraCapture()) return true
  return Boolean(
    typeof navigator !== 'undefined'
      && navigator.mediaDevices
      && typeof navigator.mediaDevices.getUserMedia === 'function',
  )
}

export function canAcceptFixTicket(actor, ticket, now = Date.now()) {
  if (!ticket || ticket.status !== '待验收') return false
  if (actor && actor.kind === 'super' && ticket.opener_kind === 'super') return true
  if (actor && actor.id != null && Number(actor.id) === Number(ticket.opener_id)) return true
  if (!actor) return false
  // 验别人的整改单看的是「整改单」这一项开关，不是 `permission` 那个档位（2026-10-05）：
  // 同一档的两个人可以一个开了整改单、一个没开，拿标签判就会把没开的那个人也放进来。
  // 上面两条是例外、不看开关：自己开的单子自己收（时限到了），超级管理员开的单子他向来自收。
  const canReviewFix = actor.kind === 'super' || hasCap(actor.admin_caps, 'fix')
  if (!canReviewFix) return false
  const deadline = Date.parse(ticket.deadline)
  return Number.isFinite(deadline) && now >= deadline
}

export function rosterStatusLabel(employee) {
  if (employee && employee.disabled) return '已停用'
  if (employee && employee.approved) return '已批准'
  return '待批准'
}
