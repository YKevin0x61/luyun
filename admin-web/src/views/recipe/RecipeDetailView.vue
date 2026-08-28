<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import QRCode from 'qrcode'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import * as RC from '../../utils/recipeCore'
import { RECIPE_INGREDIENTS_AMOUNT_CLASS } from '../../utils/recipeIngredients'
import { parseBaseServingsQty, scaleAmount, servingsFactor } from '../../utils/recipeServings'
import SvgIcon from '../../components/SvgIcon.vue'
import RecipeNavIcon from './RecipeNavIcon.vue'

// 通过 innerHTML 注入的原生 DOM（复制按钮），无法命中 Vue 的 <style scoped>，故用行内 style 兜底对齐。
const CHECK_ICON_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="display:inline-block;vertical-align:-0.15em;flex-shrink:0"><path d="M20 6 9 17l-5-5"/></svg>'
const SCALE_TOGGLE_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="6" cy="12" r="3"/><circle cx="18" cy="12" r="3"/><path d="M9 12h6"/></svg>'

useScopedStylesheet('/recipe.css')

const route = useRoute()
const slug = computed(() => route.query.slug || '')

const title = ref('')
const contentHtml = ref('')
const loading = ref(true)
const errorMsg = ref('')
const bodyRef = ref(null)
const tocHtml = ref('')
const tocVisible = ref(false)

const theme = ref(RC.readPref(window.localStorage, 'sop.theme', 'auto'))
const density = ref(RC.readPref(window.localStorage, 'sop.density', 'compact') !== 'grid')
const fontPx = ref(RC.clampFontPx(RC.readPref(window.localStorage, 'sop.fontScale', '14')))
const searchTerm = ref('')
const searchCount = ref('')
const onlyNew = ref(false)
const showInactive = ref(false)
const qrModalUrl = ref('')
const qrCanvasRef = ref(null)
const targetServingsQty = ref('')
const servingsUnitLabel = ref('')
const servingsControlVisible = ref(false)

let searchDebounce = null
let intersectionObserver = null
let focusHighlightTimer = null
let scaleOutsideHandler = null
let scaleEscHandler = null
const FOCUS_HIGHLIGHT_MS = 1600

function applyTheme(t) {
  const norm = RC.normalizeTheme(t)
  if (norm === 'auto') document.documentElement.removeAttribute('data-theme')
  else document.documentElement.setAttribute('data-theme', norm)
}
function toggleTheme() {
  theme.value = RC.nextTheme(theme.value)
  applyTheme(theme.value)
  try { window.localStorage.setItem('sop.theme', theme.value) } catch (e) { /* noop */ }
}

function applyFont(px) {
  document.documentElement.style.setProperty('--reader-fs', `${px}px`)
}
function incFont(delta) {
  fontPx.value = RC.clampFontPx(fontPx.value + delta)
  applyFont(fontPx.value)
  try { window.localStorage.setItem('sop.fontScale', String(fontPx.value)) } catch (e) { /* noop */ }
}

function toggleDensity() {
  density.value = !density.value
  try { window.localStorage.setItem('sop.density', density.value ? 'compact' : 'grid') } catch (e) { /* noop */ }
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await api.get(`/api/recipes/stations/${encodeURIComponent(slug.value)}`, {
      include_inactive: showInactive.value ? 1 : undefined,
    })
    title.value = data.title
    contentHtml.value = data.content_html
    document.title = `${data.title} · 配方 SOP`
    await nextTick()
    afterContentRendered()
  } catch (e) {
    // 对齐老页 public/recipe-app.js:92,100 的固定中文文案：404 → 未找到该岗位，其它异常 → 加载失败。
    errorMsg.value = e.status === 404 ? '未找到该岗位' : '加载失败'
  } finally {
    loading.value = false
  }
}

function afterContentRendered() {
  buildToc()
  readPageServingsDefault()
  injectScaleControls()
  applyAllCardScales()
  bindScaleDismiss()
  injectCopyButtons()
  nextTick(() => applyFocusFromQuery())
}

function applyFocusFromQuery() {
  if (!bodyRef.value) return
  const raw = route.query.focus
  if (raw == null || raw === '') return
  const id = String(Array.isArray(raw) ? raw[0] : raw).trim()
  if (!/^\d+$/.test(id)) return
  const card = bodyRef.value.querySelector(`article.recipe-card[data-recipe-id="${id}"]`)
  if (!card) return
  card.scrollIntoView({ behavior: 'smooth', block: 'start' })
  bodyRef.value.querySelectorAll('article.recipe-card.recipe-card--focus').forEach((el) => {
    el.classList.remove('recipe-card--focus')
  })
  card.classList.add('recipe-card--focus')
  if (focusHighlightTimer != null) clearTimeout(focusHighlightTimer)
  focusHighlightTimer = setTimeout(() => {
    card.classList.remove('recipe-card--focus')
    focusHighlightTimer = null
  }, FOCUS_HIGHLIGHT_MS)
}

function buildToc() {
  if (!bodyRef.value) return
  const sections = Array.from(bodyRef.value.querySelectorAll('.sop-section'))
  if (!sections.length) {
    tocVisible.value = false
    return
  }
  const uniq = RC.makeUniqueSlugger()
  let html = '<h5>章节目录</h5>'
  intersectionObserver?.disconnect()
  const targets = []
  for (const sec of sections) {
    const h2 = sec.querySelector('.sop-section-head h2')
    if (!h2) continue
    const text = (h2.textContent || '').trim()
    const id = uniq(text)
    sec.id = id
    const count = sec.querySelectorAll('.recipe-card').length
    html += `<a href="#${id}" data-toc="${id}">${escapeHtml(text)} <span class="n">${count}</span></a>`
    targets.push(sec)
  }
  tocHtml.value = html
  tocVisible.value = true

  nextTick(() => {
    const tocEl = document.getElementById('sopToc')
    if (!tocEl) return
    const links = {}
    tocEl.querySelectorAll('a[data-toc]').forEach((a) => {
      links[a.getAttribute('data-toc')] = a
      a.addEventListener('click', (e) => {
        e.preventDefault()
        document.getElementById(a.getAttribute('data-toc'))?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    })
    if ('IntersectionObserver' in window) {
      intersectionObserver = new IntersectionObserver(
        (entries) => {
          for (const en of entries) {
            if (en.isIntersecting) {
              Object.keys(links).forEach((k) => links[k].classList.toggle('on', k === en.target.id))
            }
          }
        },
        { rootMargin: '-20% 0px -70% 0px' },
      )
      targets.forEach((s) => intersectionObserver.observe(s))
    }
  })
}

function escapeHtml(s) {
  const d = document.createElement('div')
  d.textContent = s == null ? '' : s
  return d.innerHTML
}

function injectCopyButtons() {
  if (!bodyRef.value) return
  bodyRef.value.querySelectorAll('article.recipe-card').forEach((card) => {
    if (card.querySelector('.recipe-copy')) return
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.className = 'recipe-copy no-print'
    btn.textContent = '复制'
    btn.addEventListener('click', (e) => {
      e.stopPropagation()
      const head = card.querySelector('.recipe-card-head')
      const bodyEl = card.querySelector('.recipe-card-body')
      const text = `${head ? head.textContent : ''}\n${bodyEl ? bodyEl.textContent : ''}`.trim()
      copyText(text).then(() => {
        const old = btn.innerHTML
        btn.innerHTML = `${CHECK_ICON_SVG} 已复制`
        setTimeout(() => { btn.innerHTML = old }, 900)
      })
    })
    card.appendChild(btn)
  })
}

function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text)
  return new Promise((resolve) => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy') } catch (e) { /* noop */ }
    document.body.removeChild(ta)
    resolve()
  })
}

const SCALE_PRESETS = [['×½', 0.5], ['×1', 1], ['×2', 2], ['×3', 3]]

function cardBaseQty(card) {
  return parseBaseServingsQty(card.getAttribute('data-base-servings-qty'))
}

function readPageServingsDefault() {
  if (!bodyRef.value) {
    servingsControlVisible.value = false
    targetServingsQty.value = ''
    servingsUnitLabel.value = ''
    return
  }
  const cards = bodyRef.value.querySelectorAll('article.recipe-card')
  let first = null
  for (const card of cards) {
    if (cardBaseQty(card) != null) {
      first = card
      break
    }
  }
  if (!first) {
    servingsControlVisible.value = false
    targetServingsQty.value = ''
    servingsUnitLabel.value = ''
    return
  }
  servingsControlVisible.value = true
  targetServingsQty.value = String(cardBaseQty(first))
  servingsUnitLabel.value = first.getAttribute('data-base-servings-unit') || ''
}

function effectiveFactor(card) {
  if (card.__sopOverrideFactor != null) return RC.clampFactor(card.__sopOverrideFactor)
  const base = cardBaseQty(card)
  if (base == null) return 1
  return servingsFactor(targetServingsQty.value, base)
}

function scaleCardAmounts(card, factor) {
  card.querySelectorAll(`.${RECIPE_INGREDIENTS_AMOUNT_CLASS}`).forEach((cell) => {
    if (cell.dataset.sopBaseAmount == null) {
      cell.dataset.sopBaseAmount = cell.textContent || ''
    }
    cell.textContent = scaleAmount(cell.dataset.sopBaseAmount, factor)
  })
  const f = RC.clampFactor(factor)
  card.setAttribute('data-scale', f === 1 ? '' : `×${RC.formatQty(f)}`)
  card.classList.toggle('recipe-card--scaled', f !== 1)
}

function applyAllCardScales() {
  if (!bodyRef.value) return
  bodyRef.value.querySelectorAll('article.recipe-card').forEach((card) => {
    if (cardBaseQty(card) == null) return
    scaleCardAmounts(card, effectiveFactor(card))
  })
}

function closeAllScalePopovers() {
  if (!bodyRef.value) return
  bodyRef.value.querySelectorAll('.sop-scale-wrap.is-open').forEach((wrap) => {
    wrap.classList.remove('is-open')
    wrap.querySelector('.sop-scale-toggle')?.setAttribute('aria-expanded', 'false')
  })
}

function bindScaleDismiss() {
  unbindScaleDismiss()
  scaleOutsideHandler = (e) => {
    if (e.target.closest && e.target.closest('.sop-scale-wrap')) return
    closeAllScalePopovers()
  }
  scaleEscHandler = (e) => {
    if (e.key === 'Escape') closeAllScalePopovers()
  }
  document.addEventListener('click', scaleOutsideHandler)
  document.addEventListener('keydown', scaleEscHandler)
}

function unbindScaleDismiss() {
  if (scaleOutsideHandler) document.removeEventListener('click', scaleOutsideHandler)
  if (scaleEscHandler) document.removeEventListener('keydown', scaleEscHandler)
  scaleOutsideHandler = null
  scaleEscHandler = null
}

function injectScaleControls() {
  if (!bodyRef.value) return
  bodyRef.value.querySelectorAll('article.recipe-card').forEach((card) => {
    if (cardBaseQty(card) == null) return
    if (card.querySelector('.sop-scale-wrap')) return
    const head = card.querySelector('.recipe-card-head')
    if (!head) return
    const wrap = document.createElement('div')
    wrap.className = 'sop-scale-wrap no-print'
    const toggle = document.createElement('button')
    toggle.type = 'button'
    toggle.className = 'sop-scale-toggle'
    toggle.setAttribute('aria-label', '临时覆盖用量倍率')
    toggle.setAttribute('aria-expanded', 'false')
    toggle.innerHTML = SCALE_TOGGLE_SVG
    const box = document.createElement('div')
    box.className = 'sop-scale sop-scale--popover'
    let html = ''
    for (const [label, f] of SCALE_PRESETS) {
      html += `<button type="button" class="sop-scale-btn" data-f="${f}">${label}</button>`
    }
    html += '<input type="text" inputmode="decimal" class="sop-scale-input" placeholder="倍" aria-label="自定义倍率">'
    box.innerHTML = html
    wrap.appendChild(toggle)
    wrap.appendChild(box)
    head.appendChild(wrap)

    toggle.addEventListener('click', (e) => {
      e.stopPropagation()
      const open = wrap.classList.toggle('is-open')
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false')
      if (open) {
        bodyRef.value.querySelectorAll('.sop-scale-wrap.is-open').forEach((other) => {
          if (other === wrap) return
          other.classList.remove('is-open')
          other.querySelector('.sop-scale-toggle')?.setAttribute('aria-expanded', 'false')
        })
      }
    })
    box.addEventListener('click', (e) => {
      const b = e.target.closest('.sop-scale-btn')
      if (!b) return
      const f = parseFloat(b.getAttribute('data-f'))
      card.__sopOverrideFactor = f
      box.querySelectorAll('.sop-scale-btn').forEach((btn) => {
        btn.classList.toggle('on', parseFloat(btn.getAttribute('data-f')) === f)
      })
      box.querySelector('.sop-scale-input').value = ''
      scaleCardAmounts(card, f)
    })
    box.querySelector('.sop-scale-input').addEventListener('input', (e) => {
      if (e.target.value === '') {
        card.__sopOverrideFactor = null
        box.querySelectorAll('.sop-scale-btn').forEach((btn) => btn.classList.remove('on'))
        scaleCardAmounts(card, effectiveFactor(card))
        return
      }
      const f = RC.clampFactor(e.target.value)
      card.__sopOverrideFactor = f
      box.querySelectorAll('.sop-scale-btn').forEach((btn) => btn.classList.remove('on'))
      scaleCardAmounts(card, f)
    })
  })
}

function onSearchInput() {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => {
    if (!bodyRef.value) return
    let visible = 0
    bodyRef.value.querySelectorAll('article.recipe-card').forEach((card) => {
      const hit = RC.matchRecipe(card.textContent || '', searchTerm.value)
      card.style.display = hit ? '' : 'none'
      if (hit) visible++
    })
    searchCount.value = searchTerm.value.trim() ? `找到 ${visible} 个匹配` : ''
  }, 120)
}

function toggleOnlyNew() {
  onlyNew.value = !onlyNew.value
  document.body.classList.toggle('sop-only-new', onlyNew.value)
}

function toggleShowInactive() {
  showInactive.value = !showInactive.value
  load()
}

async function openQr() {
  qrModalUrl.value = window.location.href
  await nextTick()
  if (qrCanvasRef.value) {
    QRCode.toCanvas(qrCanvasRef.value, qrModalUrl.value, { width: 220, margin: 1 })
  }
}

watch(slug, load)
watch(targetServingsQty, () => applyAllCardScales())
watch(() => route.query.focus, () => {
  if (!loading.value) nextTick(() => applyFocusFromQuery())
})
onMounted(() => {
  applyTheme(theme.value)
  applyFont(fontPx.value)
  document.body.classList.toggle('sop-density-compact', density.value)
  document.body.classList.toggle('sop-density-comfortable', !density.value)
  load()
})
onBeforeUnmount(() => {
  intersectionObserver?.disconnect()
  clearTimeout(searchDebounce)
  if (focusHighlightTimer != null) clearTimeout(focusHighlightTimer)
  unbindScaleDismiss()
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.style.removeProperty('--reader-fs')
  document.body.classList.remove('sop-density-compact', 'sop-density-comfortable', 'sop-only-new')
})
</script>

<template>
  <div>
    <header class="site-header no-print" style="position:static">
      <div class="site-header-inner">
        <router-link class="site-brand" to="/recipe">
          <span class="site-brand-mark" aria-hidden="true"><span class="site-brand-mark-inner">SOP</span></span>
          <span class="site-brand-text">
            <span class="site-brand-title">配方 SOP</span>
            <span class="site-brand-tagline">岗位配方 · 出品检核</span>
          </span>
        </router-link>
        <nav class="site-nav no-print">
          <router-link class="site-nav-link" to="/"><RecipeNavIcon name="home" :size="14" />返回主页</router-link>
          <router-link class="site-nav-link" to="/recipe"><RecipeNavIcon name="layout-grid" :size="14" />岗位列表</router-link>
          <router-link class="site-nav-link" to="/recipe/manage"><RecipeNavIcon name="sparkles" :size="14" />配方管理</router-link>
        </nav>
        <div class="sop-header-actions no-print">
          <input type="search" v-model="searchTerm" class="sop-search-input" placeholder="搜索配方…" autocomplete="off" @input="onSearchInput">
          <span class="sop-btn-group" role="group" aria-label="字号">
            <button type="button" class="btn btn-ghost btn-sm" @click="incFont(-1)">A−</button>
            <button type="button" class="btn btn-ghost btn-sm" @click="incFont(1)">A+</button>
          </span>
          <button type="button" class="btn btn-ghost sop-density-toggle" @click="toggleTheme">
            <span aria-hidden="true">◐</span><span>{{ RC.themeLabel(theme) }}</span>
          </button>
          <button type="button" class="btn btn-ghost sop-density-toggle" @click="toggleDensity">
            <span aria-hidden="true">▦</span><span>{{ density ? '网格' : '紧凑' }}</span>
          </button>
          <button type="button" class="btn btn-ghost sop-density-toggle" @click="openQr">
            <span aria-hidden="true">▣</span>二维码
          </button>
          <router-link class="print-button" :to="`/recipe/print?slug=${encodeURIComponent(slug)}`">
            <span aria-hidden="true">◱</span>打印预览
          </router-link>
        </div>
      </div>
    </header>
    <main class="site-main">
      <div class="sop-layout">
        <aside class="sop-toc no-print" id="sopToc" v-show="tocVisible" v-html="tocHtml"></aside>
        <article class="sop-article">
          <div class="sop-toolbar no-print">
            <router-link class="back-link" to="/recipe"><span class="back-link-icon" aria-hidden="true">←</span>返回列表</router-link>
            <span class="sop-toolbar-chip">{{ title }}</span>
            <div v-if="servingsControlVisible" class="sop-servings no-print" aria-live="polite">
              <label class="sop-servings-label" for="sop-target-servings">目标份数</label>
              <input
                id="sop-target-servings"
                v-model="targetServingsQty"
                type="number"
                inputmode="decimal"
                min="0.1"
                step="any"
                class="sop-servings-input"
                aria-label="目标份数"
              >
              <span class="sop-servings-unit">{{ servingsUnitLabel }}</span>
            </div>
            <button type="button" class="sop-chip" :aria-pressed="onlyNew" @click="toggleOnlyNew"><SvgIcon name="star" :size="12" /> 只看新品</button>
            <button type="button" class="sop-chip" :aria-pressed="showInactive" @click="toggleShowInactive">显示停用</button>
            <span class="sop-search-count" aria-live="polite">{{ searchCount }}</span>
          </div>
          <div class="sop-panel">
            <div v-if="loading" class="sop-body markdown-body">加载中…</div>
            <div v-else-if="errorMsg" class="sop-body markdown-body">{{ errorMsg }}</div>
            <div v-else ref="bodyRef" class="sop-body markdown-body" v-html="contentHtml"></div>
          </div>
        </article>
      </div>
    </main>

    <div v-if="qrModalUrl" class="print-preview-modal" @click.self="qrModalUrl = ''">
      <div class="print-preview-modal-backdrop" @click="qrModalUrl = ''"></div>
      <div class="sop-panel qr-modal">
        <h3 class="qr-modal-title">{{ title }} · 扫码查看</h3>
        <div class="qr-box"><canvas ref="qrCanvasRef"></canvas></div>
        <p class="qr-url">{{ qrModalUrl }}</p>
        <div class="qr-actions"><button type="button" class="btn btn-ghost" @click="qrModalUrl = ''">关闭</button></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.site-nav-link {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
</style>
