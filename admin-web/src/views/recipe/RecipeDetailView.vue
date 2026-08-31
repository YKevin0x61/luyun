<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import QRCode from 'qrcode'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import * as RC from '../../utils/recipeCore'
import { RECIPE_INGREDIENTS_AMOUNT_CLASS } from '../../utils/recipeIngredients'
import { parseBaseServingsQty, scaleAmount, servingsFactor } from '../../utils/recipeServings'
import { isRecipeDrawerPanel, nextRecipeDrawerState } from '../../utils/recipeDrawer'
import SvgIcon from '../../components/SvgIcon.vue'
import RecipeNavIcon from './RecipeNavIcon.vue'
import RecipeFormModal from '../../components/recipe/RecipeFormModal.vue'
import {
  RECIPE_BRAND_MARK,
  RECIPE_BRAND_TAGLINE,
  RECIPE_BRAND_TITLE,
  RECIPE_NAV_HOME_LABEL,
  RECIPE_NAV_MANAGE_LABEL,
  RECIPE_NAV_STATIONS_LABEL,
  recipeDocumentTitle,
} from '../../utils/recipeCopy'
import { deleteRecipeConfirmCopy } from '../../utils/recipeConfirmCopy'
import {
  readerToggleLabel,
  recipeCardId,
  recipeCardIsInactive,
  recipeCardName,
} from '../../utils/recipeReaderActions'
import {
  READER_DRAG_BLOCK_MQ,
  READER_DRAG_IGNORE_SELECTOR,
  applySubsetOrder,
  collectGridRecipeIds,
  eventElement,
  insertCardRelative,
  readerDragAllowed,
  sameSectionGrid,
} from '../../utils/recipeReaderDrag'

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
const fontPx = ref(RC.clampFontPx(RC.readPref(window.localStorage, 'sop.fontScale', '12')))
const searchTerm = ref('')
const searchCount = ref('')
const onlyNew = ref(false)
const showInactive = ref(false)
const qrModalUrl = ref('')
const qrCanvasRef = ref(null)
const targetServingsQty = ref('')
const servingsUnitLabel = ref('')
const servingsControlVisible = ref(false)
const drawerPanel = ref(null)
const drawerRef = ref(null)
const canEdit = ref(false)
const editRecipeId = ref(null)
const confirmDialog = reactive({
  open: false,
  title: '',
  body: '',
  confirmLabel: '确认',
  busy: false,
  error: '',
})
let confirmAction = null
const lastTriggerEls = {}
const SHEET_TITLES = { toc: '章节目录', more: '更多', font: '字号', servings: '目标份数', search: '搜索' }
const sheetOpen = computed(() => isRecipeDrawerPanel(drawerPanel.value))
const sheetTitle = computed(() => SHEET_TITLES[drawerPanel.value] || '')

let searchDebounce = null
let desktopMq = null
let dragBlockMq = null
let intersectionObserver = null
let focusHighlightTimer = null
let scaleOutsideHandler = null
let scaleEscHandler = null
let dragRoot = null
let dragFromId = null
let reorderBusy = false
const dragBlocked = ref(false)
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
  unbindReaderDrag()
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await api.get(`/api/recipes/stations/${encodeURIComponent(slug.value)}`, {
      include_inactive: showInactive.value ? 1 : undefined,
    })
    title.value = data.title
    contentHtml.value = data.content_html
    document.title = recipeDocumentTitle(data.title)
  } catch (e) {
    errorMsg.value = e.status === 404 ? '未找到该岗位' : '无法加载该岗位配方，请稍后重试'
  } finally {
    loading.value = false
  }
  if (!errorMsg.value) {
    await nextTick()
    afterContentRendered()
  }
}

function afterContentRendered() {
  buildToc()
  readPageServingsDefault()
  injectScaleControls()
  applyAllCardScales()
  bindScaleDismiss()
  injectCardActions()
  bindReaderDrag()
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
    tocEl.querySelectorAll('a[data-toc]').forEach((a) => {
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
              document.querySelectorAll('a[data-toc]').forEach((a) => {
                a.classList.toggle('on', a.getAttribute('data-toc') === en.target.id)
              })
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

function injectCardActions() {
  if (!bodyRef.value) return
  bodyRef.value.querySelectorAll('article.recipe-card').forEach((card) => {
    let wrap = card.querySelector('.recipe-card-actions')
    if (!wrap) {
      wrap = document.createElement('div')
      wrap.className = 'recipe-card-actions no-print'
      wrap.setAttribute('role', 'group')
      wrap.setAttribute('aria-label', '配方操作')
      const copy = document.createElement('button')
      copy.type = 'button'
      copy.className = 'recipe-copy'
      copy.textContent = '复制'
      copy.addEventListener('click', (e) => {
        e.stopPropagation()
        const head = card.querySelector('.recipe-card-head')
        const bodyEl = card.querySelector('.recipe-card-body')
        const text = `${head ? head.textContent : ''}\n${bodyEl ? bodyEl.textContent : ''}`.trim()
        copyText(text).then(() => {
          const old = copy.innerHTML
          copy.innerHTML = `${CHECK_ICON_SVG} 已复制`
          setTimeout(() => { copy.innerHTML = old }, 900)
        })
      })
      wrap.appendChild(copy)
      card.appendChild(wrap)
    }
    syncCardAdminActions(card, wrap)
  })
}

function makeReaderActionButton(action, label, extraClass, handler) {
  const btn = document.createElement('button')
  btn.type = 'button'
  btn.className = extraClass ? `recipe-card-action ${extraClass}` : 'recipe-card-action'
  btn.setAttribute('data-reader-action', action)
  btn.textContent = label
  btn.addEventListener('click', (e) => {
    e.stopPropagation()
    handler()
  })
  return btn
}

function syncCardAdminActions(card, wrap) {
  wrap.querySelectorAll('[data-reader-action]').forEach((el) => el.remove())
  if (!canEdit.value) return
  const id = recipeCardId(card)
  if (!id) return
  const inactive = recipeCardIsInactive(card)
  wrap.appendChild(makeReaderActionButton('delete', '删除', 'recipe-card-action--danger', () => {
    onReaderDelete(card)
  }))
  wrap.appendChild(makeReaderActionButton('toggle', readerToggleLabel(inactive), '', () => {
    onReaderToggle(card)
  }))
  wrap.appendChild(makeReaderActionButton('edit', '编辑', '', () => {
    onReaderEdit(card)
  }))
}

function askConfirm(copy, action) {
  confirmDialog.title = copy.title
  confirmDialog.body = copy.body
  confirmDialog.confirmLabel = copy.confirmLabel
  confirmDialog.error = ''
  confirmAction = action
  confirmDialog.open = true
}

function cancelConfirm() {
  if (confirmDialog.busy) return
  confirmDialog.open = false
  confirmDialog.error = ''
  confirmAction = null
}

async function runConfirm() {
  if (confirmDialog.busy) return
  confirmDialog.busy = true
  confirmDialog.error = ''
  try {
    await confirmAction?.()
    confirmDialog.open = false
    confirmAction = null
  } catch (e) {
    confirmDialog.error = e.message || '操作失败'
  } finally {
    confirmDialog.busy = false
  }
}

function onReaderDelete(card) {
  const id = recipeCardId(card)
  if (!id) return
  askConfirm(deleteRecipeConfirmCopy({ recipeName: recipeCardName(card) }), async () => {
    await api.delete(`/api/recipes/recipes/${id}`)
    await load()
  })
}

async function onReaderToggle(card) {
  const id = recipeCardId(card)
  if (!id) return
  try {
    await api.post(`/api/recipes/recipes/${id}/toggle-active`, {})
    await load()
  } catch (e) {
    window.alert(e.message || '无法更新配方状态')
  }
}

function onReaderEdit(card) {
  const id = recipeCardId(card)
  if (!id) return
  editRecipeId.value = id
}

function closeEditForm() {
  editRecipeId.value = null
}

async function onEditFormSaved() {
  closeEditForm()
  await load()
}

function readerDragEnabled() {
  return readerDragAllowed({ canEdit: canEdit.value, blocked: dragBlocked.value })
}

function cardFromDragEvent(e) {
  const card = eventElement(e)?.closest?.('article.recipe-card')
  if (!card || !dragRoot || !dragRoot.contains(card)) return null
  return card
}

function clearDropTargets(except) {
  if (!dragRoot) return
  dragRoot.querySelectorAll('article.recipe-card.is-drop-target').forEach((el) => {
    if (el !== except) el.classList.remove('is-drop-target')
  })
}

function syncCardDraggable() {
  if (!bodyRef.value) return
  const on = readerDragEnabled()
  bodyRef.value.querySelectorAll('article.recipe-card[data-recipe-id]').forEach((card) => {
    if (on) {
      card.setAttribute('draggable', 'true')
      card.classList.add('is-draggable')
    } else {
      card.removeAttribute('draggable')
      card.classList.remove('is-draggable', 'is-dragging', 'is-drop-target')
    }
  })
}

function onReaderDragStart(e) {
  if (!readerDragEnabled()) {
    e.preventDefault()
    return
  }
  if (eventElement(e)?.closest?.(READER_DRAG_IGNORE_SELECTOR)) {
    e.preventDefault()
    return
  }
  const card = cardFromDragEvent(e)
  const id = card?.getAttribute('data-recipe-id')
  if (!card || !id) {
    e.preventDefault()
    return
  }
  dragFromId = id
  card.classList.add('is-dragging')
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', id)
  }
}

function onReaderDragOver(e) {
  if (!readerDragEnabled() || !dragFromId) return
  const toCard = cardFromDragEvent(e)
  const fromCard = dragRoot?.querySelector(`article.recipe-card[data-recipe-id="${dragFromId}"]`)
  if (!toCard || !fromCard || !sameSectionGrid(fromCard, toCard)) {
    clearDropTargets()
    return
  }
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  toCard.classList.add('is-drop-target')
  clearDropTargets(toCard)
}

function onReaderDragEnd() {
  if (dragRoot) {
    dragRoot.querySelectorAll('article.recipe-card.is-dragging, article.recipe-card.is-drop-target').forEach((el) => {
      el.classList.remove('is-dragging', 'is-drop-target')
    })
  }
  dragFromId = null
}

async function persistGridOrder(grid) {
  if (reorderBusy || !slug.value) return
  const subsetIds = collectGridRecipeIds(grid)
  if (subsetIds.length < 2) return
  reorderBusy = true
  try {
    const data = await api.get(`/api/recipes/stations/${encodeURIComponent(slug.value)}/recipes`)
    const fullIds = (data.recipes || []).map((row) => row.id)
    const ids = applySubsetOrder(fullIds, subsetIds)
    const unchanged = ids.length === fullIds.length
      && ids.every((id, i) => Number(id) === Number(fullIds[i]))
    if (unchanged) return
    await api.put(
      `/api/recipes/stations/${encodeURIComponent(slug.value)}/recipes/reorder`,
      { ids },
    )
  } catch (e) {
    window.alert(e.message || '排序保存失败')
    await load()
  } finally {
    reorderBusy = false
  }
}

async function onReaderDrop(e) {
  e.preventDefault()
  const toCard = cardFromDragEvent(e)
  const rawFrom = dragFromId || (e.dataTransfer && e.dataTransfer.getData('text/plain'))
  const fromId = rawFrom && /^\d+$/.test(rawFrom) ? rawFrom : ''
  const fromCard = fromId && dragRoot
    ? dragRoot.querySelector(`article.recipe-card[data-recipe-id="${fromId}"]`)
    : null
  onReaderDragEnd()
  if (!readerDragEnabled() || !fromCard || !toCard) return
  if (!insertCardRelative(fromCard, toCard)) return
  await persistGridOrder(toCard.closest('.sop-section-grid'))
}

function bindReaderDrag() {
  unbindReaderDrag()
  if (!bodyRef.value) return
  dragRoot = bodyRef.value
  dragRoot.addEventListener('dragstart', onReaderDragStart)
  dragRoot.addEventListener('dragover', onReaderDragOver)
  dragRoot.addEventListener('drop', onReaderDrop)
  dragRoot.addEventListener('dragend', onReaderDragEnd)
  syncCardDraggable()
}

function unbindReaderDrag() {
  if (!dragRoot) return
  dragRoot.removeEventListener('dragstart', onReaderDragStart)
  dragRoot.removeEventListener('dragover', onReaderDragOver)
  dragRoot.removeEventListener('drop', onReaderDrop)
  dragRoot.removeEventListener('dragend', onReaderDragEnd)
  dragRoot.querySelectorAll('article.recipe-card').forEach((card) => {
    card.removeAttribute('draggable')
    card.classList.remove('is-draggable', 'is-dragging', 'is-drop-target')
  })
  dragRoot = null
  dragFromId = null
}

function onDragBlockMq(e) {
  dragBlocked.value = e.matches
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

function applyDrawer(next) {
  const restore = next.restoreFocus
  drawerPanel.value = next.open
  if (restore) {
    nextTick(() => lastTriggerEls[restore]?.focus())
  }
}

function onDrawerToggle(id, event) {
  if (id === 'servings' && !servingsControlVisible.value) return
  if (id === 'toc' && !tocVisible.value) return
  if (event && event.currentTarget instanceof HTMLElement) {
    lastTriggerEls[id] = event.currentTarget
  }
  applyDrawer(nextRecipeDrawerState({ open: drawerPanel.value }, { type: 'toggle', id }))
}

function onDrawerBackdrop() {
  applyDrawer(nextRecipeDrawerState({ open: drawerPanel.value }, { type: 'backdrop' }))
}

function onDrawerKeydown(e) {
  if (e.key !== 'Escape') return
  if (confirmDialog.open) {
    cancelConfirm()
    return
  }
  if (!drawerPanel.value) return
  applyDrawer(nextRecipeDrawerState({ open: drawerPanel.value }, { type: 'escape' }))
}

function onDesktopMq(e) {
  if (e.matches && drawerPanel.value) {
    applyDrawer(nextRecipeDrawerState({ open: drawerPanel.value }, { type: 'close' }))
  }
}

function onMobileTocClick(e) {
  const a = e.target.closest && e.target.closest('a[data-toc]')
  if (!a) return
  e.preventDefault()
  document.getElementById(a.getAttribute('data-toc'))?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  applyDrawer(nextRecipeDrawerState({ open: drawerPanel.value }, { type: 'close' }))
}

function onMoreQr() {
  applyDrawer(nextRecipeDrawerState({ open: drawerPanel.value }, { type: 'close' }))
  openQr()
}

async function loadEditAccess() {
  try {
    const resp = await fetch('/api/auth/status', { credentials: 'include' })
    if (!resp.ok) return
    const data = await resp.json()
    canEdit.value = !!data.logged_in
  } catch {
    canEdit.value = false
  }
}

watch(slug, load)
watch(targetServingsQty, () => applyAllCardScales())
watch(drawerPanel, (open) => {
  const lock = isRecipeDrawerPanel(open)
  document.body.style.overflow = lock ? 'hidden' : ''
  if (!lock) return
  nextTick(() => {
    if (open === 'servings') {
      document.getElementById('sop-target-servings-sheet')?.focus()
    } else if (open === 'search') {
      document.getElementById('sop-search-sheet')?.focus()
    } else if (open === 'font') {
      drawerRef.value?.querySelector('button')?.focus()
    } else {
      drawerRef.value?.focus()
    }
  })
})
watch(() => route.query.focus, () => {
  if (!loading.value) nextTick(() => applyFocusFromQuery())
})
watch([canEdit, dragBlocked], () => {
  injectCardActions()
  syncCardDraggable()
})
onMounted(() => {
  applyTheme(theme.value)
  applyFont(fontPx.value)
  document.body.classList.toggle('sop-density-compact', density.value)
  document.body.classList.toggle('sop-density-comfortable', !density.value)
  document.addEventListener('keydown', onDrawerKeydown)
  desktopMq = window.matchMedia('(min-width: 901px)')
  desktopMq.addEventListener('change', onDesktopMq)
  dragBlockMq = window.matchMedia(READER_DRAG_BLOCK_MQ)
  dragBlocked.value = dragBlockMq.matches
  dragBlockMq.addEventListener('change', onDragBlockMq)
  loadEditAccess()
  load()
})
onBeforeUnmount(() => {
  intersectionObserver?.disconnect()
  clearTimeout(searchDebounce)
  if (focusHighlightTimer != null) clearTimeout(focusHighlightTimer)
  unbindScaleDismiss()
  unbindReaderDrag()
  document.removeEventListener('keydown', onDrawerKeydown)
  desktopMq?.removeEventListener('change', onDesktopMq)
  dragBlockMq?.removeEventListener('change', onDragBlockMq)
  document.body.style.overflow = ''
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.style.removeProperty('--reader-fs')
  document.body.classList.remove('sop-density-compact', 'sop-density-comfortable', 'sop-only-new')
})
</script>

<template>
  <div class="sop-reader">
    <header class="site-header no-print" style="position:static">
      <div class="site-header-inner">
        <router-link class="site-brand" to="/recipe">
          <span class="site-brand-mark" aria-hidden="true"><span class="site-brand-mark-inner">{{ RECIPE_BRAND_MARK }}</span></span>
          <span class="site-brand-text">
            <span class="site-brand-title">{{ RECIPE_BRAND_TITLE }}</span>
            <span class="site-brand-tagline">{{ RECIPE_BRAND_TAGLINE }}</span>
          </span>
        </router-link>
        <nav class="site-nav no-print">
          <router-link class="site-nav-link" to="/"><RecipeNavIcon name="home" :size="14" />{{ RECIPE_NAV_HOME_LABEL }}</router-link>
          <router-link class="site-nav-link" to="/recipe"><RecipeNavIcon name="layout-grid" :size="14" />{{ RECIPE_NAV_STATIONS_LABEL }}</router-link>
          <router-link class="site-nav-link" to="/recipe/manage"><RecipeNavIcon name="sparkles" :size="14" />{{ RECIPE_NAV_MANAGE_LABEL }}</router-link>
        </nav>
        <div class="sop-header-actions no-print">
          <button
            type="button"
            class="sop-search-toggle"
            aria-label="搜索"
            :aria-expanded="drawerPanel === 'search'"
            @click="onDrawerToggle('search', $event)"
          >
            <SvgIcon name="search" :size="16" />
          </button>
          <input
            type="search"
            v-model="searchTerm"
            class="sop-search-input"
            placeholder="搜索配方…"
            autocomplete="off"
            @input="onSearchInput"
          >
          <span class="sop-header-desktop">
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
          </span>
        </div>
      </div>
    </header>
    <main class="site-main">
      <div class="sop-layout" :class="{ 'sop-layout--with-toc': tocVisible }">
        <aside class="sop-toc no-print" id="sopToc" v-show="tocVisible" v-html="tocHtml"></aside>
        <article class="sop-article">
          <div class="sop-toolbar no-print">
            <router-link class="back-link" to="/recipe"><span class="back-link-icon" aria-hidden="true">←</span>返回列表</router-link>
            <span class="sop-toolbar-chip">{{ title }}</span>
            <div v-if="servingsControlVisible" class="sop-servings sop-servings--toolbar no-print" aria-live="polite">
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

    <nav class="sop-bottom-bar no-print" aria-label="阅读工具">
      <button
        type="button"
        aria-label="搜索"
        :aria-pressed="drawerPanel === 'search'"
        @click="onDrawerToggle('search', $event)"
      >
        <SvgIcon name="search" :size="18" />
        <span>搜索</span>
      </button>
      <button
        type="button"
        aria-label="目录"
        :disabled="!tocVisible"
        :aria-pressed="drawerPanel === 'toc'"
        @click="onDrawerToggle('toc', $event)"
      >
        <SvgIcon name="scroll-text" :size="18" />
        <span>目录</span>
      </button>
      <button
        type="button"
        aria-label="字号"
        :aria-pressed="drawerPanel === 'font'"
        @click="onDrawerToggle('font', $event)"
      >
        <span class="sop-bottom-bar-font-mark" aria-hidden="true">A</span>
        <span>字号</span>
      </button>
      <button
        type="button"
        aria-label="份数"
        :disabled="!servingsControlVisible"
        :aria-pressed="drawerPanel === 'servings'"
        @click="onDrawerToggle('servings', $event)"
      >
        <SvgIcon name="bowl" :size="18" />
        <span>份数</span>
      </button>
      <button
        type="button"
        aria-label="更多"
        :aria-pressed="drawerPanel === 'more'"
        @click="onDrawerToggle('more', $event)"
      >
        <SvgIcon name="menu" :size="18" />
        <span>更多</span>
      </button>
    </nav>

    <Teleport to="body">
      <Transition name="sop-sheet">
        <div v-if="sheetOpen" class="sop-drawer-root no-print">
          <div class="sop-drawer-backdrop" @click="onDrawerBackdrop"></div>
          <div
            ref="drawerRef"
            class="sop-drawer"
            role="dialog"
            aria-modal="true"
            :aria-label="sheetTitle"
            tabindex="-1"
          >
            <h2 class="sop-drawer-title">{{ sheetTitle }}</h2>
            <div v-if="drawerPanel === 'search'" class="sop-drawer-search">
              <input
                id="sop-search-sheet"
                v-model="searchTerm"
                type="search"
                class="sop-search-input"
                placeholder="搜索配方…"
                autocomplete="off"
                aria-label="搜索配方"
                @input="onSearchInput"
              >
              <span class="sop-search-count" aria-live="polite">{{ searchCount }}</span>
            </div>
            <div
              v-else-if="drawerPanel === 'toc'"
              class="sop-toc"
              v-html="tocHtml"
              @click="onMobileTocClick"
            ></div>
            <div v-else-if="drawerPanel === 'font'" class="sop-btn-group" role="group" aria-label="字号">
              <button type="button" class="btn btn-ghost" aria-label="减小字号" @click="incFont(-1)">A−</button>
              <button type="button" class="btn btn-ghost" aria-label="增大字号" @click="incFont(1)">A+</button>
            </div>
            <div v-else-if="drawerPanel === 'servings'" class="sop-servings" aria-live="polite">
              <label class="sop-servings-label" for="sop-target-servings-sheet">目标份数</label>
              <input
                id="sop-target-servings-sheet"
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
            <div v-else-if="drawerPanel === 'more'" class="sop-drawer-more">
              <button type="button" class="btn btn-ghost" aria-label="切换主题" @click="toggleTheme">
                <span aria-hidden="true">◐</span><span>{{ RC.themeLabel(theme) }}</span>
              </button>
              <button type="button" class="btn btn-ghost" aria-label="切换密度" @click="toggleDensity">
                <span aria-hidden="true">▦</span><span>{{ density ? '网格' : '紧凑' }}</span>
              </button>
              <button type="button" class="btn btn-ghost" aria-label="二维码" @click="onMoreQr">
                <span aria-hidden="true">▣</span>二维码
              </button>
              <router-link
                class="print-button"
                aria-label="打印预览"
                :to="`/recipe/print?slug=${encodeURIComponent(slug)}`"
              >
                <span aria-hidden="true">◱</span>打印预览
              </router-link>
              <router-link v-if="canEdit" class="btn btn-ghost" to="/recipe/manage">{{ RECIPE_NAV_MANAGE_LABEL }}</router-link>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <div v-if="qrModalUrl" class="print-preview-modal" @click.self="qrModalUrl = ''">
      <div class="print-preview-modal-backdrop" @click="qrModalUrl = ''"></div>
      <div class="sop-panel qr-modal">
        <h3 class="qr-modal-title">{{ title }} · 扫码查看</h3>
        <div class="qr-box"><canvas ref="qrCanvasRef"></canvas></div>
        <p class="qr-url">{{ qrModalUrl }}</p>
        <div class="qr-actions"><button type="button" class="btn btn-ghost" @click="qrModalUrl = ''">关闭</button></div>
      </div>
    </div>

    <RecipeFormModal
      v-if="editRecipeId"
      :key="editRecipeId"
      :station-slug="String(slug || '')"
      :station-title="title"
      :recipe-id="editRecipeId"
      @close="closeEditForm"
      @saved="onEditFormSaved"
      @review-confirmed="load"
    />

    <div
      v-if="confirmDialog.open"
      class="print-preview-modal"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="recipe-reader-confirm-title"
      aria-describedby="recipe-reader-confirm-body"
      @click.self="cancelConfirm"
    >
      <div class="print-preview-modal-backdrop" @click="cancelConfirm"></div>
      <div class="sop-panel qr-modal">
        <h3 id="recipe-reader-confirm-title" class="qr-modal-title">{{ confirmDialog.title }}</h3>
        <p id="recipe-reader-confirm-body" class="qr-url">{{ confirmDialog.body }}</p>
        <p v-if="confirmDialog.error" class="flash flash-error">{{ confirmDialog.error }}</p>
        <div class="qr-actions" style="gap:.5rem">
          <button
            type="button"
            class="btn btn-danger"
            :disabled="confirmDialog.busy"
            @click="runConfirm"
          >{{ confirmDialog.confirmLabel }}</button>
          <button type="button" class="btn btn-ghost" :disabled="confirmDialog.busy" @click="cancelConfirm">取消</button>
        </div>
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
