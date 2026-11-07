// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import PrepReviewView from '../PrepReviewView.vue'
import { clearAuthStatusCache } from '../../utils/authStatus.js'

/**
 * 备货复盘 · 天气设置（票 11）。
 *
 * 只盯界面契约：按钮打得开、字段回填脱敏值、保存 payload 的形状（空 Key 不带
 * 字段）、必填不发请求、测试按钮的 loading / 成功 / 失败三态。真正出网与写库
 * 由后端用例负责；这里 fetch 全 mock。
 */

const DAY_STR = new Date().toLocaleDateString('sv-SE')
const MASKED = 'abcd****wxyz'
const HOST = 'https://abc123.re.qweatherapi.com'

const SUMMARY = {
  success: true,
  period: {},
  primary_clock: '07:00',
  metrics: {},
  groups: {},
  cost: null,
}

const DAY = { success: true, day: DAY_STR, primary_clock: null, snapshots: [], context: null }

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

function weatherFetch({ config = { city: '广州', api_host: '' }, testResult = null, saveResult = null } = {}) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    const method = options.method || 'GET'
    calls.push({ path, method, body: options.body })
    if (path.includes('/api/stations')) return jsonResponse([])
    if (path.includes('/api/prep-plan/context/weather/test')) {
      return jsonResponse(testResult || { ok: true, sample: { textDay: '晴', tempMin: 21, tempMax: 28 } })
    }
    if (path.includes('/api/prep-plan/context/weather')) {
      return jsonResponse(method === 'PUT' ? saveResult || config : config)
    }
    if (path.includes('/api/prep-plan/review/summary')) return jsonResponse(SUMMARY)
    if (path.includes('/api/prep-plan/snapshots')) return jsonResponse(DAY)
    return jsonResponse({}, 404)
  })
  fetchMock.calls = calls
  return fetchMock
}

async function mountView() {
  vi.resetModules()
  window.localStorage.clear()
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(PrepReviewView, {
    global: { plugins: [pinia], stubs: { RouterLink: true } },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

async function openSettings(wrapper) {
  await wrapper.find('button[aria-label="天气设置"]').trigger('click')
  await flushPromises()
}

function callsTo(fetchMock, fragment) {
  return fetchMock.calls.filter((call) => call.path.includes(fragment))
}

beforeEach(() => {
  clearAuthStatusCache()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('天气设置弹层', () => {
  it('按钮在头部，点开后读配置并把脱敏值放进 Key 占位', async () => {
    const fetchMock = weatherFetch({
      config: {
        configured: true,
        api_key_masked: MASKED,
        city: '广州',
        api_host: HOST,
        updated_at: '2026-11-01T10:00:00+08:00',
      },
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    const button = wrapper.find('button[aria-label="天气设置"]')
    expect(button.exists()).toBe(true)
    expect(button.text()).toContain('设置')
    expect(wrapper.find('.pr-weather-dialog').exists()).toBe(false)

    await openSettings(wrapper)

    expect(wrapper.find('.pr-weather-dialog').exists()).toBe(true)
    expect(callsTo(fetchMock, '/api/prep-plan/context/weather')).toHaveLength(1)
    const key = wrapper.find('#pr-weather-key')
    expect(key.attributes('type')).toBe('password')
    expect(key.attributes('placeholder')).toContain(MASKED)
    expect(wrapper.find('#pr-weather-city').element.value).toBe('广州')
    expect(wrapper.find('#pr-weather-host').element.value).toBe(HOST)
    expect(wrapper.text()).toContain('配了才有天气')
  })

  it('关闭按钮收起弹层', async () => {
    vi.stubGlobal('fetch', weatherFetch())
    const wrapper = await mountView()
    await openSettings(wrapper)

    await wrapper.find('.pr-weather-close').trigger('click')

    expect(wrapper.find('.pr-weather-dialog').exists()).toBe(false)
  })

  it('保存时空 Key 不带 api_key 字段，成功后关闭并重拉快照', async () => {
    const config = {
      configured: true,
      api_key_masked: MASKED,
      city: '广州',
      api_host: HOST,
      updated_at: '',
    }
    const fetchMock = weatherFetch({ config })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()
    await openSettings(wrapper)
    const before = callsTo(fetchMock, '/api/prep-plan/snapshots').length

    await wrapper.find('.pr-weather-dialog').trigger('submit')
    await flushPromises()

    const put = callsTo(fetchMock, '/api/prep-plan/context/weather').find((c) => c.method === 'PUT')
    expect(put).toBeTruthy()
    expect(JSON.parse(put.body)).toEqual({ city: '广州', api_host: HOST })
    expect(wrapper.find('.pr-weather-dialog').exists()).toBe(false)
    expect(callsTo(fetchMock, '/api/prep-plan/snapshots').length).toBe(before + 1)
  })

  it('填了新 Key 时保存把它带上', async () => {
    const fetchMock = weatherFetch({ config: { city: '广州', api_host: HOST } })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()
    await openSettings(wrapper)

    await wrapper.find('#pr-weather-key').setValue('typed-key-9876')
    await wrapper.find('.pr-weather-dialog').trigger('submit')
    await flushPromises()

    const put = callsTo(fetchMock, '/api/prep-plan/context/weather').find((c) => c.method === 'PUT')
    expect(JSON.parse(put.body)).toEqual({
      api_key: 'typed-key-9876',
      city: '广州',
      api_host: HOST,
    })
  })

  it('城市为空时不发保存请求，直接提示必填', async () => {
    const fetchMock = weatherFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()
    await openSettings(wrapper)

    await wrapper.find('#pr-weather-city').setValue('   ')
    await wrapper.find('.pr-weather-dialog').trigger('submit')
    await flushPromises()

    expect(callsTo(fetchMock, '/api/prep-plan/context/weather').filter((c) => c.method === 'PUT')).toHaveLength(0)
    expect(wrapper.find('.pr-weather-status').text()).toContain('城市必填')
  })

  it('保存失败时把后端原因显示在状态行', async () => {
    const fetchMock = weatherFetch()
    fetchMock.mockImplementation(async (url, options = {}) => {
      const path = String(url)
      if (path.includes('/api/stations')) return jsonResponse([])
      if (path.includes('/api/prep-plan/context/weather') && options.method === 'PUT') {
        return jsonResponse({ detail: 'API Host 必须是 https:// 开头的地址' }, 400)
      }
      if (path.includes('/api/prep-plan/context/weather')) return jsonResponse({ city: '广州', api_host: HOST })
      if (path.includes('/api/prep-plan/review/summary')) return jsonResponse(SUMMARY)
      return jsonResponse(DAY)
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()
    await openSettings(wrapper)

    await wrapper.find('.pr-weather-dialog').trigger('submit')
    await flushPromises()

    expect(wrapper.find('.pr-weather-dialog').exists()).toBe(true)
    expect(wrapper.find('.pr-weather-status').text()).toContain('https://')
  })

  it('测试连通：请求期间按钮 loading，成功后显示样本天气', async () => {
    let release
    const pending = new Promise((resolve) => {
      release = resolve
    })
    const fetchMock = weatherFetch()
    fetchMock.mockImplementation(async (url, options = {}) => {
      const path = String(url)
      if (path.includes('/api/stations')) return jsonResponse([])
      if (path.includes('/api/prep-plan/context/weather/test')) {
        await pending
        return jsonResponse({
          ok: true,
          location_id: '101280101',
          sample: { fxDate: DAY_STR, textDay: '晴', tempMin: 21, tempMax: 28 },
        })
      }
      if (path.includes('/api/prep-plan/context/weather')) return jsonResponse({ city: '广州', api_host: HOST })
      if (path.includes('/api/prep-plan/review/summary')) return jsonResponse(SUMMARY)
      return jsonResponse(DAY)
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()
    await openSettings(wrapper)

    await wrapper.find('.pr-weather-test').trigger('click')
    await nextTick()

    const testButton = wrapper.find('.pr-weather-test')
    expect(testButton.attributes('disabled')).toBeDefined()
    expect(testButton.text()).toContain('测试中')

    release()
    await flushPromises()

    expect(wrapper.find('.pr-weather-test').attributes('disabled')).toBeUndefined()
    const status = wrapper.find('.pr-weather-status').text()
    expect(status).toContain('晴')
    expect(status).toContain('21')
    expect(status).toContain('28')
  })

  it('测试连通失败：200 + ok=false 的原因原样显示', async () => {
    const fetchMock = weatherFetch({
      testResult: { ok: false, error: 'Key 无效，或这个 Key 没有天气接口权限' },
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()
    await openSettings(wrapper)

    await wrapper.find('.pr-weather-test').trigger('click')
    await flushPromises()

    expect(wrapper.find('.pr-weather-status').text()).toContain('Key 无效')
    expect(wrapper.find('.pr-weather-test').attributes('disabled')).toBeUndefined()
  })

  it('测试连通前同样校验城市必填', async () => {
    const fetchMock = weatherFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()
    await openSettings(wrapper)

    await wrapper.find('#pr-weather-city').setValue('')
    await wrapper.find('.pr-weather-test').trigger('click')
    await flushPromises()

    expect(callsTo(fetchMock, '/context/weather/test')).toHaveLength(0)
    expect(wrapper.find('.pr-weather-status').text()).toContain('城市必填')
  })
})
