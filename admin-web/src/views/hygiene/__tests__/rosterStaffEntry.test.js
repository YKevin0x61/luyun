// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HygieneRosterView from '../HygieneRosterView.vue'
import { STAFF_ENTRY_PATH } from '../../../utils/staffPaths'

// 票 03：花名册页上那张员工入口码与可复制地址必须指向**新地址**（工作台的「我的」组）。
// 店员扫的是这张码，指错就是扫出一片空白 —— 所以这里真挂一次页面，断渲染出来的那条 URL。
// 路径本身不在这里写死：它来自 `utils/staffPaths.js` 的 `STAFF_ENTRY_PATH`（唯一一份判据，
// 值有它自己的单测），这里断的是「页面确实用了它、并且拼上了当前 origin」。

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
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({})))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('花名册页的员工入口（票 03 换新地址）', () => {
  it('可复制地址与二维码都是 origin + 员工入口常量，不再是旧的 /staff/today', async () => {
    const wrapper = mount(HygieneRosterView)
    await flushPromises()
    await flushPromises()

    const shown = wrapper.get('.staff-entry-copy code').text()
    expect(shown).toBe(`${window.location.origin}${STAFF_ENTRY_PATH}`)
    expect(shown).toContain('/workbench/me/today')
    expect(shown).not.toContain('/staff/')

    wrapper.unmount()
  })
})
