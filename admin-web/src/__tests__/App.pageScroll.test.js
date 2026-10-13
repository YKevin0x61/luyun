// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import App from '../App.vue'
import appSource from '../App.vue?raw'
import { pageMeta } from '../router/pageRoutes.js'

// 「卫生验收八页滑不动」（2026-10-12 报）的守卫。
//
// 背景：页面区的滚动**只有一处** —— `.app-shell` 里那个 `.page-body`（`theme.css`：
// `flex:1; min-height:0; overflow-y:auto`，`body` 自己 `overflow:hidden`）。工作台各组
// （今天 / 人事 / 现场 / 后勤 / 我的）都是 `meta.standalone` 的页，App 给页面区加
// `.page-body-standalone`（padding 0 + 滚动），统一壳的 `.wb-top` sticky 也挂在这条上。
//
// 现场那八页原先还多一个 `.page-body-hygiene { overflow: hidden }`：那是旧卫生壳
// （`.hygiene-app` 的 `height:100%` 整屏锁）让 `.hy-main` **在内部自己滚**的配套。三壳
// 合一（ADR 0105）删掉了整屏锁，这条却留着 —— 外层被裁、内层高度是 auto（撑到内容高，
// `clientHeight == scrollHeight`），中间没有任何人能滚，八页就整段卡在视口里。
//
// 这里钉住两件事：① 现场页的页面区**不带**那个锁；② App.vue 的样式里不再有把页面区
// 锁成裁剪盒的规则（类名没人挂、规则还在一样危险）。

class FakeWebSocket {
  constructor() {
    this.readyState = 0
  }

  send() {}

  close() {
    this.readyState = 3
  }
}

// 现场页（真实清单那一行）与人事页（对照：它一直是好的）。
const FLOOR = '/workbench/floor/zones'
const HR = '/workbench/hr/calendar'

const ROUTES = [
  { path: '/', component: { template: '<div />' }, meta: pageMeta('/') },
  { path: FLOOR, component: { template: '<div />' }, meta: pageMeta(FLOOR) },
  { path: HR, component: { template: '<div />' }, meta: pageMeta(HR) },
]

function jsonResponse(data) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountApp(path) {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push(path)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(App, {
    global: {
      plugins: [router, pinia],
      stubs: { NavBar: true, PwaUpdateBanner: true, ImageUploadQueuePanel: true },
    },
  })
  await flushPromises()
  return wrapper
}

/** 把 `<style>` 块里的注释去掉，再按规则切开 —— 注释里提到 `overflow: hidden` 不算数。 */
function pageBodyRules(source) {
  const styleBlock = source.slice(source.indexOf('<style'))
  const noComments = styleBlock.replace(/\/\*[\s\S]*?\*\//g, '')
  return noComments
    .split('}')
    .map((chunk) => chunk.trim())
    .filter((chunk) => chunk.includes('{'))
    .map((chunk) => ({
      selector: chunk.slice(0, chunk.indexOf('{')).trim(),
      body: chunk.slice(chunk.indexOf('{') + 1),
    }))
    .filter((rule) => rule.selector.includes('.page-body'))
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse([])))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('页面区滚动（卫生验收八页滑不动的回归）', () => {
  it('现场页的页面区只带 standalone 那一条，不带「内部自己滚动」的锁', async () => {
    const wrapper = await mountApp(FLOOR)
    const classes = wrapper.find('.page-body').classes()

    expect(classes).toContain('page-body-standalone')
    expect(classes).not.toContain('page-body-hygiene')
    wrapper.unmount()
  })

  it('人事页与现场页走同一条（页面区的类名不再按组分叉）', async () => {
    const wrapper = await mountApp(HR)
    expect(wrapper.find('.page-body').classes()).toEqual([
      'page-body', 'luyun-scrollbar', 'page-body-standalone',
    ])
    wrapper.unmount()
  })

  it('App.vue 里没有把页面区锁成裁剪盒的规则', () => {
    const rules = pageBodyRules(appSource)
    expect(rules.length).toBeGreaterThan(0)
    for (const rule of rules) {
      expect(rule.body).not.toMatch(/overflow(-y)?:\s*hidden/)
    }
  })

  it('页面区自己就是滚动容器（overflow-y: auto 还在）', () => {
    const standalone = pageBodyRules(appSource)
      .find((rule) => rule.selector.includes('.page-body-standalone'))
    expect(standalone).toBeTruthy()
    expect(standalone.body).toMatch(/overflow-y:\s*auto/)
  })
})
