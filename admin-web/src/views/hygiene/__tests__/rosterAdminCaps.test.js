// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HygieneRosterView from '../HygieneRosterView.vue'
import { ADMIN_CAP_DEFS } from '../../../utils/adminCaps'

/**
 * 花名册的「管理权限」十项开关（2026-10-05 用户裁定：由超级管理员逐项放权）。
 *
 * 真挂一遍页面，压三件读源码看不准的事：
 * 1. 十个开关按**契约顺序**渲染，那个「卫生权限」下拉还在（它还是人话标签）；
 * 2. **勾选不发请求**：十个勾改完只在本地，点「保存」才发**一次** PATCH，
 *    body 里 `admin_caps` 是整组（且归一化成契约顺序）；
 * 3. 保存中开关禁用，成功后草稿跟着**服务端返回值**走（不是"我勾了什么就一直是什么"）。
 */

const ROW = {
  id: 11,
  name: '张三',
  phone: '13800000000',
  job_title: '案板',
  permission: '普通员工',
  admin_caps: [],
  approved: true,
  disabled: false,
  shift: '白班',
  zone_id: null,
  zone_name: null,
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 服务端那一份 `admin_caps`：PATCH 之后 GET 回的就是它（用来验"草稿跟着服务端走"）。 */
let serverCaps = []
let patchCalls = []
/** 保存中那一次请求挂住不返回：用来验"保存中禁用"。 */
let patchGate = null
let patchGateRelease = () => {}

beforeEach(() => {
  serverCaps = []
  patchCalls = []
  patchGate = null
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const path = String(url).split('?')[0]
    const method = (options.method || 'GET').toUpperCase()
    if (method === 'PATCH') {
      patchCalls.push({ path, body: JSON.parse(options.body) })
      if (patchGate) await patchGate
      // 服务端说了算：这里故意只回其中一项，草稿必须跟着它走。
      serverCaps = ['daily_review']
      return jsonResponse({ employee: { ...ROW, admin_caps: serverCaps } })
    }
    if (path === '/api/hygiene/admin/roster') {
      return jsonResponse({ employees: [{ ...ROW, admin_caps: serverCaps }], zones: [] })
    }
    if (path === '/api/scheduling/calendar') return jsonResponse({ today: '2026-10-05' })
    if (path === '/api/scheduling/shifts') return jsonResponse({ shifts: [] })
    if (path === '/api/scheduling/day') return jsonResponse({ groups: [], off_people: [] })
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

function capBoxes(wrapper) {
  return wrapper.findAll('.roster-cap input')
}

function saveButton(wrapper) {
  return wrapper.findAll('button').find((node) => node.text() === '保存')
}

describe('花名册 · 管理权限十项开关', () => {
  it('十个开关按契约顺序渲染，且「卫生权限」下拉还在', async () => {
    const wrapper = await mountRoster()

    expect(wrapper.findAll('.roster-cap').map((node) => node.text()))
      .toEqual(ADMIN_CAP_DEFS.map((item) => item.label))
    expect(capBoxes(wrapper)).toHaveLength(10)

    // 人话标签那一列没被开关顶掉（它决定的是员工 vs 管理员这个显示用标签）。
    const selects = wrapper.findAll('select')
    expect(selects.length).toBeGreaterThan(0)
    expect(selects[0].element.value).toBe('普通员工')
    expect(wrapper.text()).toContain('管理员')

    wrapper.unmount()
  })

  it('说清作用范围：只在员工手机端的「卫生」页生效，现场七页仍归超级管理员', async () => {
    const wrapper = await mountRoster()
    const hint = wrapper.get('.roster-caps-hint').text()
    expect(hint).toContain('只在员工手机端的「卫生」页生效')
    expect(hint).toContain('现场七页')
    expect(hint).toContain('超级管理员')
    // 也说明了「保存」才生效（勾选不即时提交）。
    expect(hint).toContain('保存')
    wrapper.unmount()
  })

  it('十个勾一次改完只在本地，点「保存」才发一次 PATCH（整组、按契约顺序）', async () => {
    const wrapper = await mountRoster()
    const boxes = capBoxes(wrapper)

    // 故意与契约顺序反着勾：先「整改单」（第 3 项）再「日常验收」（第 1 项）。
    await boxes[2].setValue(true)
    await boxes[0].setValue(true)
    await flushPromises()

    // 勾选期间一个请求都不该发。
    expect(patchCalls).toHaveLength(0)

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(patchCalls).toHaveLength(1)
    expect(patchCalls[0].path).toBe('/api/hygiene/admin/roster/11')
    // 整组替换 + 归一化：顺序按契约（不是勾选顺序），别的字段照旧一起发。
    expect(patchCalls[0].body.admin_caps).toEqual(['daily_review', 'fix'])
    expect(patchCalls[0].body).toMatchObject({
      name: '张三',
      job_title: '案板',
      permission: '普通员工',
    })

    wrapper.unmount()
  })

  it('保存中开关禁用，成功后草稿同步成服务端返回值', async () => {
    const wrapper = await mountRoster()
    await capBoxes(wrapper)[2].setValue(true)
    await flushPromises()

    patchGate = new Promise((resolve) => { patchGateRelease = resolve })
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(capBoxes(wrapper)[2].element.disabled).toBe(true)

    patchGateRelease()
    await flushPromises()
    await flushPromises()

    // 服务端只回了 daily_review：界面跟着它走，不再是我勾的那一项。
    expect(patchCalls).toHaveLength(1)
    const boxes = capBoxes(wrapper)
    expect(boxes[0].element.checked).toBe(true)
    expect(boxes[2].element.checked).toBe(false)
    expect(boxes[0].element.disabled).toBe(false)

    wrapper.unmount()
  })
})
