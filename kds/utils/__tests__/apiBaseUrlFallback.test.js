// C-A5：KDS 的 API 地址兜底。
//
// 原来 `storage.js` 与 `constants.js` 都把生产域名写死成兜底：
//   return process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : 'https://luyun.ykevin0x61.com'
// 于是一台本机/内网部署的屏即使带了 `?token=`，也照样去打生产 API —— 首页显示的是
// **生产数据**（审查截图里那 820 / 767），而 `/api/stations` 直接 `request:fail`。
// 运维分不清"屏坏了"与"指错服务器"，而数字看着是真的，这比没数据更危险。
//
// 现在：能拿到页面来源就用它（屏从哪台服务器加载的就打哪台），拿不到就明确地"没配"
// —— 不再指向任何一个具体门店。
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { resolveFallbackApiBaseUrl } from '../constants.js'

describe('resolveFallbackApiBaseUrl', () => {
  it('H5：用页面来源（本机预览 / 门店内网 / 反代后面三处都对）', () => {
    expect(resolveFallbackApiBaseUrl({ origin: 'http://127.0.0.1:8123' }))
      .toBe('http://127.0.0.1:8123')
    expect(resolveFallbackApiBaseUrl({ origin: 'https://shop.local' }))
      .toBe('https://shop.local')
  })

  it('去掉尾斜杠（拼 URL 时不会出现 //api）', () => {
    expect(resolveFallbackApiBaseUrl({ origin: 'http://127.0.0.1:8123/' }))
      .toBe('http://127.0.0.1:8123')
  })

  it('再也不会兜到生产域名上', () => {
    for (const origin of ['http://127.0.0.1:8123', 'https://shop.local', '']) {
      expect(resolveFallbackApiBaseUrl({ origin })).not.toContain('ykevin0x61')
    }
  })

  it('file:// 或 null（直接双击 dist/index.html）当"没配"，不拼一个必然 404 的地址', () => {
    expect(resolveFallbackApiBaseUrl({ origin: 'file://' })).toBe('')
    expect(resolveFallbackApiBaseUrl({ origin: 'null' })).toBe('')
    expect(resolveFallbackApiBaseUrl({ origin: '   ' })).toBe('')
  })

  it('非 H5（没有 origin）：返回空串 —— 地址没配就让它失败得明白', () => {
    expect(resolveFallbackApiBaseUrl({})).toBe('')
    expect(resolveFallbackApiBaseUrl({ origin: undefined })).toBe('')
    expect(resolveFallbackApiBaseUrl({ origin: 42 })).toBe('')
  })

  it('开发构建仍用本地后端（与既有行为一致，服务端默认端口 8000）', () => {
    expect(resolveFallbackApiBaseUrl({ isDev: true, origin: '' })).toBe('http://localhost:8000')
    // 来源优先于开发档位：`storage.js` 只在开发且**没有**来源时才轮到 5173 —— 见下面那条。
    expect(resolveFallbackApiBaseUrl({ isDev: true, origin: 'http://localhost:5173' }))
      .toBe('http://localhost:5173')
  })
})

// `ApiSettingsManager.getBaseUrl()` 是真正被 request / realtime 调用的那一处：
// 配过就用配的，没配过才走上面的兜底。
describe('ApiSettingsManager.getBaseUrl', () => {
  const memory = new Map()

  beforeEach(() => {
    memory.clear()
    vi.resetModules()
    vi.stubGlobal('uni', {
      getStorageSync: (key) => (memory.has(key) ? memory.get(key) : ''),
      setStorageSync: (key, value) => memory.set(key, value),
      removeStorageSync: (key) => memory.delete(key),
    })
    vi.stubGlobal('location', { origin: 'http://127.0.0.1:8123' })
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('配过就用配的（设置页写进 kds_api_settings 的那个地址）', async () => {
    memory.set('kds_api_settings', JSON.stringify({ baseUrl: 'https://shop.example' }))
    const { ApiSettingsManager } = await import('../storage.js')

    expect(ApiSettingsManager.getBaseUrl()).toBe('https://shop.example')
  })

  it('没配过：用页面来源，并在控制台留一句（运维据此分清"屏坏了"与"指错服务器"）', async () => {
    const { ApiSettingsManager } = await import('../storage.js')

    expect(ApiSettingsManager.getBaseUrl()).toBe('http://127.0.0.1:8123')
    expect(console.warn).toHaveBeenCalledWith(
      expect.stringContaining('http://127.0.0.1:8123'),
    )
  })

  it('没有页面来源时：不猜地址，明说"地址没配"', async () => {
    vi.stubGlobal('location', undefined)
    const { ApiSettingsManager } = await import('../storage.js')

    expect(ApiSettingsManager.getBaseUrl()).toBe('')
    expect(console.warn).toHaveBeenCalledWith(expect.stringContaining('设置'))
  })
})
