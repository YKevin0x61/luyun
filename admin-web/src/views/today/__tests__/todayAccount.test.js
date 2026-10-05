// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/**
 * 「我的」那一块（2026-10-05 用户裁定：**从卫生页的「我」整格搬来**）。
 *
 * 搬的是行为，所以这一条按行为压：三行入口开得出表单、手机号那位登录账号改之前要确认、
 * 两次新密码不一致就地拦住、保存时发的是哪一条请求与什么 body。同目录 `todayView.test.js`
 * 是源码契约式的断言（结构、文案），这一条补的是"点下去真的这么走"。
 */

const ME = {
  employee: { id: 7, name: '余威威' },
  days: [{
    business_date: '2026-10-05',
    is_today: true,
    scheduled: true,
    shift_id: 1,
    shift_name: '白班',
    zone_id: 3,
    zone_name: '案板',
    leave: false,
  }],
}

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
  },
  daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
  deep_clock: { hhmm: '20:00' },
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 记下发出去的写请求（方法 + 路径 + body），读请求一律按表返回。 */
async function mountToday({ onPatch } = {}) {
  const writes = []
  const fetchMock = vi.fn(async (url, init = {}) => {
    const path = String(url).split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    if (method !== 'GET') {
      writes.push({ path, method, body: init.body ? JSON.parse(init.body) : null })
      if (onPatch) {
        const override = onPatch({ path, method })
        if (override) return jsonResponse(override.data, override.status || 200)
      }
      if (path === '/api/hygiene/staff/me') {
        return jsonResponse({ employee: { ...HYGIENE_ME.employee, name: '余威威', phone: '13900000000' } })
      }
      return jsonResponse({ ok: true })
    }
    if (path === '/api/scheduling/me') return jsonResponse(ME)
    if (path === '/api/scheduling/me/requests') return jsonResponse({ requests: [], incoming: [] })
    if (path === '/api/hygiene/staff/me') return jsonResponse(HYGIENE_ME)
    if (path === '/api/hygiene/staff/daily-work') return jsonResponse({ items: [] })
    if (path === '/api/hygiene/staff/attire') return jsonResponse({ attire: { required: false } })
    // 「我的成绩」卡（2026-10-05）读的那条：这一条用例不管它，但得答得上，
    // 否则挂载时它会以 404 落进错误态（真单测在 todayScore.test.js）。
    if (path === '/api/hygiene/staff/me/stats') {
      return jsonResponse({ days: 7, pass_rate: null, first_pass: 0, rejected: 0, reasons: [] })
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const { default: TodayView } = await import('../TodayView.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/workbench/me/today', component: { template: '<div />' } }],
  })
  await router.push('/workbench/me/today')
  await router.isReady()

  const wrapper = mount(TodayView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, writes }
}

/** 找到那三行入口里的一行（按钮文案是「修改」/「怎么改」）。 */
function rowButton(wrapper, label) {
  const row = wrapper.findAll('.tL-list li').find((li) => li.text().includes(label))
  if (!row) throw new Error(`找不到「${label}」那一行`)
  return row.get('button')
}

// jsdom 里没有滚动实现：下面那条用例自己补一个空壳，用完还回去。
const originalScrollIntoView = Element.prototype.scrollIntoView

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  Element.prototype.scrollIntoView = originalScrollIntoView
})

describe('「今天」页尾的账号设置（从卫生页搬来）', () => {
  it('三行入口都在，资料用的是卫生那条 /staff/me（不额外发请求）', async () => {
    const { wrapper } = await mountToday()
    for (const label of ['修改个人信息', '修改密码', '重新选择区域和班次']) {
      expect(wrapper.text()).toContain(label)
    }
    expect(wrapper.get('.me-meta').text()).toContain('余威威')
    expect(wrapper.get('.me-meta').text()).toContain('13800000000')
    expect(wrapper.get('.me-meta').text()).toContain('案板')
    expect(wrapper.get('.me-meta').text()).toContain('18:00 前交')
    expect(wrapper.get('.me-meta').text()).toContain('20:00 前做完')
  })

  it('改手机号：先确认再发 PATCH，body 是姓名 + 新号', async () => {
    const { wrapper, writes } = await mountToday()
    await rowButton(wrapper, '修改个人信息').trigger('click')

    const nameInput = wrapper.get('#profile-name')
    const phoneInput = wrapper.get('#profile-phone')
    expect(nameInput.element.value).toBe('余威威')
    expect(phoneInput.element.value).toBe('13800000000')

    await phoneInput.setValue('13900000000')
    // 表单里那个「保存」（弹层里那颗，不是行上那颗）。
    const save = wrapper.findAll('button').find((btn) => btn.text() === '保存')
    await save.trigger('click')
    await flushPromises()
    // 还没确认：一条 PATCH 都不该出去。
    expect(writes).toEqual([])
    expect(wrapper.text()).toContain('确认改手机号')

    const confirm = wrapper.findAll('button').find((btn) => btn.text() === '确认改号')
    await confirm.trigger('click')
    await flushPromises()
    expect(writes).toEqual([
      {
        path: '/api/hygiene/staff/me',
        method: 'PATCH',
        body: { name: '余威威', phone: '13900000000' },
      },
    ])
    expect(wrapper.text()).toContain('个人信息已保存，手机号下次登录生效。')
  })

  it('手机号格式不对就地拦住，不发请求', async () => {
    const { wrapper, writes } = await mountToday()
    await rowButton(wrapper, '修改个人信息').trigger('click')
    await wrapper.get('#profile-phone').setValue('1390000')
    const save = wrapper.findAll('button').find((btn) => btn.text() === '保存')
    await save.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('手机号格式不对，应该是 11 位、以 1 开头的号码。')
    expect(writes).toEqual([])
  })

  it('只改姓名不弹确认框，直接保存', async () => {
    const { wrapper, writes } = await mountToday()
    await rowButton(wrapper, '修改个人信息').trigger('click')
    await wrapper.get('#profile-name').setValue('余威威威')
    const save = wrapper.findAll('button').find((btn) => btn.text() === '保存')
    await save.trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('确认改手机号')
    expect(writes).toHaveLength(1)
    expect(writes[0]).toMatchObject({
      path: '/api/hygiene/staff/me',
      method: 'PATCH',
      body: { name: '余威威威', phone: '13800000000' },
    })
  })

  it('改密码：两次不一致不发请求；一致了发三个字段并清空重来', async () => {
    const { wrapper, writes } = await mountToday()
    await rowButton(wrapper, '修改密码').trigger('click')
    await wrapper.get('#password-current').setValue('oldpass123')
    await wrapper.get('#password-new').setValue('newpass123')
    await wrapper.get('#password-confirm').setValue('newpass124')
    const submit = wrapper.findAll('button').find((btn) => btn.text() === '修改密码')
    await submit.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('两次输入的新密码不一致')
    expect(writes).toEqual([])

    await wrapper.get('#password-confirm').setValue('newpass123')
    await wrapper.findAll('button').find((btn) => btn.text() === '修改密码').trigger('click')
    await flushPromises()
    expect(writes).toEqual([{
      path: '/api/hygiene/staff/password',
      method: 'PATCH',
      body: {
        current_password: 'oldpass123',
        new_password: 'newpass123',
        confirm_password: 'newpass123',
      },
    }])
    expect(wrapper.text()).toContain('密码已修改，其他设备上的登录已失效。')
  })

  it('第三行不自选：点一下只说清由排班决定，并把页面送回顶上那张排班卡', async () => {
    const { wrapper, writes } = await mountToday()
    const scrolled = []
    Element.prototype.scrollIntoView = function scrollIntoView(options) {
      scrolled.push({ el: this, options })
    }
    await rowButton(wrapper, '重新选择区域和班次').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('今天上哪个班、在哪个区由排班决定；要改哪一天，找店长在排班页改。')
    expect(scrolled).toHaveLength(1)
    // 一个选择器都不该弹出来，也没有任何写请求（员工改不了班次与区）。
    expect(wrapper.find('select').exists()).toBe(false)
    expect(writes).toEqual([])
  })
})
