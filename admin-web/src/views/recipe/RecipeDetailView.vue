<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import QRCode from 'qrcode'
import { api } from '../../api/client'
import { useRecipeAdmin } from '../../composables/useRecipeAdmin'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import * as RC from '../../utils/recipeCore'
import { RECIPE_INGREDIENTS_AMOUNT_CLASS } from '../../utils/recipeIngredients'
import { parseBaseServingsQty, scaleAmount, servingsFactor } from '../../utils/recipeServings'
import { isRecipeDrawerPanel, nextRecipeDrawerState } from '../../utils/recipeDrawer'
import { RECIPE_HOME_PATH, RECIPE_MANAGE_PATH, RECIPE_PRINT_PATH } from '../../utils/recipePaths'
import SvgIcon from '../../components/SvgIcon.vue'
import RecipeNavIcon from './RecipeNavIcon.vue'
import RecipeExitButton from '../../components/recipe/RecipeExitButton.vue'
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
// 章节 <3 时不再占左栏：改成正文上方一条横滚 tab（v3 版式审查）。
const tocCompact = ref(false)

const theme = ref(RC.readPref(window.localStorage, 'sop.theme', 'auto'))
const density = ref(RC.readPref(window.localStorage, 'sop.density', 'compact') !== 'grid')
const fontPx = ref(RC.clampFontPx(RC.readPref(window.localStorage, 'sop.fontScale', String(RC.defaultFontPx()))))
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
// 票 07：能不能编辑由**工作台身份**决定（`useRecipeAdmin`），不再问一次登录状态 ——
// 「谁能编辑配方」以前只由一个前端登录开关决定，界面上没有任何来源可查（audit 条目 11）。
// 拖拽排序 / 删除这些入口跟着它一起收；接口自己也有一道 401。
const { isAdmin: canEdit } = useRecipeAdmin()
/* 顶栏那两颗管理端入口按身份出现，模板读的是 `isAdmin`（与另外四个阅读页同一写法）；
   拖拽排序那几个入口历史上读 `canEdit`。两个名字指向同一个值，别在模板里混用。 */
const isAdmin = canEdit
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
  tocCompact.value = sections.length < 3
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
    <header class="site-header no-print sop-site-header">
      <div class="site-header-inner">
        <router-link class="site-brand" :to="RECIPE_HOME_PATH">
          <span class="site-brand-mark" aria-hidden="true"><span class="site-brand-mark-inner">{{ RECIPE_BRAND_MARK }}</span></span>
          <span class="site-brand-text">
            <span class="site-brand-title">{{ RECIPE_BRAND_TITLE }}</span>
            <span class="site-brand-tagline">{{ RECIPE_BRAND_TAGLINE }}</span>
          </span>
        </router-link>
        <nav class="site-nav no-print">
          <!-- 三颗入口按身份出现（票 07）：`/`（管理后台）与配方管理都是管理端那一档的页，
               扫码进来的厨师点它们只会被弹去登录页（navigation-audit 条目 4）。判据是
               工作台身份（`useRecipeAdmin`），不是「有没有登录」。 -->
          <router-link v-if="isAdmin" class="site-nav-link" to="/"><RecipeNavIcon name="home" :size="14" />{{ RECIPE_NAV_HOME_LABEL }}</router-link>
          <router-link class="site-nav-link" :to="RECIPE_HOME_PATH"><RecipeNavIcon name="layout-grid" :size="14" />{{ RECIPE_NAV_STATIONS_LABEL }}</router-link>
          <router-link v-if="isAdmin" class="site-nav-link" :to="RECIPE_MANAGE_PATH"><RecipeNavIcon name="sparkles" :size="14" />{{ RECIPE_NAV_MANAGE_LABEL }}</router-link>
          <!-- 退出入口（票 10，spec 故事 47）：沉浸页没有工作台外壳，退出挂在这里。
               动作与工作台那颗是同一个（`useWorkbenchLogout` 按身份派发）。 -->
          <RecipeExitButton />
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
            <router-link class="print-button" :to="`${RECIPE_PRINT_PATH}?slug=${encodeURIComponent(slug)}`">
              <span aria-hidden="true">◱</span>打印预览
            </router-link>
          </span>
        </div>
      </div>
    </header>
    <main class="site-main">
      <div class="sop-layout" :class="{ 'sop-layout--with-toc': tocVisible && !tocCompact }">
        <aside v-if="tocVisible && !tocCompact" class="sop-toc no-print" id="sopToc" v-html="tocHtml"></aside>
        <article class="sop-article">
          <div class="sop-toolbar no-print">
            <router-link class="back-link" :to="RECIPE_HOME_PATH"><span class="back-link-icon" aria-hidden="true">←</span>返回列表</router-link>
            <div class="reader-toolbar-copy">
              <p class="reader-kicker">配方 · 阅读</p>
              <span class="reader-title sop-toolbar-chip">{{ title }}</span>
            </div>
            <div class="reader-toolbar-tools">
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
          </div>
          <nav
            v-if="tocVisible && tocCompact"
            id="sopToc"
            class="sop-toc-tabs no-print"
            aria-label="章节目录"
            v-html="tocHtml"
          ></nav>
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
                :to="`${RECIPE_PRINT_PATH}?slug=${encodeURIComponent(slug)}`"
              >
                <span aria-hidden="true">◱</span>打印预览
              </router-link>
              <router-link v-if="isAdmin" class="btn btn-ghost" :to="RECIPE_MANAGE_PATH">{{ RECIPE_NAV_MANAGE_LABEL }}</router-link>
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
        <div class="qr-actions qr-actions-confirm">
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
/* 沉浸阅读面：页头不粘（原来写在内联 style 里）。**只碰外观**：
   `.sop-bottom-bar` 的 fixed 定位、`.sop-reader` 那套滚动规则一概不动。 */
.sop-site-header {
  position: static;
}
.site-nav-link {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
.qr-actions-confirm {
  gap: var(--sp-2);
}

/* ── 页头式工具栏：kicker / 标题 / 右侧工具（B 案 v2 页面骨架）───────── */
.reader-toolbar-copy {
  min-width: 0;
}
.reader-kicker {
  margin: 0 0 5px;
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .26em;
  color: var(--ink-faint);
}
.reader-title {
  display: inline-block;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink);
  font: 600 18px/1.2 var(--font-sans);
  letter-spacing: .01em;
}
.reader-toolbar-tools {
  margin-left: auto;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

/* 章节 <3：正文上方的横滚 tab（不占左栏） */
.sop-toc-tabs {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  margin: 0 0 12px;
  padding: 2px 0 8px;
  border-bottom: 1px solid var(--line);
}
.sop-toc-tabs h5 {
  display: none;
}
.sop-toc-tabs :deep(a) {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-1);
  font-size: 12px;
  color: var(--ink-dim);
  text-decoration: none;
}
.sop-toc-tabs :deep(a .n) {
  font-family: var(--font-mono);
  color: var(--ink-faint);
}
.sop-toc-tabs :deep(a.on) {
  border-color: var(--line-strong);
  background: var(--surface-2);
  color: var(--ink);
  box-shadow: inset 2px 0 0 var(--action);
}
.reader-led {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
  flex: 0 0 6px;
}
.reader-led.ok { background: var(--ok); box-shadow: 0 0 0 3px var(--wb-ok-a16); }
.reader-led.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.reader-led.bad { background: var(--danger); box-shadow: 0 0 0 3px var(--wb-danger-a16); }
.reader-led.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }

/* ── B 案「钢面 · 仪」（屏幕）────────────────────────────────────────────
   `recipe.css` 里章节头是整条薄荷底、配方卡是 10px 圆角 + 顶部渐变光条 + 悬停浮起；
   B 案要：章节头退成「LED + 刻度小标题」，配方卡是唯一面板（配方名当面板头），
   卡内用料 / 步骤 / 小贴士用 border-top 分成三段（次级分组，不再卡片套卡片）。
   全部圈在 `@media screen`：打印仍走 `recipe.css` 末尾那套白纸版面，
   一个字都不改。 */
@media screen {
  .sop-toolbar {
    display: flex;
    align-items: flex-end;
    flex-wrap: wrap;
    gap: 10px 14px;
    margin-bottom: 14px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--line);
  }
  .back-link {
    border-radius: var(--r-1);
    color: var(--ink-dim);
    font-size: 12px;
    font-weight: 600;
  }
  .sop-toolbar-chip {
    border-radius: var(--r-1);
    border: 1px solid var(--line-strong);
    background: var(--surface-1);
    color: var(--ink);
  }
  .sop-chip {
    border-radius: var(--r-1);
    font-size: 11px;
    font-weight: 600;
    color: var(--ink-dim);
  }

  /* 阅读正文宽限 880px（v3：卡片网格 280 起跳铺满这个宽度，不再拉满整屏） */
  .sop-article {
    max-width: 880px;
    margin: 0 auto;
  }
  .sop-layout--with-toc .sop-article {
    margin: 0;
  }

  /* 阅读面板 */
  .sop-panel {
    border-color: var(--line);
    border-radius: var(--r-2);
    box-shadow: var(--hy-shadow-sm);
  }

  /* 章节目录（aside）：面板 + 行，选中项用左条 + 正文色，不再整块薄荷填充。 */
  .sop-toc {
    border: 1px solid var(--line);
    border-radius: var(--r-2);
    background: var(--surface-1);
    box-shadow: var(--hy-shadow-sm);
  }
  .sop-toc :deep(a) {
    border-radius: var(--r-1);
    font-size: 12px;
    color: var(--ink-dim);
  }
  .sop-toc :deep(a .n) {
    font-family: var(--font-mono);
    color: var(--ink-faint);
  }
  .sop-toc :deep(a:hover) {
    background: var(--surface-2);
    color: var(--ink);
  }
  .sop-toc :deep(a.on) {
    background: var(--surface-2);
    color: var(--ink);
    box-shadow: inset 2px 0 0 var(--action);
  }
  .sop-toc :deep(a.on .n) {
    color: var(--ink-faint);
  }

  /* 文档：章节 = 小节标题 + 卡片网格（不再堆第三种卡片） */
  .sop-body :deep(.sop-doc) {
    gap: 18px;
  }
  .sop-body :deep(.sop-section) {
    gap: 10px;
  }
  .sop-body :deep(.sop-section-head h2) {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 0 0 8px;
    border: 0;
    border-bottom: 1px solid var(--line);
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    font: 600 13px/1.2 var(--font-sans);
    letter-spacing: .06em;
    color: var(--ink);
  }
  .sop-body :deep(.sop-section-head h2::before) {
    content: "";
    flex: 0 0 6px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--action);
    box-shadow: 0 0 0 3px var(--wb-tint-14);
  }
  .sop-body :deep(.sop-section-intro) {
    font-size: 12.5px;
    color: var(--ink-dim);
  }
  .sop-body :deep(.sop-section-grid) {
    gap: 10px;
    /* v3：卡片网格按 280px 起跳铺满正文宽，不再 3 张 210px 窄卡并排 */
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)) !important;
  }

  /* 配方卡 = 面板（唯一卡片语言）：去顶部渐变光条、去悬停浮起。 */
  .sop-body :deep(.recipe-card) {
    border: 1px solid var(--line);
    border-radius: var(--r-2);
    background: var(--surface-1);
    box-shadow: var(--hy-shadow-sm);
  }
  .sop-body :deep(.recipe-card::before) {
    display: none;
  }
  .sop-body :deep(.recipe-card:hover) {
    transform: none;
    border-color: var(--line-strong);
    box-shadow: var(--hy-shadow-sm);
  }
  .sop-body :deep(.recipe-card.recipe-card--focus) {
    border-color: var(--action);
    box-shadow: 0 0 0 3px var(--wb-tint-14), var(--hy-shadow-sm);
  }
  .sop-body :deep(.recipe-card-head) {
    background: var(--surface-2);
    border-bottom: 1px solid var(--line);
  }
  .sop-body :deep(.recipe-card-head h3) {
    padding: 11px 14px;
    font: 600 12.5px/1.3 var(--font-sans);
    letter-spacing: .06em;
    color: var(--ink);
  }
  /* 用料 / 步骤 / 小贴士：卡内三段，用 border-top 分隔（次级分组）。 */
  .sop-body :deep(.recipe-card-body) {
    padding: 0;
    line-height: 1.5;
  }
  .sop-body :deep(.recipe-card-body > *) {
    margin: 0;
    padding: 10px 14px;
    border-top: 1px solid var(--wb-ink-a08);
  }
  .sop-body :deep(.recipe-card-body > :first-child) {
    border-top: 0;
  }
  /* 用料：表格退成行（名称左 / 用量右对齐 mono）。 */
  .sop-body :deep(.recipe-card-body table) {
    margin: 0;
    border: 0;
    border-collapse: collapse;
    border-radius: 0;
    font-size: inherit;
  }
  .sop-body :deep(.recipe-ingredients-name) {
    border-right: 0;
  }
  .sop-body :deep(.recipe-ingredients-amount) {
    border-right: 0;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    color: var(--ink);
  }
  .sop-body :deep(.recipe-card-body td) {
    border-right: 0;
    border-bottom: 1px solid var(--wb-ink-a08);
  }
  .sop-body :deep(.recipe-card-body tr:last-child td) {
    border-bottom: 0;
  }
  /* 步骤：编号仍是圆形小记号，换成动作色。 */
  .sop-body :deep(ol.recipe-steps > li.recipe-steps-item::before) {
    color: var(--action);
    background: var(--wb-tint-08);
    border-color: var(--line-strong);
  }
  /* 小贴士：提示块用 warn 左条，不做整块高饱和填充。 */
  .sop-body :deep(ul.recipe-tips) {
    border-left: 3px solid var(--warn);
    background: var(--surface-2);
  }
  /* 新品 / 停用：状态用小徽章。 */
  .sop-body :deep(article.recipe-card.recipe-card--new) {
    border-color: var(--wb-warn-a45);
  }
  .sop-body :deep(article.recipe-card.recipe-card--new::after) {
    top: 10px;
    right: 12px;
    padding: 2px 8px;
    border: 1px solid var(--wb-warn-a45);
    border-radius: var(--r-full);
    background: var(--wb-warn-a10);
    color: var(--warn);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .06em;
  }
  .sop-body :deep(article.recipe-card.recipe-card--new .recipe-card-head) {
    background: var(--surface-2);
    border-bottom-color: var(--line);
  }
  .sop-body :deep(article.recipe-card.recipe-card--new .recipe-card-head h3) {
    color: var(--ink);
    padding-right: 3.6rem;
  }
  .sop-body :deep(article.recipe-card.recipe-card--inactive::after) {
    top: 10px;
    right: 12px;
    padding: 2px 8px;
    border-radius: var(--r-full);
    font-size: 10px;
    font-weight: 600;
  }
}
@media (max-width: 720px) {
  /* v3：390 下标题不套 chip 框 */
  .reader-title.sop-toolbar-chip {
    border: 0;
    background: transparent;
    padding: 0;
  }
}
</style>
