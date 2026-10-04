import { describe, expect, it } from 'vitest'
import { pageMeta } from '../../router/pageRoutes.js'
import {
  WORKBENCH_NAV_GROUPS,
  workbenchAudienceFor,
  workbenchGroupOf,
  workbenchNavFor,
} from '../workbenchNav.js'

// 票 04：导航的判据从「这一页的身份」换成「**此刻的身份**」，于是工作台身份那两档
// （`super` / `staff`）要先映射成清单里的身份词（`admin` / `staff`）。
// 这里压的是映射与过滤本身；「顶上那条栏跟着换」在 `workbenchShell.test.js` 里按渲染断言。

describe('工作台身份 → 清单身份', () => {
  it('两档各映射到一边，认不出的身份给 null（fail-closed，不当作管理员）', () => {
    expect(workbenchAudienceFor('super')).toBe('admin')
    expect(workbenchAudienceFor('staff')).toBe('staff')
    expect(workbenchAudienceFor(null)).toBeNull()
    expect(workbenchAudienceFor(undefined)).toBeNull()
    expect(workbenchAudienceFor('admin')).toBeNull()
  })
})

describe('按身份过滤导航', () => {
  it('认不出身份时一格都不渲染（没有点不通的入口）', () => {
    expect(workbenchNavFor(null)).toEqual([])
    expect(workbenchNavFor(undefined)).toEqual([])
    expect(workbenchNavFor('')).toEqual([])
  })

  it('每一格的可见性来自它指向那一页的 audience（不另写一份名单）', () => {
    for (const group of WORKBENCH_NAV_GROUPS) {
      const { audience } = pageMeta(group.to)
      const shown = workbenchNavFor(audience === 'admin' ? 'super' : 'staff')
      expect(shown.map((item) => item.key), `${group.key} 的可见性`).toContain(group.key)
      if (audience === 'admin' || audience === 'staff') {
        const other = audience === 'admin' ? 'staff' : 'super'
        expect(workbenchNavFor(other).map((item) => item.key), `${group.key} 藏给另一边`).not.toContain(group.key)
      }
    }
  })

  it('现在这一格在「我的」：两档各自的结果与清单一致', () => {
    const meAudience = pageMeta('/workbench/me/today').audience
    expect(meAudience).toBe('staff')
    expect(workbenchNavFor('staff').map((item) => item.key)).toEqual(['me'])
    // 票 05 把人事 / 现场并进来之前，店长这一档在工作台里还没有自己的组。
    expect(workbenchNavFor('super')).toEqual([])
  })

  it('高亮按「组」算：整组下的任意一页都算在那一格上', () => {
    expect(workbenchGroupOf('/workbench/me/month')).toBe('me')
    expect(workbenchGroupOf('/workbench/me/clean')).toBe('me')
    // 不在清单里的路径（单测里的临时路由）没有组，也就没有高亮。
    expect(workbenchGroupOf('/nowhere')).toBeNull()
  })
})
