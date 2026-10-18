<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api/client'
import { RECIPE_DETAIL_PATH, RECIPE_HOME_PATH } from '../../utils/recipePaths'
import { useRecipeAdmin } from '../../composables/useRecipeAdmin'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { recipeDocumentTitle } from '../../utils/recipeCopy'
import {
  A4_CONTENT_HEIGHT_MM,
  PRINT_CARD_GAP_MM,
  lastSelectedPageIndex,
  mmToPx,
  normalizeSelectedPages,
  paginateStationCards,
  shouldRepackPrintPreview,
  stationPageHtml,
  syncSelectedPages,
} from '../../utils/recipePrintPagination'

useScopedStylesheet('/recipe.css')

// 身份探针：阅读 / 打印 / 印码三页原本是沉浸页、没人替它们探会话，所以在页内开场
// （幂等 —— 正在探的时候直接返回）。2026-10-08 起它们也套了工作台外壳（壳里的身份切换器
// 会探），这一句留着当兜底。工具栏上那两颗入口撤掉之后，`isAdmin` 不再需要。
useRecipeAdmin()

const route = useRoute()
const slugs = computed(() =>
  String(route.query.slugs || route.query.slug || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
)

const bodyHtml = ref('加载中…')
const pageHtmls = ref(['加载中…'])
const pageTitle = ref('打印预览')
const pageCount = computed(() => Math.max(1, pageHtmls.value.length))
const selectedPages = ref([])
const selectedCount = computed(() => selectedPages.value.length)
const printTailIndex = computed(() => lastSelectedPageIndex(selectedPages.value))
const canPrint = computed(() => selectedCount.value > 0)
const measureRef = ref(null)
const probeRef = ref(null)
const sheetWrapRef = ref(null)
// v3：屏幕预览是全屏 overlay，纸张按可用宽度 fit-width 缩放（1280 以下不横向滚）。
const zoomMode = ref('fit')
const fitScale = ref(1)
const manualScale = ref(1)
const scalePercent = computed(() => Math.round(
  (zoomMode.value === 'fit' ? fitScale.value : manualScale.value) * 100,
))
// 纸面头：站点 / 日期（固定成本次预览的那一天）
const stationTitles = ref([])
const todayText = `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}-${String(new Date().getDate()).padStart(2, '0')}`
const stationLabel = computed(() => (
  stationTitles.value.length ? stationTitles.value.join('、') : slugs.value.join('、')
))
const backHref = computed(() => (slugs.value.length === 1
  ? `${RECIPE_DETAIL_PATH}?slug=${encodeURIComponent(slugs.value[0])}`
  : RECIPE_HOME_PATH))

let resizeObserver = null
let previousThemeAttr = null
let printMediaQuery = null
let isPrinting = false
let rememberedPrintPages = { key: '', pages: [] }

function selectionScope() {
  return slugs.value.join(',')
}

function rememberPages(pages) {
  rememberedPrintPages = { key: selectionScope(), pages: Array.isArray(pages) ? pages.slice() : [] }
}

async function waitForRecipeCss() {
  const deadline = Date.now() + 2000
  while (Date.now() < deadline) {
    const link = document.querySelector('link[data-scoped="true"][href*="recipe.css"]')
    if (link?.sheet) return
    await new Promise((r) => setTimeout(r, 20))
  }
}

function measureTitleReserve(probe, titleHtml) {
  if (!titleHtml) return 0
  probe.innerHTML = stationPageHtml(titleHtml, [])
  const title = probe.querySelector('.sop-doc-title')
  const grid = probe.querySelector('.sop-section-grid')
  if (!title || !grid) return title?.getBoundingClientRect().height || 0
  return Math.max(0, grid.getBoundingClientRect().top - title.getBoundingClientRect().top)
}

function measureCardHeights(probe, cardHtmls) {
  probe.innerHTML = `<div class="sop-print-measure-col">${cardHtmls.join('')}</div>`
  return [...probe.querySelectorAll('article.recipe-card')].map((card) => card.getBoundingClientRect().height)
}

function packStationToHtmls(station, probe, pagePx) {
  const titleHtml = station.querySelector('.sop-doc-title')?.outerHTML || ''
  const cardHtmls = [...station.querySelectorAll('article.recipe-card')].map((card) => card.outerHTML)
  if (!cardHtmls.length) return [station.innerHTML]
  const heights = measureCardHeights(probe, cardHtmls)
  const cards = cardHtmls.map((html, index) => ({ html, height: heights[index] || 0 }))
  const titleReserve = measureTitleReserve(probe, titleHtml)
  const gapPx = mmToPx(PRINT_CARD_GAP_MM)
  return paginateStationCards({ titleHtml, cards, pagePx, gapPx, titleReserve })
}

function applyPageHtmls(htmls) {
  const next = htmls.length ? htmls : ['']
  pageHtmls.value = next
  const fromState = normalizeSelectedPages(selectedPages.value, next.length)
  const fromMemory = rememberedPrintPages.key === selectionScope()
    ? normalizeSelectedPages(rememberedPrintPages.pages, next.length)
    : []
  const picked = fromState.length ? fromState : fromMemory
  selectedPages.value = picked.length ? picked : syncSelectedPages([], next.length)
  rememberPages(selectedPages.value)
}

function remesurePages() {
  if (isPrinting || printMediaQuery?.matches || !shouldRepackPrintPreview()) return
  const measure = measureRef.value
  const probe = probeRef.value
  if (!measure || !probe) return
  const host = measure.parentElement
  if (host && getComputedStyle(host).display === 'none') return
  const pagePx = mmToPx(A4_CONTENT_HEIGHT_MM)
  const stations = [...measure.querySelectorAll('.sop-print-station')]
  if (!stations.length) {
    applyPageHtmls([measure.innerHTML])
    probe.innerHTML = ''
    return
  }
  const htmls = []
  for (const station of stations) {
    htmls.push(...packStationToHtmls(station, probe, pagePx))
  }
  applyPageHtmls(htmls.length ? htmls : [measure.innerHTML])
  probe.innerHTML = ''
}

async function layoutPages() {
  await waitForRecipeCss()
  if (document.fonts?.ready) await document.fonts.ready
  await nextTick()
  remesurePages()
  measureFit()
}

onMounted(async () => {
  // 标题要在**任何分支之前**设：下面「没给 slug」「加载失败」都会早早走掉，而原来那行
  // `document.title = pageTitle.value` 在成功分支里 —— 于是失败时浏览器标签停在 index.html
  // 的默认值「厨务管家 · 管理后台」（真机走查 N1）。加载成功后再换成更具体的标题。
  document.title = recipeDocumentTitle('配方打印')
  previousThemeAttr = document.documentElement.getAttribute('data-theme')
  document.documentElement.setAttribute('data-theme', 'light')
  document.body.classList.add('sop-print-preview-page')
  if (!slugs.value.length) {
    bodyHtml.value = '未指定岗位'
    await layoutPages()
    return
  }
  try {
    const parts = []
    for (const slug of slugs.value) {
      try {
        const data = await api.get(`/api/recipes/stations/${encodeURIComponent(slug)}`)
        parts.push(`<section class="sop-print-station">${data.content_html}</section>`)
        stationTitles.value.push(String(data.title || slug))
      } catch (e) { /* 单个岗位加载失败时跳过，不阻塞其余岗位 */ }
    }
    if (!parts.length) {
      bodyHtml.value = '未找到岗位'
    } else {
      bodyHtml.value = parts.join('<div class="sop-print-page-break"></div>')
      pageTitle.value = slugs.value.length === 1 ? '打印预览' : `批量打印 · ${slugs.value.length} 个岗位`
      document.title = recipeDocumentTitle(pageTitle.value)
    }
  } catch (e) {
    bodyHtml.value = '加载失败'
  }
  await layoutPages()
  bindMeasureResize()
  bindPrintMedia()
  measureFit()
  window.addEventListener('resize', onWindowResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onWindowResize)
  printMediaQuery?.removeEventListener('change', onPrintMediaChange)
  resizeObserver?.disconnect()
  document.body.classList.remove('sop-print-preview-page')
  if (previousThemeAttr == null) document.documentElement.removeAttribute('data-theme')
  else document.documentElement.setAttribute('data-theme', previousThemeAttr)
})

watch(bodyHtml, () => {
  layoutPages()
})

function zoomValue() {
  return zoomMode.value === 'fit' ? fitScale.value : manualScale.value
}
// 直接给每张纸设 zoom：不新增 CSS 变量（它不在令牌层里，设计护栏会当成未定义令牌）。
function syncZoom() {
  const wrap = sheetWrapRef.value
  if (!wrap) return
  const value = String(zoomValue())
  wrap.querySelectorAll('.sop-print-preview-sheet').forEach((el) => {
    el.style.zoom = value
  })
}
function measureFit() {
  const el = sheetWrapRef.value
  if (!el) return
  const available = (el.clientWidth || window.innerWidth) - 32
  fitScale.value = Math.max(0.3, Math.min(1, available / mmToPx(210)))
  syncZoom()
}
function zoomBy(delta) {
  if (zoomMode.value === 'fit') {
    manualScale.value = fitScale.value
    zoomMode.value = 'manual'
  }
  manualScale.value = Math.max(0.3, Math.min(2, Math.round((manualScale.value + delta) * 100) / 100))
  syncZoom()
}
function zoomFit() {
  zoomMode.value = 'fit'
  measureFit()
}
function onWindowResize() {
  if (zoomMode.value === 'fit') measureFit()
}

function bindMeasureResize() {
  if (!measureRef.value || typeof ResizeObserver === 'undefined') return
  resizeObserver?.disconnect()
  resizeObserver = new ResizeObserver(() => remesurePages())
  resizeObserver.observe(measureRef.value)
}

function isPageSelected(index) {
  return selectedPages.value.includes(index)
}

function bindPrintMedia() {
  if (typeof matchMedia !== 'function') return
  printMediaQuery?.removeEventListener('change', onPrintMediaChange)
  printMediaQuery = matchMedia('print')
  printMediaQuery.addEventListener('change', onPrintMediaChange)
}

function onPrintMediaChange() {
  isPrinting = !!printMediaQuery?.matches
  if (isPrinting) {
    resizeObserver?.disconnect()
    return
  }
  bindMeasureResize()
}

function togglePage(index) {
  const next = selectedPages.value.includes(index)
    ? selectedPages.value.filter((item) => item !== index)
    : selectedPages.value.concat(index)
  selectedPages.value = normalizeSelectedPages(next, pageCount.value)
  rememberPages(selectedPages.value)
}

function selectAllPages() {
  selectedPages.value = syncSelectedPages([], pageCount.value)
  rememberPages(selectedPages.value)
}

function doPrint() {
  if (!canPrint.value) return
  isPrinting = true
  rememberPages(selectedPages.value)
  resizeObserver?.disconnect()
  const restore = () => {
    window.removeEventListener('afterprint', restore)
    isPrinting = false
    bindMeasureResize()
  }
  window.addEventListener('afterprint', restore)
  window.focus()
  window.print()
}
</script>

<template>
  <div class="print-page">
    <!-- 页头：kicker / H1 / 右侧读数 + 动作（B 案 v2 页面骨架）。
         打印时整条由壳的 `.no-print` 与 recipe.css 的 @media print 隐藏。 -->
    <header class="sop-print-preview-toolbar no-print print-head">
      <div class="print-head-copy">
        <p class="print-kicker">配方 · 打印预览</p>
        <h1 class="print-title">{{ pageTitle }}</h1>
      </div>
      <div class="print-head-pills">
        <span class="print-pill"><i class="print-led sig" aria-hidden="true"></i>A4 · {{ pageCount }} 页</span>
        <span v-if="pageCount > 1" class="print-pill">
          <i class="print-led warn" aria-hidden="true"></i>已选 {{ selectedCount }} 页
        </span>
      </div>
      <div class="print-head-actions">
        <!-- v3：屏幕预览是全屏 overlay，页头只留 返回 / 缩放 / 打印 / 关闭 -->
        <router-link class="btn btn-ghost" :to="backHref">返回</router-link>
        <div class="print-zoom" role="group" aria-label="缩放">
          <button type="button" class="btn btn-ghost print-zoom-btn" aria-label="缩小" @click="zoomBy(-0.1)">−</button>
          <button type="button" class="btn btn-ghost print-zoom-value" @click="zoomFit">{{ scalePercent }}%</button>
          <button type="button" class="btn btn-ghost print-zoom-btn" aria-label="放大" @click="zoomBy(0.1)">+</button>
        </div>
        <button type="button" class="btn btn-primary" :disabled="!canPrint" @click="doPrint">
          {{ pageCount > 1 ? `打印已选页` : '打印' }}
        </button>
        <router-link class="btn btn-ghost" :to="RECIPE_HOME_PATH">关闭</router-link>
      </div>
    </header>
    <div class="sop-print-preview-measure-host no-print" aria-hidden="true">
      <div ref="measureRef" class="sop-print-preview-measure sop-body markdown-body" v-html="bodyHtml"></div>
      <div ref="probeRef" class="sop-print-preview-measure sop-body markdown-body"></div>
    </div>
    <div ref="sheetWrapRef" class="sop-print-preview-sheet-wrap print-stage">
      <div
        v-for="(html, index) in pageHtmls"
        :key="index"
        class="sop-print-preview-sheet sop-panel"
        :class="{
          'is-print-skipped': pageCount > 1 && !isPageSelected(index),
          'is-print-tail': index === printTailIndex,
        }"
      >
        <div class="print-sheet-head" aria-hidden="true">
          <span>{{ stationLabel }}</span>
          <span>{{ todayText }} · {{ index + 1 }} / {{ pageCount }}</span>
        </div>
        <label v-if="pageCount > 1" class="sop-print-preview-sheet-pick no-print" @click.prevent="togglePage(index)">
          <input type="checkbox" :checked="isPageSelected(index)" tabindex="-1">
          第 {{ index + 1 }} 页
        </label>
        <div class="sop-body markdown-body sop-print-preview-sheet-content" v-html="html"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* B 案 v2——**只在屏幕上**是「预览台」，纸面必须白底黑字。
   纸张与打印的那套规则住在 `recipe.css` 末尾的 `@media print` 与壳的打印复位里，
   这里所有覆盖都圈在 `@media screen`，打印时一条都不生效，别把白纸弄脏。 */
/* v3：屏幕预览是全屏 overlay（只留 返回 / 缩放 / 打印 / 关闭 四个动作）。
   打印时这条 overlay 必须解除，否则 fixed 层会被印成一张空白页。 */
.print-page {
  position: fixed;
  inset: 0;
  z-index: var(--z-queue);
  overflow: auto;
  background: var(--surface-0);
  padding-bottom: calc(24px + var(--safe-b));
}
.print-kicker {
  margin: 0 0 7px;
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .26em;
  color: var(--ink-faint);
}
.print-title {
  margin: 0;
  font: 600 26px/1.05 var(--font-sans);
  letter-spacing: .01em;
  color: var(--ink);
}
.print-head-copy {
  min-width: 0;
}
.print-head-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.print-head-actions {
  margin-left: auto;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.print-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink-dim);
  font: 500 10px/1 var(--font-mono);
}
.print-led {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
  flex: 0 0 6px;
}
.print-led.ok { background: var(--ok); box-shadow: 0 0 0 3px var(--wb-ok-a16); }
.print-led.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.print-led.bad { background: var(--danger); box-shadow: 0 0 0 3px var(--wb-danger-a16); }
.print-led.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }

.print-zoom {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.print-zoom-btn {
  min-width: 44px;
  padding: 0 10px;
}
.print-zoom-value {
  min-width: 64px;
  padding: 0 10px;
  font: 500 11px/1 var(--font-mono);
}

/* 纸面头：站点 · 日期 · 页码 —— 绝对定位在纸张上内边距里，
   不占正文内容流，所以分页测量（A4 内容高度）一个字都不用改。 */
.print-sheet-head {
  position: absolute;
  top: 3.5mm;
  left: 10mm;
  right: 10mm;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8mm;
  font: 500 7.5pt/1.2 var(--font-mono);
  letter-spacing: .04em;
  color: var(--ink-dim);
  pointer-events: none;
}

@media screen {
  /* 屏上的页头：替代 recipe.css 那条写死的深色渐变工具条。 */
  .sop-print-preview-toolbar {
    position: sticky;
    top: 0;
    z-index: var(--z-shell);
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: 12px 16px;
    padding: 14px 18px;
    background: var(--surface-1);
    border-bottom: 1px solid var(--line);
    color: var(--ink);
    box-shadow: var(--hy-shadow-sm);
  }
  .sop-print-preview-sheet-pick {
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-radius: var(--r-1);
    color: var(--ink);
  }
  /* 预览台：纸张浮在钢面底上（纸张本身的白色由 recipe.css 的预览令牌定）。 */
  .sop-print-preview-sheet-wrap {
    padding: 20px 16px 32px;
  }
  /* 比 recipe.css 那条同特异性规则更具体一层：动态注入的 recipe.css 在
     组件样式之后加载，同特异性会反压回来。 */
  .sop-print-preview-sheet-wrap .sop-print-preview-sheet {
    border-radius: var(--r-1);
    box-shadow: var(--hy-shadow-md);
  }
  .sop-print-preview-page-index {
    color: var(--ink-faint);
    font-family: var(--font-mono);
  }
}


@media print {
  /* 只解除本页新增的内容（overlay / 缩放），recipe.css 的 A4 规则照旧。 */
  .print-page {
    position: static;
    inset: auto;
    overflow: visible;
    background: transparent;
    padding-bottom: 0;
  }
  .sop-print-preview-sheet {
    zoom: 1 !important;
  }
}

@media (max-width: 720px) {
  .print-page {
    padding-bottom: calc(12px + var(--safe-b));
  }
  .print-head-pills {
    display: none;
  }
  .print-head-actions {
    margin-left: 0;
    flex-wrap: wrap;
  }
  .print-title {
    font-size: 18px;
  }
  .print-zoom-value {
    min-width: 52px;
  }
}
</style>
