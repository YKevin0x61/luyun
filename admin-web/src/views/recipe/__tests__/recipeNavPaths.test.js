// @vitest-environment jsdom
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 配方页那几颗入口地址的**来源**（票 12 收的 O4）：地址常量在 `utils/recipePaths.js`
// 一份，页面里用哪个就 import 哪个。`RecipeStationsView` 与 `RecipeManageView` 都用了
// `RECIPE_MANAGE_PATH` 却没 import（票 07 把它们改成 `:to="RECIPE_MANAGE_PATH"` 时漏的
// 一行）—— 模板里它是 `undefined`：
//  - 真机（生产构建）：`<router-link :to="undefined">` 的 `router.resolve()` 抛
//    `TypeError: Cannot read properties of undefined (reading 'path')`，那颗「配方管理」
//    链接整颗不渲染（管理端身份下 DOM 里只剩「管理后台、岗位列表」），控制台还多一条报错；
//  - 单测（dev 构建）：prop 校验只警告，链接落成「当前页」的 href —— 一样是错的。
// 两道网都钉住它：
// 1. 源码级：这一族页面里用到的每个 `RECIPE_*` 标识符都必须 import（漏一个就红）；
// 2. 真挂载：管理端身份下「配方管理」那颗链接真的在 DOM 里，href 是配方管理那一页。

const here = dirname(fileURLToPath(import.meta.url))
const RECIPE_DIR = join(here, '..')
const RECIPE_COMPONENT_DIR = join(here, '../../../components/recipe')

/** 取 `<script setup>` 块里 import 进来的名字（`import { A, B as C } from …`）。 */
function importedNames(source) {
  const script = source.split('</script>')[0]
  const names = new Set()
  for (const match of script.matchAll(/import\s*\{([^}]*)\}\s*from/g)) {
    for (const raw of match[1].split(',')) {
      const name = raw.trim().split(/\s+as\s+/).pop().trim()
      if (name) names.add(name)
    }
  }
  return names
}

describe('配方页用到的 RECIPE_* 常量都得 import', () => {
  const files = [
    ...readdirSync(RECIPE_DIR).filter((f) => f.endsWith('.vue')).map((f) => join(RECIPE_DIR, f)),
    ...readdirSync(RECIPE_COMPONENT_DIR).filter((f) => f.endsWith('.vue'))
      .map((f) => join(RECIPE_COMPONENT_DIR, f)),
  ]

  it.each(files.map((path) => [path.slice(path.indexOf('src/')), path]))(
    '%s 没有「用了但没 import」的 RECIPE_* 标识符',
    (_label, path) => {
      const source = readFileSync(path, 'utf8')
      const declared = importedNames(source)
      const used = new Set(source.match(/\bRECIPE_[A-Z0-9_]+\b/g) || [])
      const missing = [...used].filter((name) => !declared.has(name)).sort()
      expect(missing).toEqual([])
    },
  )
})

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 管理端会话在、员工会话不在：身份那一档是「超级管理员」，配方管理那颗链接要出来。 */
function adminOnlyFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: true, initialized: true })
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({ detail: '需要员工登录' }, 401)
    if (path.includes('/api/recipes/stations')) return jsonResponse({ stations: [] })
    return jsonResponse({}, 404)
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('配方列表页顶栏（管理端身份）', () => {
  it('「配方管理」那颗链接在，href 指向配方管理那一页', async () => {
    vi.resetModules()
    localStorage.clear()
    vi.stubGlobal('fetch', adminOnlyFetch())
    const { RECIPE_MANAGE_PATH } = await import('../../../utils/recipePaths.js')
    const RecipeStationsView = (await import('../RecipeStationsView.vue')).default

    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/workbench/kitchen/recipe', component: RecipeStationsView },
        { path: RECIPE_MANAGE_PATH, component: { template: '<div />' } },
        { path: '/', component: { template: '<div />' } },
      ],
    })
    await router.push('/workbench/kitchen/recipe')
    await router.isReady()

    const wrapper = mount(RecipeStationsView, { global: { plugins: [router, pinia] } })
    await flushPromises()
    await flushPromises()

    const links = wrapper.findAll('.site-nav-link').map((a) => a.text())
    expect(links).toEqual(['管理后台', '岗位列表', '配方管理'])
    const manage = wrapper.find(`a[href="${RECIPE_MANAGE_PATH}"]`)
    expect(manage.exists()).toBe(true)
    expect(manage.text()).toBe('配方管理')
    wrapper.unmount()
  })
})
