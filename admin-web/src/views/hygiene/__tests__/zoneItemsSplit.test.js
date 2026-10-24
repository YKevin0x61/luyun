// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/**
 * 「工作区 · 日常清单」按断点分流（2026-10-19）：
 * 桌面仍在工作区页右栏（两栏），手机整块隐藏、从每张工作区卡片的「查看清单 ›」进独立页
 * `/workbench/floor/zone-items?zone=<id>`。两边共用 `HygieneZoneItemsPanel`。
 *
 * 组件的结构性断言按源码断（清单整块是否真的只剩一份）；新页的选中与渲染按真实挂载断
 * （`?zone=` 选中、项数、返回链接）。路由注册由 Lead 另加，这里不依赖真实 router 表。
 */
const here = dirname(fileURLToPath(import.meta.url))
const zonesPage = readFileSync(join(here, '../HygieneZonesView.vue'), 'utf8')
const itemsPage = readFileSync(join(here, '../HygieneZoneItemsView.vue'), 'utf8')
const panel = readFileSync(join(here, '../../../components/hygiene/HygieneZoneItemsPanel.vue'), 'utf8')
const editor = readFileSync(join(here, '../../../components/hygiene/HygieneItemEditor.vue'), 'utf8')

describe('工作区 · 日常清单：桌面两栏 / 手机独立页', () => {
  it('工作区页右栏渲染组件，手机档隐藏并给每张工作区卡片一个入口', () => {
    expect(zonesPage).toMatch(/<HygieneZoneItemsPanel :zone="selected" @changed="refreshPage"/)
    // 包装层 + 手机隐藏
    expect(zonesPage).toMatch(/class="zone-items-wrap"/)
    const phone = zonesPage.slice(zonesPage.indexOf('@media (max-width: 720px)'))
    expect(phone).toMatch(/\.zone-items-wrap \{ display: none; \}/)
    // 桌面隐藏、手机显示「查看清单 ›」
    expect(zonesPage).toMatch(/\.zone-items-link \{[^}]*display: none;/)
    expect(phone).toMatch(/\.zone-items-link \{ display: flex; width: 100%; \}/)
    expect(zonesPage).toMatch(/path: '\/workbench\/floor\/zone-items'/)
    expect(zonesPage).toMatch(/query: \{ zone: zone\.id \}/)
    expect(zonesPage).toMatch(/>查看清单 ›</)
  })

  it('清单整块只留一份：页面里不再有编辑区 / 上传 / 删除检查项', () => {
    for (const gone of ['startEditMarkup(', 'startReplace(', 'imageUploads.enqueue(', 'class="item-editor"', 'deleteItemTarget']) {
      expect(zonesPage, `页面不该再有 ${gone}`).not.toContain(gone)
    }
  })

  it('组件保留 class 锚点；编辑器单独一层，挂在被编辑项之后', () => {
    for (const cls of ['wbx-panel', 'wbx-ph', 'item-list', 'item-row', 'item-thumb', 'item-meta', 'item-actions', 'wbx-empty-bar', 'wbx-fold-entry']) {
      expect(panel).toContain(cls)
    }
    for (const cls of ['item-editor', 'wbx-subhead', 'editor-lead', 'editor-field', 'mark-tools', 'mark-list', 'editor-actions']) {
      expect(editor).toContain(cls)
    }
    // 就地渲染：template v-for 里每一项后面紧跟 `li.item-row.is-editor`
    expect(panel).toMatch(/<template v-for="item in zone\.items" :key="item\.id">/)
    expect(panel).toMatch(/<li v-if="editingItemId === item\.id" class="item-row is-editor">/)
    expect(panel).toMatch(/<HygieneItemEditor[\s\S]{0,160}:mode="editorMode"/)
    expect(panel).toMatch(/mode="new"/)
    expect(panel).toMatch(/@saved="onSaved"/)
    expect(panel).toMatch(/@cancel="closeEditor"/)
    // 打开即定位 + 手机关照
    expect(editor).toMatch(/scrollIntoView\(\{ block: 'center' \}\)/)
    expect(editor).toMatch(/@media \(max-width: 720px\)/)
    expect(editor).toMatch(/\.editor-canvas \{ max-height: 45vh; overflow: auto; \}/)
    expect(editor).toMatch(/position: sticky; bottom: 0;/)
  })

  it('新页：按 ?zone 选中、渲染项数、页头有返回工作区', () => {
    expect(itemsPage).toMatch(/现场 · 工作区/)
    expect(itemsPage).toMatch(/api\.get\('\/api\/hygiene\/admin\/zones'\)/)
    expect(itemsPage).toMatch(/route\.query\.zone/)
    expect(itemsPage).toMatch(/:zone="zone" @changed="load"/)
    expect(itemsPage).toMatch(/to="\/workbench\/floor\/zones"/)
    expect(itemsPage).toMatch(/没有找到这个工作区/)
  })
})

const ZONES = {
  zones: [
    { id: 1, name: '前厅', items: [{ id: 11, name: '台面' }] },
    {
      id: 2,
      name: '后厨',
      items: [
        { id: 21, name: '灶台' },
        { id: 22, name: '地面' },
      ],
    },
  ],
}

function jsonResponse(data) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workbench/floor/zones', component: { template: '<div />' } },
      { path: '/workbench/floor/zone-items', component: { template: '<div />' } },
    ],
  })
  return router
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    if (path === '/api/hygiene/admin/zones') return jsonResponse(ZONES)
    return jsonResponse({})
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function mountItemsPage(query = '?zone=2') {
  const [{ default: HygieneZoneItemsView }] = await Promise.all([
    import('../HygieneZoneItemsView.vue'),
  ])
  const router = makeRouter()
  await router.push(`/workbench/floor/zone-items${query}`)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(HygieneZoneItemsView, {
    global: {
      plugins: [router, pinia],
      // 标准图缩略图走真实图片接口，这里换成桩（本用例只看选中与项数）。
      stubs: { HygieneStandardOverlay: true },
    },
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, router }
}

describe('工作区 · 日常清单独立页', () => {
  it('按 ?zone=2 选中后厨：标题、读数、面板标题与列表项都跟着换', async () => {
    const { wrapper } = await mountItemsPage('?zone=2')

    expect(wrapper.get('h1').text()).toBe('后厨')
    expect(wrapper.text()).toContain('2 项')
    expect(wrapper.text()).toContain('后厨 日常清单')
    const rows = wrapper.findAll('.item-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('灶台')
    expect(wrapper.get('a.btn').attributes('href')).toBe('/workbench/floor/zones')
  })

  it('缺省 ?zone 时退回第一个工作区', async () => {
    const { wrapper } = await mountItemsPage('')

    expect(wrapper.get('h1').text()).toBe('前厅')
    expect(wrapper.findAll('.item-row')).toHaveLength(1)
  })

  it('query 变了重新选中（设备返回/换链接都走同一条）', async () => {
    const { wrapper, router } = await mountItemsPage('?zone=1')
    expect(wrapper.get('h1').text()).toBe('前厅')

    await router.push('/workbench/floor/zone-items?zone=2')
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('后厨')
  })

  it('区不存在时给空态与返回入口', async () => {
    const { wrapper } = await mountItemsPage('?zone=999')
    // 999 找不到 → 退回第一个区（缺省口径），不会白屏
    expect(wrapper.get('h1').text()).toBe('前厅')

    // 真的一个区都没有时才是空态
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ zones: [] })))
    const { wrapper: empty } = await mountItemsPage('')
    expect(empty.text()).toContain('还没有卫生工作区。')
    expect(empty.get('a.btn').attributes('href')).toBe('/workbench/floor/zones')
  })
})

const PANEL_ZONE = {
  id: 2,
  name: '后厨',
  items: [
    { id: 21, name: '灶台', current_standard_id: 5, markup: [] },
    { id: 22, name: '地面', current_standard_id: 6, markup: [] },
    { id: 23, name: '冰箱内部', current_standard_id: 7, markup: [] },
  ],
}

async function mountPanel(zone = PANEL_ZONE) {
  const [{ default: HygieneZoneItemsPanel }] = await Promise.all([
    import('../../../components/hygiene/HygieneZoneItemsPanel.vue'),
  ])
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(HygieneZoneItemsPanel, {
    props: { zone },
    global: {
      plugins: [pinia],
      // 缩略图/画布走真实图片接口，换成桩（本组只看编辑器的位置）。
      stubs: { HygieneStandardOverlay: true },
    },
  })
  await flushPromises()
  return wrapper
}

describe('工作区 · 日常清单：编辑器跟随被编辑的那一项', () => {
  it('点第 1 项的「编辑标注」，编辑器紧邻第 1 项（不再跑到列表末尾）', async () => {
    const wrapper = await mountPanel()
    const before = wrapper.findAll('.item-list > .item-row')
    expect(before).toHaveLength(3)

    const editBtn = before[0].findAll('button').find((node) => node.text() === '编辑标注')
    expect(editBtn).toBeTruthy()
    await editBtn.trigger('click')
    await flushPromises()

    const after = wrapper.findAll('.item-list > .item-row')
    expect(after).toHaveLength(4)
    // 第 1 项仍在原位，编辑器就是紧随其后的那一个 li
    expect(after[0].text()).toContain('灶台')
    expect(after[1].classes()).toContain('is-editor')
    expect(after[1].text()).toContain('编辑标注 · 灶台')
    // 最后一项（冰箱内部）后面没有编辑器 —— 原先把编辑器挂在列表末尾的 bug
    expect(after[3].text()).toContain('冰箱内部')
    expect(after[3].classes()).not.toContain('is-editor')
  })

  it('「换标准图」用同一个编辑器、标题带项名；「＋新增检查项」在列表底部用 mode=new', async () => {
    const wrapper = await mountPanel()
    const rows = wrapper.findAll('.item-list > .item-row')
    const replaceBtn = rows[1].findAll('button').find((node) => node.text() === '换标准图')
    await replaceBtn.trigger('click')
    await flushPromises()

    const after = wrapper.findAll('.item-list > .item-row')
    expect(after).toHaveLength(4)
    // 第 2 项（地面）后面紧跟编辑器：0=灶台 1=地面 2=编辑器 3=冰箱内部
    expect(after[1].text()).toContain('地面')
    expect(after[2].classes()).toContain('is-editor')
    expect(after[2].text()).toContain('换标准图 · 地面')

    // 收起后点底部「＋ 新增检查项」：编辑器是 mode=new（没有项名后缀）
    await after[2].findAll('button').find((node) => node.text() === '取消').trigger('click')
    await flushPromises()
    const addBtn = wrapper.findAll('button').find((node) => node.text() === '＋ 新增检查项')
    expect(addBtn).toBeTruthy()
    await addBtn.trigger('click')
    await flushPromises()
    const form = wrapper.get('form.item-editor')
    expect(form.text()).toContain('新增检查项')
    expect(form.text()).not.toContain(' · ')
  })
})
