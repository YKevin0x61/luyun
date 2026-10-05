import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  RECIPE_DETAIL_PATH,
  RECIPE_HOME_PATH,
  RECIPE_MANAGE_PATH,
  RECIPE_PRINT_PATH,
  RECIPE_QR_PATH,
  RECIPE_READER_PATHS,
  isRecipeReaderPath,
} from '../recipePaths.js'

// 票 07：配方阅读面搬进工作台的「后勤」组，地址从 `/recipe*` 换成
// `/workbench/kitchen/recipe*`。五条地址**只有一份**（`utils/recipePaths.js`）：
// 页面里的 router-link、印码页生成的岗位码、登录回跳白名单、PWA 归属判据都引它。
//
// 这一份压两件事：
// 1. 那份常量本身（五条路径、四条阅读面）；
// 2. 四个阅读页 + 印码页**真的**从它取地址、且顶栏那两颗管理端入口按身份出现 ——
//    用读源码的方式（同 `views/hygiene/__tests__/adminSection.test.js` 的路子）：
//    页面组件挂载代价大（要 router / fetch / canvas），而这里要钉的是「不许再抄一份
//    字面量」与「那两颗入口不许无条件渲染」，源码层面正好看得清。
const here = dirname(fileURLToPath(import.meta.url))
const REPO_SRC = join(here, '..', '..')

function read(rel) {
  return readFileSync(join(REPO_SRC, rel), 'utf8')
}

describe('配方五条地址的唯一一份常量', () => {
  it('五条路径都在工作台的「后勤」组下', () => {
    expect(RECIPE_HOME_PATH).toBe('/workbench/kitchen/recipe')
    expect(RECIPE_DETAIL_PATH).toBe('/workbench/kitchen/recipe/detail')
    expect(RECIPE_PRINT_PATH).toBe('/workbench/kitchen/recipe/print')
    expect(RECIPE_QR_PATH).toBe('/workbench/kitchen/recipe/qr')
    expect(RECIPE_MANAGE_PATH).toBe('/workbench/kitchen/recipe/manage')
  })

  it('阅读面三条、印码与管理页不在其中', () => {
    // 印码页是**店长的活**（生成岗位码、印出来贴到岗位上），员工是扫码的那一方 ——
    // 扫码扫到的是 detail 页。它原来被并称「阅读面」，于是未登录访问它会默认开员工栏。
    expect(RECIPE_READER_PATHS).toEqual([
      RECIPE_HOME_PATH, RECIPE_DETAIL_PATH, RECIPE_PRINT_PATH,
    ])
    expect(isRecipeReaderPath(RECIPE_QR_PATH)).toBe(false)
    expect(isRecipeReaderPath(RECIPE_MANAGE_PATH)).toBe(false)
  })

  it('印码页的路径常量照旧在（页内那颗「岗位二维码」入口靠它）', () => {
    expect(RECIPE_QR_PATH).toBe('/workbench/kitchen/recipe/qr')
  })

  it('旧的 /recipe* 一条都不是阅读面（搬走之后不留别名）', () => {
    for (const stale of ['/recipe', '/recipe/detail', '/recipe/print', '/recipe/qr']) {
      expect(isRecipeReaderPath(stale), stale).toBe(false)
    }
  })
})

describe('四个阅读页与印码页：地址来自那一份常量，入口按身份出现', () => {
  const VIEWS = [
    'views/recipe/RecipeStationsView.vue',
    'views/recipe/RecipeDetailView.vue',
    'views/recipe/RecipeQrView.vue',
    'views/recipe/RecipeManageView.vue',
  ]

  it('页面里不再有 /recipe* 的地址字面量', () => {
    for (const view of VIEWS) {
      const src = read(view)
      // `useScopedStylesheet('/recipe.css')` 与组件目录名不算地址。
      const hits = src.match(/['"`]\/recipe(\/|\?|['"`])/g) || []
      expect(hits, `${view} 里还抄着地址字面量：${hits}`).toEqual([])
    }
    expect(read('views/recipe/RecipePrintView.vue')).not.toMatch(/['"`]\/recipe\//)
  })

  it('顶栏那两颗管理端入口按身份出现（未登录的扫码用户点不到）', () => {
    for (const view of VIEWS) {
      const src = read(view)
      // `/`（管理后台）与配方管理两颗都是管理端那一档的页：`v-if="isAdmin"`。
      expect(src, `${view} 的「管理后台」入口`).toMatch(
        /<router-link v-if="isAdmin" class="site-nav-link" to="\/">/,
      )
      expect(src, `${view} 的配方管理入口`).toMatch(
        /<router-link v-if="isAdmin" class="site-nav-link" :to="RECIPE_MANAGE_PATH">/,
      )
      // 判据来自工作台身份那一个组合式函数，不是各自写一份。
      // （详情页把它绑到 `canEdit` 那个老名字上，拖拽排序那几个入口照旧读它。）
      expect(src).toMatch(/useRecipeAdmin\(\)/)
      expect(src).toMatch(/const \{ isAdmin(: canEdit)? \} = useRecipeAdmin\(\)/)
    }
  })

  it('「谁能编辑配方」不再问 /api/auth/status，改由身份模型决定', () => {
    const detail = read('views/recipe/RecipeDetailView.vue')
    expect(detail).not.toMatch(/\/api\/auth\/status/)
    expect(detail).not.toMatch(/canEdit\.value = /)
    expect(detail).toMatch(/const \{ isAdmin: canEdit \} = useRecipeAdmin\(\)/)
    // 拖拽排序那几个入口照旧跟着 canEdit 收（判据换了，行为不变）。
    expect(detail).toMatch(/readerDragAllowed\(\{ canEdit: canEdit\.value/)
  })

  it('印码页生成的是新阅读地址（岗位码印出去就是它）', () => {
    const qr = read('views/recipe/RecipeQrView.vue')
    expect(qr).toMatch(/window\.location\.origin\}\$\{RECIPE_DETAIL_PATH\}\?slug=/)
    expect(qr).not.toMatch(/origin\}\/recipe\//)
  })
})
