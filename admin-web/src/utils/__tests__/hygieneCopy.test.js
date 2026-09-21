import { describe, expect, it } from 'vitest'
import {
  HYGIENE_ADMIN_NAV,
  HYGIENE_BACK_TO_ADMIN_LABEL,
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

  it('管理端七页合成一个卫生板块，不把员工手机入口算进去', () => {
    expect(HYGIENE_BRAND_TITLE).toBe('卫生')
    expect(hygieneDocumentTitle('花名册')).toBe('花名册 · 卫生')
    expect(HYGIENE_ADMIN_NAV.map((item) => item.path)).toEqual([
      '/hygiene-roster',
      '/hygiene-zones',
      '/hygiene-daily',
      '/hygiene-deep-clean',
      '/hygiene-fix',
      '/hygiene-boards',
      '/hygiene-data',
    ])
    expect(isHygieneAdminPath('/hygiene-roster')).toBe(true)
    expect(isHygieneAdminPath('/hygiene-boards')).toBe(true)
    expect(isHygieneAdminPath('/hygiene-data')).toBe(true)
    expect(isHygieneAdminPath('/hygiene')).toBe(false)
    expect(isHygieneAdminPath('/hygiene/login')).toBe(false)
    expect(isHygieneAdminPath('/hygiene/register')).toBe(false)
    expect(HYGIENE_ADMIN_NAV.map((item) => item.shortTitle)).toEqual([
      '人员', '责任区', '日常', '专项', '整改', '榜', '数据',
    ])
    expect(HYGIENE_STAFF_TABS.map((item) => item.id)).toEqual([
      'inbox', 'deep', 'fix', 'boards', 'me',
    ])
    expect(HYGIENE_BACK_TO_ADMIN_LABEL).toBe('后台')
  })
})
