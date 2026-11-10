// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/**
 * 员工端「我的」页 · 2026-11 工作台未落地项的两颗入口：
 *
 * 1. 健康证横条右手的「更新健康证」→ 页内弹层（新到期日必填、不早于今天）→
 *    `PATCH /api/hygiene/staff/me/health-cert` → 成功后 `staffMe` 合并、横条消失、
 *    「我的资料」里出现「健康证已更新…」回执；
 * 2. 页头「整月 ›」旁边的「加班与补钟 ›」：1 击到 `/workbench/staff/overtime`
 *    （这一页路由 audience=staff，管理端会话进不来，所以入口天然只对员工出现）。
 *
 * 健康证数据全是 `/api/hygiene/staff/me` 的那份：`health_cert_state` 决定横条在不在，
 * 前端不做日期算术（今天由服务端算）；这里只把服务端的状态当输入。
 */

const HYGIENE_ME = {
  employee: {
    id: 7,
    name: '余威威',
    phone: '13800000000',
    job_title: '案板',
    permission: '普通员工',
    shift: '白班',
    zone_id: 3,
    zone_name: '案板',
    hire_date: '2026-01-01',
    health_cert_expires_on: '2026-09-01',
    health_cert_state: 'expired',
  },
  daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
  deep_clock: { hhmm: '20:00' },
}

// 日期选择器换成一颗裸 input：弹层流程是本文件的主角，日历本身由组件自己的
// 用例覆盖（`components/ui/__tests__`），这里只负责把 v-model 的字符串传进去。
const DatePickerStub = defineComponent({
  name: 'LuyunDatePicker',
  props: {
    modelValue: { type: String, default: '' },
    inputId: { type: String, default: undefined },
  },
  emits: ['update:modelValue'],
  template:
    '<input class="date-stub" :id="inputId" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)">',
})

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountToday({ employee = {}, patch = null } = {}) {
  const calls = []
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const path = String(url).split('?')[0]
    const method = options.method || 'GET'
    const body = options.body ? JSON.parse(options.body) : null
    calls.push({ path, method, body })
    if (path === '/api/hygiene/staff/me/health-cert' && method === 'PATCH') {
      if (patch) return patch({ path, method, body })
      return jsonResponse({
        employee: {
          ...HYGIENE_ME.employee,
          ...employee,
          health_cert_expires_on: body.expires_on,
          health_cert_state: 'ok',
        },
      })
    }
    if (path === '/api/scheduling/me/requests') return jsonResponse({ requests: [], incoming: [] })
    if (path === '/api/hygiene/staff/me') {
      return jsonResponse({ ...HYGIENE_ME, employee: { ...HYGIENE_ME.employee, ...employee } })
    }
    if (path === '/api/hygiene/staff/me/stats') {
      return jsonResponse({ days: 7, pass_rate: null, first_pass: 0, rejected: 0, reasons: [] })
    }
    return jsonResponse({ detail: 'not found' }, 404)
  }))

  const { default: TodayView } = await import('../TodayView.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workbench/staff/today', component: { template: '<div />' } },
      { path: '/workbench/staff/month', component: { template: '<div />' } },
      { path: '/workbench/staff/overtime', component: { template: '<div />' } },
    ],
  })
  await router.push('/workbench/staff/today')
  await router.isReady()

  const wrapper = mount(TodayView, {
    global: { plugins: [router, pinia], stubs: { LuyunDatePicker: DatePickerStub } },
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, calls, router }
}

function patchCalls(calls) {
  return calls.filter((call) => call.path === '/api/hygiene/staff/me/health-cert')
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('员工端 · 页头「加班与补钟」入口', () => {
  it('「整月 ›」旁出现「加班与补钟 ›」，链接到 /workbench/staff/overtime', async () => {
    const { wrapper } = await mountToday()
    const links = wrapper.findAll('.tTop-link')
    expect(links).toHaveLength(2)
    expect(links[0].text()).toBe('整月 ›')
    expect(links[0].attributes('href')).toBe('/workbench/staff/month')
    const overtime = wrapper.get('.tTop-link[href="/workbench/staff/overtime"]')
    expect(overtime.text()).toBe('加班与补钟 ›')
    wrapper.unmount()
  })
})

describe('员工端 · 健康证自助更新', () => {
  it('过期时横条右手有「更新健康证」，点开弹层（dialog + 标题 + 日期下限）', async () => {
    const { wrapper } = await mountToday({ employee: { health_cert_state: 'expired' } })
    const action = wrapper.get('.health-cert-action')
    expect(action.text()).toBe('更新健康证')

    await action.trigger('click')
    const dialog = wrapper.get('.modal-overlay[role="dialog"]')
    expect(dialog.attributes('aria-modal')).toBe('true')
    expect(dialog.attributes('aria-labelledby')).toBe('health-cert-title')
    expect(wrapper.get('#health-cert-title').text()).toBe('更新健康证')
    // 背景 inert（焦点陷阱那一套的判据）。
    expect(wrapper.get('.tTop').attributes('inert')).toBeDefined()
    wrapper.unmount()
  })

  it('没选日期就保存：报「请选择新的健康证到期日」，不发 PATCH', async () => {
    const { wrapper, calls } = await mountToday({ employee: { health_cert_state: 'expired' } })
    await wrapper.get('.health-cert-action').trigger('click')
    await wrapper.get('.modal-box .btn-primary').trigger('click')
    await flushPromises()

    expect(wrapper.get('.modal-box .tL-err').text()).toBe('请选择新的健康证到期日')
    expect(patchCalls(calls)).toHaveLength(0)
    wrapper.unmount()
  })

  it('选了早于今天的日期：报「不能早于今天」，不发 PATCH', async () => {
    const { wrapper, calls } = await mountToday({ employee: { health_cert_state: 'expired' } })
    await wrapper.get('.health-cert-action').trigger('click')
    await wrapper.get('.date-stub').setValue('2000-01-01')
    await wrapper.get('.modal-box .btn-primary').trigger('click')
    await flushPromises()

    expect(wrapper.get('.modal-box .tL-err').text()).toBe('新的健康证到期日不能早于今天')
    expect(patchCalls(calls)).toHaveLength(0)
    wrapper.unmount()
  })

  it('选好到期日保存：PATCH body 正确，成功后横条消失并出现「已更新」回执', async () => {
    const { wrapper, calls } = await mountToday({ employee: { health_cert_state: 'expired' } })
    await wrapper.get('.health-cert-action').trigger('click')
    await wrapper.get('.date-stub').setValue('2027-09-13')
    await wrapper.get('#health-cert-note').setValue('新证已办')
    await wrapper.get('.modal-box .btn-primary').trigger('click')
    await flushPromises()

    const patches = patchCalls(calls)
    expect(patches).toHaveLength(1)
    expect(patches[0].method).toBe('PATCH')
    expect(patches[0].body).toEqual({ expires_on: '2027-09-13', note: '新证已办' })

    // 弹层关了、横条没了（state 从 expired 变 ok）、资料区出现回执。
    expect(wrapper.find('.modal-overlay').exists()).toBe(false)
    expect(wrapper.find('.health-cert-alert').exists()).toBe(false)
    expect(wrapper.get('.me-flash').text()).toContain('健康证已更新，有效期至 2027-09-13')
    wrapper.unmount()
  })

  it('服务端拒绝（400）：弹层里的错误原话照显示，横条不动', async () => {
    const { wrapper } = await mountToday({
      employee: { health_cert_state: 'expired' },
      patch: () => jsonResponse({ detail: '这个到期日没有对应的办理日期，请改选一天' }, 400),
    })
    await wrapper.get('.health-cert-action').trigger('click')
    await wrapper.get('.date-stub').setValue('2027-09-13')
    await wrapper.get('.modal-box .btn-primary').trigger('click')
    await flushPromises()

    expect(wrapper.get('.modal-box .tL-err').text()).toBe('这个到期日没有对应的办理日期，请改选一天')
    expect(wrapper.find('.health-cert-alert').exists()).toBe(true)
    wrapper.unmount()
  })
})
