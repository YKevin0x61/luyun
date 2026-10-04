import { describe, expect, it } from 'vitest'
import {
  HYGIENE_ADMIN_NAV,
  HYGIENE_BACK_TO_ADMIN_LABEL,
  HYGIENE_BRAND_MARK,
  HYGIENE_BRAND_TAGLINE,
  HYGIENE_BRAND_TITLE,
  HYGIENE_STAFF_TABS,
  HYGIENE_PERMISSIONS,
  HYGIENE_SHIFTS,
  HYGIENE_WEEKDAYS,
  HYGIENE_FIX_TYPES,
  canAcceptFixTicket,
  hygieneDocumentTitle,
  hygienePermissionLabel,
  hygieneShiftLabel,
  hygieneWeekdayLabel,
  isAllowedHygienePermission,
  isAllowedHygieneShift,
  isHygieneAdminPath,
  rosterStatusLabel,
} from '../hygieneCopy.js'

describe('hygieneCopy', () => {
  it('卫生权限只有普通员工或管理员，没有超级管理员', () => {
    expect(HYGIENE_PERMISSIONS).toEqual(['普通员工', '管理员'])
    expect(isAllowedHygienePermission('普通员工')).toBe(true)
    expect(isAllowedHygienePermission('管理员')).toBe(true)
    expect(isAllowedHygienePermission('超级管理员')).toBe(false)
    expect(isAllowedHygienePermission('领班')).toBe(false)
  })

  it('权限标签不把职位当成权限', () => {
    expect(hygienePermissionLabel('普通员工')).toBe('普通员工')
    expect(hygienePermissionLabel('管理员')).toBe('管理员')
    expect(hygienePermissionLabel('领班')).toBe('普通员工')
  })

  it('花名册状态：待批准、已批准、停用后仍能看见', () => {
    expect(rosterStatusLabel({ approved: false, disabled: false })).toBe('待批准')
    expect(rosterStatusLabel({ approved: true, disabled: false })).toBe('已批准')
    expect(rosterStatusLabel({ approved: true, disabled: true })).toBe('已停用')
  })

  it('班次只有白班或夜班，没选显示未选', () => {
    expect(HYGIENE_SHIFTS).toEqual(['白班', '夜班'])
    expect(isAllowedHygieneShift('白班')).toBe(true)
    expect(isAllowedHygieneShift('夜班')).toBe(true)
    expect(isAllowedHygieneShift('早班')).toBe(false)
    expect(hygieneShiftLabel('白班')).toBe('白班')
    expect(hygieneShiftLabel('夜班')).toBe('夜班')
    expect(hygieneShiftLabel(null)).toBe('未选')
    expect(hygieneShiftLabel('')).toBe('未选')
  })

  it('专项卫生按周一到周日配，下标 0 是周一', () => {
    expect(HYGIENE_WEEKDAYS).toEqual(['周一', '周二', '周三', '周四', '周五', '周六', '周日'])
    expect(hygieneWeekdayLabel(0)).toBe('周一')
    expect(hygieneWeekdayLabel(6)).toBe('周日')
    expect(hygieneWeekdayLabel(9)).toBe('')
  })

  it('整改类型只有卫生摆放标签，时限未到只有开单人能验', () => {
    expect(HYGIENE_FIX_TYPES).toEqual(['卫生', '摆放', '标签'])
    const ticket = {
      status: '待验收',
      opener_id: 20,
      opener_kind: 'staff',
      deadline: '2026-09-13T12:00:00+08:00',
    }
    const before = Date.parse('2026-09-13T11:59:00+08:00')
    const after = Date.parse('2026-09-13T12:00:00+08:00')
    expect(canAcceptFixTicket({ id: 20, permission: '管理员' }, ticket, before)).toBe(true)
    expect(canAcceptFixTicket({ id: 21, permission: '管理员' }, ticket, before)).toBe(false)
    expect(canAcceptFixTicket({ kind: 'super' }, ticket, before)).toBe(false)
    expect(canAcceptFixTicket({ id: 21, permission: '管理员' }, ticket, after)).toBe(true)
    expect(canAcceptFixTicket({ kind: 'super' }, ticket, after)).toBe(true)
    expect(canAcceptFixTicket({ id: 10, permission: '普通员工' }, ticket, after)).toBe(false)
  })

  it('管理端八页合成一个卫生板块，不把员工手机入口算进去', () => {
    // 2026-10-04：排班与卫生合并成子系统「工作台」，这一组是它的「现场」那一组，
    // 品牌三件套跟着子系统走（名字只在 workbenchCopy.js 写一次）。
    expect(HYGIENE_BRAND_TITLE).toBe('工作台')
    expect(hygieneDocumentTitle('花名册')).toBe('花名册 · 工作台')
    expect(HYGIENE_BRAND_MARK).toBe('台')
    expect(HYGIENE_BRAND_TAGLINE).toBe('现场 · 对照实拍验收')
    expect(HYGIENE_ADMIN_NAV.map((item) => item.path)).toEqual([
      '/workbench/roster',
      '/workbench/zones',
      '/workbench/daily',
      '/workbench/attire',
      '/workbench/deep-clean',
      '/workbench/fix',
      '/workbench/boards',
      '/workbench/data',
    ])
    expect(isHygieneAdminPath('/workbench/roster')).toBe(true)
    expect(isHygieneAdminPath('/workbench/boards')).toBe(true)
    expect(isHygieneAdminPath('/workbench/data')).toBe(true)
    // 前缀本身没有页面（票 05 删掉了它）。
    expect(isHygieneAdminPath('/hygiene')).toBe(false)
    // 旧的连字符路径已删除、不留别名，不能再被当成管理端卫生页。
    expect(isHygieneAdminPath('/hygiene-roster')).toBe(false)
    expect(isHygieneAdminPath('/hygiene-data')).toBe(false)
    // 前缀里曾经住着员工侧那两页（`/hygiene/login`、`/hygiene/register`），票 02/03 已删。
    expect(isHygieneAdminPath('/workbench/login')).toBe(false)
    expect(isHygieneAdminPath('/workbench/register')).toBe(false)
    // 自助注册页（票 02 起在顶层 /register）不是管理端卫生页。
    expect(isHygieneAdminPath('/register')).toBe(false)
    // 员工端的卫生待办也不是（票 03 起在 `/workbench/me/clean`）。
    expect(isHygieneAdminPath('/workbench/me/clean')).toBe(false)
    expect(HYGIENE_ADMIN_NAV.map((item) => item.shortTitle)).toEqual([
      '人员', '工作区', '日常', '仪容', '专项', '整改', '榜', '数据',
    ])
    expect(HYGIENE_STAFF_TABS.map((item) => item.id)).toEqual([
      'inbox', 'deep', 'fix', 'boards', 'me',
    ])
    expect(HYGIENE_BACK_TO_ADMIN_LABEL).toBe('后台')
  })
})
