import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  STAFF_REQUEST_TIMEOUT_MS,
  hygieneLoginUrl,
  staffRequest,
  staffSessionState,
} from '../hygieneStaff'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }
}

describe('staffRequest', () => {
  it('turns a network failure into a Chinese offline error', async () => {
    // 以前这里直接把 fetch 的 TypeError 冒泡出去，店员看到的是 "Failed to fetch"。
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(staffRequest('/api/hygiene/staff/daily-work')).rejects.toMatchObject({
      offline: true,
      message: '网络连不上，请检查手机网络后重试',
    })
  })

  it('tells a timeout apart from a plain network failure', async () => {
    const abort = Object.assign(new Error('aborted'), { name: 'AbortError' })
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(abort))

    await expect(staffRequest('/x')).rejects.toThrow('网络太慢')
  })

  it('aborts a hung request instead of waiting forever', async () => {
    vi.useFakeTimers()
    let aborted = false
    vi.stubGlobal('fetch', vi.fn((_path, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        aborted = true
        reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
      })
    })))

    const pending = staffRequest('/x', { timeoutMs: 1000 })
    const assertion = expect(pending).rejects.toThrow('网络太慢')
    await vi.advanceTimersByTimeAsync(1000)
    await assertion
    expect(aborted).toBe(true)
  })

  it('keeps the HTTP status so callers can tell 401 from a network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse(401, { detail: '需要员工登录' }),
    ))

    await expect(staffRequest('/x')).rejects.toMatchObject({
      status: 401,
      message: '需要员工登录',
    })
  })

  it('uses an 8 second default budget for JSON calls', () => {
    expect(STAFF_REQUEST_TIMEOUT_MS).toBe(8000)
  })
})

describe('hygieneLoginUrl', () => {
  it('encodes the return path so the staff loses nothing on re-login', () => {
    expect(hygieneLoginUrl('/hygiene?tab=fix')).toBe(
      '/hygiene/login?next=%2Fhygiene%3Ftab%3Dfix',
    )
  })
})

describe('staffSessionState', () => {
  it('distinguishes "not logged in" from "network unknown"', async () => {
    // 守卫据此决定要不要把员工甩到登录页：网络不明时放行，401 才跳登录。
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse(401, { detail: '需要员工登录' }),
    ))
    await expect(staffSessionState()).resolves.toBe('unauthenticated')

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(staffSessionState()).resolves.toBe('unknown')
  })

  it('reports ok on a live session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse(200, { employee: { id: 1 } }),
    ))
    await expect(staffSessionState()).resolves.toBe('ok')
  })
})
