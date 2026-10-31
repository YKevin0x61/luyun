// @vitest-environment jsdom
/**
 * 店长专属页对「员工会话 + 那项能力」开一扇门（2026-10-14 用户裁定）。
 *
 * 判据分三处、各自独立：页面清单（`staffCap`）→ 路由守卫（本文件压的就是它）→ 接口
 * 自己那道门（后端用例压）。这里用**真实** `router/index.js` 跑一遍守卫，断言 URL 最后
 * 落在哪：有那一项就进得去，没有就落 `/workbench/forbidden?next=<原目标>`，而不是
 * 静默改道、也不是甩回管理端登录页。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 每个用例都换一份崭新的 router 模块：`authStatus` 的 10 秒缓存与 router 单例都不跨用例。 */
async function freshRouter() {
  vi.resetModules()
  const mod = await import('../index.js')
  return mod.default
}

let staffEmployee = null

beforeEach(() => {
  staffEmployee = null
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url) => {
      const path = String(url)
      if (path.includes('/api/hygiene/staff/session')) {
        return staffEmployee
          ? jsonResponse({ authenticated: true, employee: staffEmployee })
          : jsonResponse({ authenticated: false, employee: null })
      }
      if (path.includes('/api/auth/status')) {
        return jsonResponse({ logged_in: false, initialized: true })
      }
      return jsonResponse({}, 404)
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('守卫：员工会话 + staffCap', () => {
  it('勾了「人事管理」的员工进得去人事四页', async () => {
    staffEmployee = { id: 7, name: '张三', admin_caps: ['schedule'] }
    for (const path of [
      '/workbench/hr/calendar',
      '/workbench/hr/inbox',
      '/workbench/hr/shifts',
      // 考勤表 2026-10-31 并进同一项能力（页面清单 staffCap: schedule）。
      '/workbench/hr/attendance',
    ]) {
      const router = await freshRouter()
      await router.push(path)
      expect(router.currentRoute.value.path, path).toBe(path)
    }
  })

  it('没勾「人事管理」的员工落无权访问页（不是静默改道、也不是登录页）', async () => {
    staffEmployee = { id: 7, name: '张三', admin_caps: [] }
    const router = await freshRouter()
    await router.push('/workbench/hr/calendar')
    expect(router.currentRoute.value.path).toBe('/workbench/forbidden')
    expect(router.currentRoute.value.query.next).toBe('/workbench/hr/calendar')
  })

  it('花名册没有 staffCap：连勾了人事管理也进不去（身份证明细不下发员工端）', async () => {
    staffEmployee = { id: 7, name: '张三', admin_caps: ['schedule'] }
    const router = await freshRouter()
    await router.push('/workbench/hr/roster')
    expect(router.currentRoute.value.path).toBe('/workbench/forbidden')
  })

  it('勾了「损耗分析」的员工进得去损耗分析页', async () => {
    staffEmployee = { id: 7, name: '张三', admin_caps: ['wastage'] }
    const router = await freshRouter()
    await router.push('/workbench/kitchen/loss')
    expect(router.currentRoute.value.path).toBe('/workbench/kitchen/loss')
  })

  it('能力键对不上（勾的是另一项）也进不去', async () => {
    staffEmployee = { id: 7, name: '张三', admin_caps: ['wastage'] }
    const router = await freshRouter()
    await router.push('/workbench/hr/shifts')
    expect(router.currentRoute.value.path).toBe('/workbench/forbidden')
  })

  it('一个会话都没有 → 登录页（不是无权访问页）', async () => {
    staffEmployee = null
    const router = await freshRouter()
    await router.push('/workbench/kitchen/loss')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench/kitchen/loss')
  })
})
