import { describe, expect, it } from 'vitest'
import { PAGE_ROUTES, pageMeta, pageRow } from '../../router/pageRoutes.js'
import {
  WORKBENCH_FIELD_HOME,
  WORKBENCH_HR_HOME,
  WORKBENCH_TITLE,
  workbenchDocumentTitle,
} from '../workbenchCopy.js'
import {
  WORKBENCH_NAV_GROUPS,
  workbenchAudienceFor,
  workbenchGroupOf,
  workbenchNavFor,
  workbenchPagesOf,
} from '../workbenchNav.js'

// 票 04：导航的判据从「这一页的身份」换成「**此刻的身份**」，于是工作台身份那两档
// （`super` / `staff`）要先映射成清单里的身份词（`admin` / `staff`）。
// 票 05：工作台的两组（人事 / 现场）落位 —— 组表、落点、组里的页都从页面清单派生。
// 这里压的是映射、分组与过滤本身；「顶上那条栏跟着换」在 `workbenchShell.test.js` 里按渲染断言。

describe('工作台身份 → 清单身份', () => {
  it('两档各映射到一边，认不出的身份给 null（fail-closed，不当作管理员）', () => {
    expect(workbenchAudienceFor('super')).toBe('admin')
    expect(workbenchAudienceFor('staff')).toBe('staff')
    expect(workbenchAudienceFor(null)).toBeNull()
    expect(workbenchAudienceFor(undefined)).toBeNull()
    expect(workbenchAudienceFor('admin')).toBeNull()
  })
})

describe('工作台的组（票 05：人事 / 现场；票 06 加上首页）', () => {
  it('四组：今天、人事、现场、我的（顺序就是顶栏里的顺序）', () => {
    expect(WORKBENCH_NAV_GROUPS.map((group) => group.key)).toEqual(['home', 'hr', 'floor', 'me'])
    expect(WORKBENCH_NAV_GROUPS.map((group) => group.label)).toEqual(['今天', '人事', '现场', '我的'])
    // 第一格是子应用根（首页），不是某一组的专页。
    expect(WORKBENCH_NAV_GROUPS[0].to).toBe('/workbench')
  })

  it('每组的落点是清单里真实的一页，且那一页就属于这一组', () => {
    for (const group of WORKBENCH_NAV_GROUPS) {
      const row = pageRow(group.to)
      expect(row, `${group.key} 的落点 ${group.to} 不在页面清单里`).toBeTruthy()
      expect(row.group, `${group.key} 的落点落在别组`).toBe(group.key)
    }
  })

  it('页面标题的写法只写一次：`<页面名> · 工作台`，页面名就是「工作台」时不叠', () => {
    expect(workbenchDocumentTitle('花名册')).toBe('花名册 · 工作台')
    expect(workbenchDocumentTitle(' 排班月历 ')).toBe('排班月历 · 工作台')
    // 子应用根那一页的名字就是「工作台」（票 06 起是首页）：不写成「工作台 · 工作台」。
    expect(workbenchDocumentTitle(WORKBENCH_TITLE)).toBe('工作台')
    expect(workbenchDocumentTitle('')).toBe('工作台')
    expect(workbenchDocumentTitle(null)).toBe('工作台')
  })

  it('人事落点是排班月历、现场落点是日常验收（两个常量只写一次）', () => {
    // 两个壳（`SchedulingLayout` / `HygieneAdminLayout`）与页内跳转都引这两个常量，
    // 谁都不许再写一遍字面量。
    expect(WORKBENCH_HR_HOME).toBe('/workbench/hr/calendar')
    expect(WORKBENCH_FIELD_HOME).toBe('/workbench/floor/daily')
    expect(WORKBENCH_NAV_GROUPS.find((group) => group.key === 'hr').to).toBe(WORKBENCH_HR_HOME)
    expect(WORKBENCH_NAV_GROUPS.find((group) => group.key === 'floor').to).toBe(WORKBENCH_FIELD_HOME)
  })

  it('组里的页从清单派生（人事四页、现场七页，顺序照清单）', () => {
    expect(workbenchPagesOf('hr').map((page) => page.path)).toEqual([
      '/workbench/hr/calendar',
      '/workbench/hr/inbox',
      '/workbench/hr/shifts',
      '/workbench/hr/roster',
    ])
    expect(workbenchPagesOf('floor').map((page) => page.path)).toEqual([
      '/workbench/floor/zones',
      '/workbench/floor/daily',
      '/workbench/floor/attire',
      '/workbench/floor/deep-clean',
      '/workbench/floor/fix',
      '/workbench/floor/boards',
      '/workbench/floor/data',
    ])
    // 标题也从清单来：外壳的导航与页签不另抄一份名字。
    expect(workbenchPagesOf('hr').map((page) => page.title)).toEqual([
      '排班月历', '排班待办', '班次表', '花名册',
    ])
    // 认不出的组给空表（不猜、也不兜底成一整份清单）。
    expect(workbenchPagesOf('nope')).toEqual([])
  })

  it('十一个页面正好落在人事 / 现场两组里，平铺那批地址一条都不在清单里', () => {
    const grouped = PAGE_ROUTES.filter((row) => row.group === 'hr' || row.group === 'floor')
    expect(grouped).toHaveLength(11)
    // 留一半最坏：「点得进去、刷新 404」。
    for (const old of [
      '/workbench/inbox', '/workbench/shifts', '/workbench/roster', '/workbench/zones',
      '/workbench/daily', '/workbench/deep-clean', '/workbench/fix', '/workbench/boards',
      '/workbench/data', '/workbench/attire',
    ]) {
      expect(pageRow(old), `${old} 又回到清单里了`).toBe(null)
    }
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

  it('店长这一档：今天 / 人事 / 现场三格；员工那一档：今天 / 我的（首页两组都看得见）', () => {
    expect(workbenchNavFor('super').map((item) => item.key)).toEqual(['home', 'hr', 'floor'])
    expect(workbenchNavFor('staff').map((item) => item.key)).toEqual(['home', 'me'])
    // 首页那一页是 `both`（票 06）：两档都进得去，过滤天然放行。
    expect(pageMeta('/workbench').audience).toBe('both')
    expect(pageMeta(WORKBENCH_HR_HOME).audience).toBe('admin')
    expect(pageMeta(WORKBENCH_FIELD_HOME).audience).toBe('admin')
    expect(pageMeta('/workbench/me/today').audience).toBe('staff')
  })
})

describe('高亮跟着新分组走', () => {
  it('整组下的任意一页都算在那一格上', () => {
    expect(workbenchGroupOf('/workbench/me/month')).toBe('me')
    expect(workbenchGroupOf('/workbench/me/clean')).toBe('me')
    expect(workbenchGroupOf('/workbench/hr/calendar')).toBe('hr')
    expect(workbenchGroupOf('/workbench/hr/roster')).toBe('hr')
    expect(workbenchGroupOf('/workbench/floor/daily')).toBe('floor')
    expect(workbenchGroupOf('/workbench/floor/data')).toBe('floor')
    // 子应用根（票 06 的首页那一组）不属于人事 / 现场任何一格。
    expect(workbenchGroupOf('/workbench')).toBe('home')
    // 不在清单里的路径（单测里的临时路由）没有组，也就没有高亮。
    expect(workbenchGroupOf('/nowhere')).toBeNull()
  })
})
