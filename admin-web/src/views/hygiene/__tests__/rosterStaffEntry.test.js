// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HygieneRosterView from '../HygieneRosterView.vue'
import { STAFF_ENTRY_PATH } from '../../../utils/staffPaths'

// 花名册页那张员工入口码与可复制地址必须指向**新地址**（工作台的「我的」组）。
// 店员扫的是这张码，指错就是扫出一片空白 —— 所以这里真挂一次页面，断渲染出来的那条 URL。
// 路径本身不在这里写死：它来自 `utils/staffPaths.js` 的 `STAFF_ENTRY_PATH`（唯一一份判据，
// 值有它自己的单测），这里断的是「页面确实用了它、并且拼上了当前 origin」。
//
// 2026-10 改版：二维码与链接从页面主体**搬进了「邀请店员」弹层**（design §4）——
// 页面上不再有独立的入口卡片，所以断言发生在点开弹层之后。

function jsonResponse(data) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

beforeEach(() => {
  // 花名册那几条读接口：给一份空数据就够了（这一条只看入口那块）。
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ employees: [], zones: [] })))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('花名册页的员工入口（票 03 换新地址）', () => {
  it('入口卡片不再常驻：点开「邀请店员」才出现 origin + 员工入口常量', async () => {
    const wrapper = mount(HygieneRosterView)
    await flushPromises()
    await flushPromises()

    // 页面上没有独立的入口卡片（旧文案与那条链接都不该在没有弹层时存在）。
    expect(wrapper.find('.staff-entry-copy').exists()).toBe(false)

    const invite = wrapper.findAll('button').find((node) => node.text() === '邀请店员')
    expect(invite).toBeTruthy()
    await invite.trigger('click')
    await flushPromises()

    const shown = wrapper.get('.staff-entry-copy code').text()
    expect(shown).toBe(`${window.location.origin}${STAFF_ENTRY_PATH}`)
    expect(shown).toContain('/workbench/me/today')
    expect(shown).not.toContain('/staff/')

    wrapper.unmount()
  })
})
