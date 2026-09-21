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
    const calls = [...home.matchAll(/clearTasksByTransport\('staff'\)/g)]
    expect(calls).toHaveLength(2)
    expect(home).toMatch(/function leaveForStaffLogin\(\)[\s\S]*?clearTasksByTransport\('staff'\)/)
    expect(home).toMatch(/async function logout\(\)[\s\S]*?clearTasksByTransport\('staff'\)/)
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

  it('uses that return path on the login page, but only inside /hygiene', () => {
    // 直接把 query 拿去 replace 就是开放重定向：必须限定站内 /hygiene 前缀。
    const login = readFileSync(join(here, '../HygieneLoginView.vue'), 'utf8')
    expect(login).toContain('^\\/hygiene')
    expect(login).toMatch(/router\.replace\(nextPath\)/)
    expect(login).not.toMatch(/router\.replace\(route\.query\.next\)/)
    expect(login).not.toMatch(/router\.replace\('\/hygiene'\)/)
  })

  it('also redirects when a post-login fetch comes back 401', () => {
    expect(home).toMatch(/if \(isAuthError\(failed\.reason\)\) \{\n      leaveForStaffLogin\(\)/)
  })
})
