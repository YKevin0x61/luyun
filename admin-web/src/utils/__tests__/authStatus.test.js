import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  clearAuthStatusCache,
  isLoggedIn,
  setAuthLoggedIn,
} from '../authStatus.js'

afterEach(() => {
  clearAuthStatusCache()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('isLoggedIn', () => {
  it('不缓存未登录，登录成功后立刻能进受保护页', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ json: async () => ({ logged_in: false }) })
      .mockResolvedValueOnce({ json: async () => ({ logged_in: true }) })
    vi.stubGlobal('fetch', fetchMock)

    expect(await isLoggedIn()).toBe(false)
    expect(await isLoggedIn()).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('已登录结果在 TTL 内复用，不重复打 /api/auth/status', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ json: async () => ({ logged_in: true }) })
    vi.stubGlobal('fetch', fetchMock)

    expect(await isLoggedIn()).toBe(true)
    expect(await isLoggedIn()).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('setAuthLoggedIn(true) 后守卫不再被负缓存挡住', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ json: async () => ({ logged_in: false }) })
    vi.stubGlobal('fetch', fetchMock)

    expect(await isLoggedIn()).toBe(false)
    setAuthLoggedIn(true)
    expect(await isLoggedIn()).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('status 请求失败时 fail-closed 且不留下负缓存', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    expect(await isLoggedIn()).toBe(false)
    const fetchMock = vi.fn().mockResolvedValue({ json: async () => ({ logged_in: true }) })
    vi.stubGlobal('fetch', fetchMock)
    expect(await isLoggedIn()).toBe(true)
  })
})
