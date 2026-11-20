import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('../../api/client', () => ({ api: { get: mocks.get } }))

import { useHygieneDayStore } from '../hygieneDay.js'

const PAYLOAD = {
  today: '2026-11-18',
  day_end: '2026-11-19T00:00:00+08:00',
  weekday: 2,
  week_from: '2026-11-16',
  week_to: '2026-11-22',
}

beforeEach(() => {
  setActivePinia(createPinia())
  mocks.get.mockReset()
  mocks.get.mockResolvedValue(PAYLOAD)
  // 2026-11-18 10:00 北京时间 —— 白天，缓存没过期。
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-18T10:00:00+08:00'))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('hygieneDay store', () => {
  it('拉一次就够：同一天里重复 load 不再发请求', async () => {
    const store = useHygieneDayStore()
    await store.load()
    await store.load()
    expect(mocks.get).toHaveBeenCalledTimes(1)
    expect(mocks.get).toHaveBeenCalledWith('/api/hygiene/today')
    expect(store.today).toBe('2026-11-18')
    expect(store.weekday).toBe(2)
    expect(store.weekFrom).toBe('2026-11-16')
    expect(store.weekTo).toBe('2026-11-22')
  })

  it('过了 day_end 再读就重新取：凌晨页面不会停在昨天', async () => {
    const store = useHygieneDayStore()
    await store.load()
    mocks.get.mockResolvedValue({
      ...PAYLOAD,
      today: '2026-11-19',
      day_end: '2026-11-20T00:00:00+08:00',
      weekday: 3,
    })
    vi.setSystemTime(new Date('2026-11-19T00:00:01+08:00'))
    await store.load()
    expect(mocks.get).toHaveBeenCalledTimes(2)
    expect(store.today).toBe('2026-11-19')
  })

  it('force 跳过缓存', async () => {
    const store = useHygieneDayStore()
    await store.load()
    await store.load(true)
    expect(mocks.get).toHaveBeenCalledTimes(2)
  })

  it('并发 load 只发一次请求', async () => {
    const store = useHygieneDayStore()
    await Promise.all([store.load(), store.load()])
    expect(mocks.get).toHaveBeenCalledTimes(1)
  })

  it('ready 只在拿到 today 之后才为真：挂载期的 realtime pull 据此跳过', async () => {
    const store = useHygieneDayStore()
    expect(store.ready).toBe(false)
    await store.load()
    expect(store.ready).toBe(true)
  })

  it('请求失败不把状态写坏：下一次还会重试', async () => {
    mocks.get.mockRejectedValueOnce(new Error('boom'))
    const store = useHygieneDayStore()
    await expect(store.load()).rejects.toThrow('boom')
    expect(store.loaded).toBe(false)
    await store.load()
    expect(store.today).toBe('2026-11-18')
    expect(mocks.get).toHaveBeenCalledTimes(2)
  })
})
