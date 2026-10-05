// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HygieneRosterView from '../HygieneRosterView.vue'
import {
  ADMIN_CAP_STAFF_DEFS,
  ADMIN_CAP_SUPERVISOR_ONLY_DEFS,
} from '../../../utils/adminCaps'

/**
 * 花名册的「管理权限」（2026-10-05 逐项放权；2026-10-06 真机实测后收敛可勾面）。
 *
 * 真机实测的教训：十项并排画着的时候，那七项**勾了不生效也不说**——勾「数据与归档」→ 保存
 * 成功 → 员工端零变化（它们对应的活全在电脑端，员工端没有入口）。所以现在只有员工端真有
 * 执行点的三项可勾，其余七项降级成只读说明。
 *
 * 真挂一遍页面，压五件读源码看不准的事：
 * 1. **可勾的只有三项**，七项只读，且只读区里没有任何可勾控件；
 * 2. 文案说清作用范围（不再说"现场七页"——现场是八页）；
 * 3. **勾选不发请求**：改完只在本地，点「保存」才发**一次** PATCH，body 里 `admin_caps`
 *    是整组（且归一化成契约顺序）；
 * 4. **只读那七项若库里已有值，保存时原样带回** —— 整组替换不能变成静默删权限；
 * 5. 保存给一次 `role="status"` 的反馈（原来保存完页面上什么都不变）。
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

describe('花名册 · 管理权限', () => {
  it('可勾的只有员工端真正生效的三项，「卫生权限」下拉还在', async () => {
    const wrapper = await mountRoster()

    expect(wrapper.findAll('.roster-cap').map((node) => node.text()))
      .toEqual(ADMIN_CAP_STAFF_DEFS.map((item) => item.label))
    expect(capBoxes(wrapper)).toHaveLength(3)

    // 人话标签那一列没被开关顶掉（它决定的是员工 vs 管理员这个显示用标签）。
    const selects = wrapper.findAll('select')
    expect(selects.length).toBeGreaterThan(0)
    expect(selects[0].element.value).toBe('普通员工')
    expect(wrapper.text()).toContain('管理员')

    wrapper.unmount()
  })

  it('其余七项是只读说明：列出来了，但里面一个可勾控件都没有', async () => {
    const wrapper = await mountRoster()

    expect(wrapper.findAll('.roster-caps-readonly li').map((node) => node.text()))
      .toEqual(ADMIN_CAP_SUPERVISOR_ONLY_DEFS.map((item) => `${item.label}：${item.note}`))
    // 关键：只读区不能藏着勾 —— 藏一个就等于"勾了不生效"又回来了。
    expect(wrapper.findAll('.roster-caps-readonly input')).toHaveLength(0)

    wrapper.unmount()
  })

  it('说清作用范围：只有这三项在员工手机端的「卫生」页生效', async () => {
    const wrapper = await mountRoster()
    const hint = wrapper.get('.roster-caps-hint').text()

    expect(hint).toContain('员工手机端')
    expect(hint).toContain('卫生')
    expect(hint).toContain('勾了就能用')
    // 也说明了「保存」才生效（勾选不即时提交）。
    expect(hint).toContain('保存')
    // 「现场七页」是旧口径：现场是八页（含卫生趋势），而且这句话已经挪进只读区说明。
    expect(hint).not.toContain('七页')
    expect(wrapper.text()).toContain('员工端暂无入口')

    wrapper.unmount()
  })

  it('三个勾一次改完只在本地，点「保存」才发一次 PATCH（整组、按契约顺序）', async () => {
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

  it('库里若已存着只读七项的值，保存时原样带回，不静默抹掉', async () => {
    // 历史数据（这七项从来没有可勾的界面，但键保留着，库里可能有值）。
    serverCaps = ['daily_review', 'data', 'boards']
    const wrapper = await mountRoster()

    // 界面上看不见 data / boards 的勾。
    expect(capBoxes(wrapper)).toHaveLength(3)

    // 再加一项「专项验收」，然后保存。
    await capBoxes(wrapper)[1].setValue(true)
    await flushPromises()
    await saveButton(wrapper).trigger('click')
    await flushPromises()

    // 整组替换里必须仍带着 data / boards：少一个就是静默删权限。
    expect(patchCalls).toHaveLength(1)
    expect(patchCalls[0].body.admin_caps)
      .toEqual(['daily_review', 'deep_review', 'boards', 'data'])

    wrapper.unmount()
  })

  it('保存中开关禁用，成功后草稿同步成服务端返回值，并给一次 role="status" 的反馈', async () => {
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

    // 保存成功要有一次可见反馈（真机实测：原来页面上什么都不变）。
    const hint = wrapper.get('.roster-saved')
    expect(hint.attributes('role')).toBe('status')
    expect(hint.text()).toContain('已保存')

    wrapper.unmount()
  })
})
