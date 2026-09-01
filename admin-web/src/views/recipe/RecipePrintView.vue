<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { RECIPE_NAV_HOME_LABEL } from '../../utils/recipeCopy'
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
const backHref = computed(() => (slugs.value.length === 1 ? `/recipe/detail?slug=${encodeURIComponent(slugs.value[0])}` : '/recipe'))

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
}

onMounted(async () => {
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
      } catch (e) { /* 单个岗位加载失败时跳过，不阻塞其余岗位 */ }
    }
    if (!parts.length) {
      bodyHtml.value = '未找到岗位'
    } else {
      bodyHtml.value = parts.join('<div class="sop-print-page-break"></div>')
      pageTitle.value = slugs.value.length === 1 ? '打印预览' : `批量打印 · ${slugs.value.length} 个岗位`
      document.title = pageTitle.value
    }
  } catch (e) {
    bodyHtml.value = '加载失败'
  }
  await layoutPages()
  bindMeasureResize()
  bindPrintMedia()
})
onBeforeUnmount(() => {
  printMediaQuery?.removeEventListener('change', onPrintMediaChange)
  resizeObserver?.disconnect()
  document.body.classList.remove('sop-print-preview-page')
  if (previousThemeAttr == null) document.documentElement.removeAttribute('data-theme')
  else document.documentElement.setAttribute('data-theme', previousThemeAttr)
})

watch(bodyHtml, () => {
  layoutPages()
})

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
  <div>
    <header class="sop-print-preview-toolbar no-print">
      <span class="sop-print-preview-title">{{ pageTitle }} · {{ pageCount }} 页 A4</span>
      <span v-if="pageCount > 1" class="sop-print-preview-pick-hint">已选 {{ selectedCount }} 页</span>
      <button v-if="pageCount > 1" type="button" class="btn btn-ghost" @click="selectAllPages">全选</button>
      <span class="sop-print-preview-spacer"></span>
      <button type="button" class="btn btn-primary" :disabled="!canPrint" @click="doPrint">
        {{ pageCount > 1 ? `打印已选页` : '打印' }}
      </button>
      <router-link class="btn btn-ghost" to="/">{{ RECIPE_NAV_HOME_LABEL }}</router-link>
      <router-link class="btn btn-ghost" :to="backHref">关闭</router-link>
    </header>
    <div class="sop-print-preview-measure-host no-print" aria-hidden="true">
      <div ref="measureRef" class="sop-print-preview-measure sop-body markdown-body" v-html="bodyHtml"></div>
      <div ref="probeRef" class="sop-print-preview-measure sop-body markdown-body"></div>
    </div>
    <div class="sop-print-preview-sheet-wrap">
      <div
        v-for="(html, index) in pageHtmls"
        :key="index"
        class="sop-print-preview-sheet sop-panel"
        :class="{
          'is-print-skipped': pageCount > 1 && !isPageSelected(index),
          'is-print-tail': index === printTailIndex,
        }"
      >
        <label v-if="pageCount > 1" class="sop-print-preview-sheet-pick no-print" @click.prevent="togglePage(index)">
          <input type="checkbox" :checked="isPageSelected(index)" tabindex="-1">
          第 {{ index + 1 }} 页
        </label>
        <div class="sop-body markdown-body sop-print-preview-sheet-content" v-html="html"></div>
        <div class="sop-print-preview-page-index">{{ index + 1 }} / {{ pageCount }}</div>
      </div>
    </div>
  </div>
</template>
