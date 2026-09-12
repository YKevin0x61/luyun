import { describe, expect, it } from 'vitest'
import {
  HYGIENE_PERMISSIONS,
  hygienePermissionLabel,
  isAllowedHygienePermission,
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
})
