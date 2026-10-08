// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HygieneRosterView from '../HygieneRosterView.vue'
import {
  ADMIN_CAP_STAFF_DEFS,
  ADMIN_CAP_SUPERVISOR_ONLY_DEFS,
} from '../../../utils/adminCaps'

/**
 * 花名册的「管理权限」（2026-10 改版：动作搬进编辑抽屉）。
 *
 * 三件事在这一组里压死：
 * 1. **可勾的只有员工端真有执行点的三项**；只读那七项**整块不渲染** —— 名字、说明、
 *    连 `title` 都不给（design §3.4）。页面不显示 ≠ 键可以抹掉，见第 4 条。
 * 2. **「卫生权限」不再是下拉**：它只是服务端派生的人话标签，页面也不再 PATCH `permission`。
 * 3. **勾选不发请求**：点「保存」才发**一次** PATCH，`admin_caps` 是整组（归一化成契约顺序），
 *    并且必须把库里只读七项已有的值原样带回 —— 整组替换少带一个就是静默删权限。
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

/** 服务端那一份名单：PATCH 之后 GET 回的就是它（用来验"草稿跟着服务端走"）。 */
let rows = [ROW]
let patchCalls = []
/** 保存中那一次请求挂住不返回：用来验"保存中禁用"。 */
let patchGate = null
let patchGateRelease = () => {}

function serverRow(overrides = {}) {
  return { ...ROW, ...overrides }
}

beforeEach(() => {
  rows = [serverRow()]
  patchCalls = []
  patchGate = null
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const path = String(url).split('?')[0]
    const method = (options.method || 'GET').toUpperCase()
    if (method === 'PATCH') {
      patchCalls.push({ path, body: JSON.parse(options.body) })
      if (patchGate) await patchGate
      // 服务端说了算：这里故意只留其中一项，草稿必须跟着它走。
      rows = [serverRow({ admin_caps: ['daily_review'] })]
      return jsonResponse({ employee: rows[0] })
    }
    if (path === '/api/hygiene/admin/roster') {
      return jsonResponse({ employees: rows, zones: [] })
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

/** 点列表行打开抽屉（改版后批准与保存都在抽屉里）。 */
async function openDrawer(wrapper) {
  await wrapper.get('.roster-row').trigger('click')
  await flushPromises()
}

function capBoxes(wrapper) {
  return wrapper.findAll('.roster-cap input')
}

function saveButton(wrapper) {
  return wrapper.findAll('button').find((node) => node.text() === '保存')
}

describe('花名册 · 管理权限（抽屉）', () => {
  it('可勾的只有员工端真正生效的四项；「卫生权限」只剩只读标签，没有下拉', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper)

    expect(wrapper.findAll('.roster-cap').map((node) => node.text()))
      .toEqual(ADMIN_CAP_STAFF_DEFS.map((item) => item.label))
    expect(capBoxes(wrapper)).toHaveLength(4)

    // 抽屉里一个 `<select>` 都没有：卫生权限是派生标签，由服务端给值。
    expect(wrapper.findAll('select')).toHaveLength(0)
    expect(wrapper.text()).toContain('卫生权限')
    expect(wrapper.text()).toContain('普通员工')

    wrapper.unmount()
  })

  it('只读七项整块不渲染：名字、说明、连 title 都不出现', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper)

    const html = wrapper.html()
    for (const cap of ADMIN_CAP_SUPERVISOR_ONLY_DEFS) {
      expect(html).not.toContain(cap.label)
      expect(html).not.toContain(cap.note)
    }
    expect(wrapper.find('.roster-caps-readonly').exists()).toBe(false)

    wrapper.unmount()
  })

  it('三个勾一次改完只在本地，点「保存」才发一次 PATCH（整组、按契约顺序、不含 permission）', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper)
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
    expect(patchCalls[0].body).toMatchObject({ name: '张三', job_title: '案板' })
    // `permission` 不再由页面写（服务端按开关派生）。
    expect(patchCalls[0].body).not.toHaveProperty('permission')

    wrapper.unmount()
  })

  it('库里若已存着只读七项的值，保存时原样带回，不静默抹掉', async () => {
    // 历史数据（这七项从来没有可勾的界面，但键保留着，库里可能有值）。
    rows = [serverRow({ admin_caps: ['daily_review', 'data', 'boards'] })]
    const wrapper = await mountRoster()
    await openDrawer(wrapper)

    // 界面上看不见 data / boards 的勾。
    expect(capBoxes(wrapper)).toHaveLength(4)

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
    await openDrawer(wrapper)
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
    expect(hint.text()).toBe('已保存')

    wrapper.unmount()
  })

  it('行内标签的项数只数员工端真正生效的三项（超管专属七项不计入）', async () => {
    rows = [serverRow({
      permission: '管理员',
      admin_caps: ['daily_review', 'fix', 'data', 'boards'],
    })]
    const wrapper = await mountRoster()

    const chip = wrapper.get('.roster-chip')
    expect(chip.text()).toBe('管理员 · 2 项')
    expect(chip.attributes('title')).toBe('管理权限的实际项数')

    wrapper.unmount()
  })

  it('边缘情况照实显示「管理员 · 0 项」：标签派生为管理员，但一项可用的都没有', async () => {
    rows = [serverRow({ permission: '管理员', admin_caps: ['data', 'clock'] })]
    const wrapper = await mountRoster()

    expect(wrapper.get('.roster-chip').text()).toBe('管理员 · 0 项')

    wrapper.unmount()
  })

  it('普通员工不看项数：写「普通员工 · 无」', async () => {
    const wrapper = await mountRoster()

    expect(wrapper.get('.roster-chip').text()).toBe('普通员工 · 无')

    wrapper.unmount()
  })
})
