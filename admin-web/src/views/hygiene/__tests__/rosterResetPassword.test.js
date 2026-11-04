// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HygieneRosterView from '../HygieneRosterView.vue'

/**
 * 花名册 · 重设登录密码（2026-11-02）。
 *
 * 老板要的那件事：员工忘了密码，店长在花名册里直接给他重设。这里压死三条：
 *
 * 1. **只有「重设」，没有「查看」** —— 库里只有哈希，页面不摆一个假装能看原文的入口；
 * 2. **两次输入一致是页面先判的那一刀**，不一致时一个请求都不发；
 * 3. 提交打的是 `POST /admin/roster/{id}/reset-password`，body 两个字段原样带上，
 *    成功后页面上要出现一句说清「旧密码和已登录的设备都失效」的结果提示。
 */

const ROW = {
  id: 11,
  name: '张三',
  phone: '13800000000',
  job_title: '案板',
  permission: '普通员工',
  admin_caps: [],
  approved: true,
  resigned: false,
  resigned_at: null,
  created_at: '2026-10-07T14:32:11+08:00',
  id_card_no: '110101199003077213',
  health_cert_date: '2025-12-01',
  health_cert_expires_on: '2026-12-01',
  health_cert_state: 'ok',
  health_cert_days_left: 40,
  base_salary: 6000,
  hire_date: '2026-01-01',
  profile_incomplete: false,
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

let resetCalls = []
let resetFails = null

beforeEach(() => {
  resetCalls = []
  resetFails = null
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const path = String(url).split('?')[0]
    const method = (options.method || 'GET').toUpperCase()
    if (method === 'POST' && path.endsWith('/reset-password')) {
      resetCalls.push({ path, body: JSON.parse(options.body) })
      if (resetFails) return jsonResponse({ detail: resetFails }, 400)
      return jsonResponse({ success: true })
    }
    if (path === '/api/hygiene/admin/roster') {
      return jsonResponse({ employees: [ROW], zones: [] })
    }
    return jsonResponse({})
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function mountRoster() {
  const wrapper = mount(HygieneRosterView)
  await flushPromises()
  await flushPromises()
  return wrapper
}

async function openDrawer(wrapper) {
  await wrapper.get('.roster-row').trigger('click')
  await flushPromises()
}

/** 抽屉里那颗「重设密码」（打开弹层的入口，不是弹层里提交那颗）。 */
function drawerResetButton(wrapper) {
  return wrapper.findAll('.roster-password-row button')
    .find((node) => node.text() === '重设密码')
}

/** 弹层里提交那颗。 */
function submitButton(wrapper) {
  return wrapper.get('.roster-password-overlay .btn-primary')
}

async function openPasswordForm(wrapper) {
  await openDrawer(wrapper)
  await drawerResetButton(wrapper).trigger('click')
  await flushPromises()
}

describe('花名册 · 重设登录密码', () => {
  it('抽屉里只有「重设」，没有查看原文的入口', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper)

    const row = wrapper.get('.roster-password-row')
    expect(row.text()).toContain('••••••••')
    expect(row.text()).toContain('重设密码')
    // 没有任何"显示密码 / 查看密码"的字样：原文在库里不存在，页面也不该假装能看。
    expect(wrapper.text()).not.toMatch(/查看密码|显示密码/)

    wrapper.unmount()
  })

  it('两次输入不一致：不发请求，直接在弹层里说清', async () => {
    const wrapper = await mountRoster()
    await openPasswordForm(wrapper)

    const inputs = wrapper.findAll('.roster-password-overlay input')
    expect(inputs).toHaveLength(2)
    await inputs[0].setValue('new-password-123')
    await inputs[1].setValue('new-password-124')
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(resetCalls).toHaveLength(0)
    expect(wrapper.get('.roster-password-overlay').text()).toContain('两次输入的新密码不一致')

    wrapper.unmount()
  })

  it('提交打的是 reset-password，成功后提示旧密码与已登录设备一并失效', async () => {
    const wrapper = await mountRoster()
    await openPasswordForm(wrapper)

    const inputs = wrapper.findAll('.roster-password-overlay input')
    await inputs[0].setValue('new-password-123')
    await inputs[1].setValue('new-password-123')
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(resetCalls).toHaveLength(1)
    expect(resetCalls[0].path).toBe('/api/hygiene/admin/roster/11/reset-password')
    expect(resetCalls[0].body).toEqual({
      new_password: 'new-password-123',
      confirm_password: 'new-password-123',
    })
    // 弹层关掉、结果提示里明确说旧密码与已登录设备都失效（别让店长以为只是换了个密码）。
    // 提示放在抽屉里那份（抽屉还开着，页头那份被它盖住）。
    expect(wrapper.find('.roster-password-overlay').exists()).toBe(false)
    const hint = wrapper.get('.roster-saved-inline').text()
    expect(hint).toContain('张三')
    expect(hint).toContain('旧密码')
    expect(hint).toContain('已登录的设备')

    wrapper.unmount()
  })

  it('服务端拒绝时把中文原话摆进弹层，弹层不关', async () => {
    resetFails = '密码至少 8 位'
    const wrapper = await mountRoster()
    await openPasswordForm(wrapper)

    const inputs = wrapper.findAll('.roster-password-overlay input')
    await inputs[0].setValue('short')
    await inputs[1].setValue('short')
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(resetCalls).toHaveLength(1)
    expect(wrapper.get('.roster-password-overlay').text()).toContain('密码至少 8 位')

    wrapper.unmount()
  })

  it('取消 / 关闭时把输入清空，下次打开不带上一次的值', async () => {
    const wrapper = await mountRoster()
    await openPasswordForm(wrapper)

    await wrapper.findAll('.roster-password-overlay input')[0].setValue('typed-once')
    const cancel = wrapper.findAll('.roster-password-overlay button')
      .find((node) => node.text() === '取消')
    await cancel.trigger('click')
    await flushPromises()
    expect(wrapper.find('.roster-password-overlay').exists()).toBe(false)

    await drawerResetButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.roster-password-overlay input')[0].element.value).toBe('')

    wrapper.unmount()
  })
})
