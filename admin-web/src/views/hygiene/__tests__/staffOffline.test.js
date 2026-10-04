import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const home = readFileSync(join(here, '../HygieneHomeView.vue'), 'utf8')

describe('员工端弱网与未登录的分流', () => {
  it('只有确认未登录才清上传队列', () => {
    // 这两处是允许清队列的全部位置：会话失效回登录页、以及员工主动登出。
    // 网络抖动绝不能出现在这份名单里——那些照片是店员现场拍的，重拍代价极高。
    // 票 10 起主动登出搬到了三页共用的 `useStaffLogout`，首页只剩会话失效那一处；
    // 两处一起断言，名单强度不变。
    const logout = readFileSync(join(here, '../../../composables/useStaffLogout.js'), 'utf8')
    const calls = [...home.matchAll(/clearTasksByTransport\('staff'\)/g)]
    expect(calls).toHaveLength(1)
    expect(home).toMatch(/function leaveForStaffLogin\(\)[\s\S]*?clearTasksByTransport\('staff'\)/)
    expect(home).not.toMatch(/function logout\(\)/)
    expect(logout).toMatch(/clearTasksByTransport\('staff'\)/)
  })

  it('splits the loadMe catch on err.status, not on "anything failed"', () => {
    expect(home).toMatch(/function isAuthError\(err\) \{\n  return Boolean\(err && err\.status === 401\)/)
    const loadMe = home.match(/async function loadMe\(\)[\s\S]*?\n\}/)[0]
    expect(loadMe).toMatch(/if \(isAuthError\(err\)\)/)
    expect(loadMe).toMatch(/leaveForStaffLogin\(\)/)
    // 网络分支：写提示 + 安排重试，然后留在本页
    expect(loadMe).toMatch(/网络不好，正在重试/)
    expect(loadMe).toMatch(/scheduleMeRetry\(\)/)
    expect(loadMe).not.toMatch(/errorText\.value = err\.message \|\| '无法读取登录状态'/)
  })

  it('retries with backoff instead of leaving the staff on a stuck page', () => {
    expect(home).toMatch(/ME_RETRY_DELAYS_MS = \[2000, 4000, 8000\]/)
    expect(home).toMatch(/function scheduleMeRetry\(\)/)
    expect(home).toMatch(/meRetryIndex = 0/)
  })

  it('clears the retry timer on unmount', () => {
    expect(home).toMatch(/if \(meRetryTimer\) window\.clearTimeout\(meRetryTimer\)/)
  })

  it('carries the current page into re-login', () => {
    expect(home).toMatch(/query: \{ next: router\.currentRoute\.value\.fullPath \}/)
  })

  it('uses that return path on the login panel, but only inside the staff pages', () => {
    // 直接把 query 拿去 replace 就是开放重定向：必须限定站内前缀。票 03 起员工登录
    // 就在 `/login` 的员工栏（`?next=` 落在员工端前缀内时面板强制开员工栏）；票 04 起
    // 员工端三页整体在 `/workbench/me/*`（今天、整月、卫生待办 —— 票 03 搬进工作台）。
    // 判据只在 utils/loginNext.js 的 `resolveStaffNext` 里写一遍（真单测在
    // utils/__tests__/loginNext.test.js）：页面里手写正则等于第二份更弱的判据，
    // 放松了也没人拦。
    const login = readFileSync(join(here, '../../LoginView.vue'), 'utf8')
    expect(login).toMatch(/resolveStaffNext\(route\.query\.next\)/)
    expect(login).not.toMatch(/startsWith\('\/staff'\)/)
    expect(login).toMatch(/router\.replace\(resolveStaffNext\(route\.query\.next\)\)/)
    expect(login).not.toMatch(/router\.replace\(route\.query\.next\)/)
    expect(login).not.toMatch(/router\.replace\('\/staff/)
  })

  it('also redirects when a post-login fetch comes back 401', () => {
    expect(home).toMatch(/if \(isAuthError\(failed\.reason\)\) \{\n      leaveForStaffLogin\(\)/)
  })
})
