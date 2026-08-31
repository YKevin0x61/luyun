<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../../api/client'
import RecipeCheckbox from './RecipeCheckbox.vue'
import {
  buildSectionOptions,
  canonicalizeSection,
  defaultSectionForNewRecipe,
} from '../../utils/recipeManageOrder'
import { SCALE_UNITS } from '../../utils/recipeCore'
import {
  addIngredientRow,
  dropBlankIngredientRows,
  moveIngredientRow,
  removeIngredientRow,
} from '../../utils/recipeIngredients'
import {
  addStepRow,
  dropBlankStepRows,
  moveStepRow,
  removeStepRow,
  renderStructuredRecipePreviewHtml,
} from '../../utils/recipeSteps'
import {
  addTipRow,
  dropBlankTipRows,
  removeTipRow,
} from '../../utils/recipeTips'
import {
  recipeHasLegacyMarkdown,
  recipeNeedsReview,
} from '../../utils/recipeReview'
import { discardRecipeEditsCopy } from '../../utils/recipeConfirmCopy'
import { recipeFormIsDirty, snapshotRecipeForm } from '../../utils/recipeFormDirty'

const INGREDIENT_FIELD_MAX_LEN = 120
const STEP_TEXT_MAX_LEN = 120
const TIP_TEXT_MAX_LEN = 120

const props = defineProps({
  stationSlug: { type: String, required: true },
  stationTitle: { type: String, default: '' },
  recipeId: { type: [Number, null], default: null },
})

const emit = defineEmits(['close', 'saved', 'review-confirmed'])

const recipeForm = reactive(blankForm())
const recipeFormFieldsRef = ref(null)
const submitBusy = ref(false)
const confirmReviewBusy = ref(false)
const errorMsg = ref('')
const loading = ref(true)
const baseline = ref('')
const ingredientUndo = ref(null)
const stepUndo = ref(null)
const tipUndo = ref(null)
const ingredientDragIndex = ref(null)
const stepDragIndex = ref(null)
const confirmDialog = reactive({
  open: false,
  title: '',
  body: '',
  confirmLabel: '确认',
  busy: false,
})
let confirmAction = null
let previousOverflow = ''

const sectionOptions = computed(() => buildSectionOptions())
const structuredPreviewHtml = computed(() => renderStructuredRecipePreviewHtml(
  recipeForm.ingredients.map((row) => ({
    name: row.name,
    amount: row.amount,
    unit: row.unit,
  })),
  recipeForm.steps,
  recipeForm.tips,
))
const recipeFormHasBody = computed(() => String(recipeForm.body || '').trim() !== '')
const titleText = computed(() => {
  const station = String(props.stationTitle || '').trim() || '配方'
  return `${station} · ${recipeForm.id ? '编辑配方' : '新增配方'}`
})

function blankForm() {
  return {
    id: null,
    section: defaultSectionForNewRecipe(),
    recipe_name: '',
    body: '',
    sort_order: null,
    is_new: false,
    ingredients: [],
    steps: [],
    tips: [],
    base_servings_qty: '',
    base_servings_unit: '',
    needs_review: 0,
    legacy_markdown: '',
  }
}

function applyBlank() {
  Object.assign(recipeForm, blankForm())
  ingredientUndo.value = null
  stepUndo.value = null
  tipUndo.value = null
}

function applyRecord(id, r) {
  recipeForm.id = id
  recipeForm.section = canonicalizeSection(r.section)
  recipeForm.recipe_name = r.recipe_name
  recipeForm.body = r.body_markdown
  recipeForm.sort_order = r.sort_order
  recipeForm.is_new = !!r.is_new
  recipeForm.ingredients = (r.ingredients || []).map((row) => ({
    name: row.name || '',
    amount: row.amount || '',
    unit: row.unit || '',
  }))
  recipeForm.steps = (r.steps || []).map((text) => String(text || ''))
  recipeForm.tips = (r.tips || []).map((text) => String(text || ''))
  recipeForm.base_servings_qty = r.base_servings_qty == null ? '' : String(r.base_servings_qty)
  recipeForm.base_servings_unit = r.base_servings_unit || ''
  recipeForm.needs_review = recipeNeedsReview(r) ? 1 : 0
  recipeForm.legacy_markdown = r.legacy_markdown == null ? '' : String(r.legacy_markdown)
  ingredientUndo.value = null
  stepUndo.value = null
  tipUndo.value = null
}

function servingsQtyPayload(raw) {
  if (raw === '' || raw == null) return 0
  const qty = Number(raw)
  if (!Number.isFinite(qty)) return 0
  return qty
}

function askConfirm(copy, action) {
  confirmDialog.title = copy.title
  confirmDialog.body = copy.body
  confirmDialog.confirmLabel = copy.confirmLabel
  confirmAction = action
  confirmDialog.open = true
}

function cancelConfirm() {
  if (confirmDialog.busy) return
  confirmDialog.open = false
  confirmAction = null
}

async function runConfirm() {
  if (confirmDialog.busy) return
  confirmDialog.busy = true
  try {
    await confirmAction?.()
    confirmDialog.open = false
    confirmAction = null
  } finally {
    confirmDialog.busy = false
  }
}

function requestClose() {
  if (submitBusy.value) return
  if (recipeFormIsDirty(recipeForm, baseline.value)) {
    askConfirm(discardRecipeEditsCopy(), () => emit('close'))
    return
  }
  emit('close')
}

async function submitRecipeForm() {
  if (submitBusy.value || loading.value) return
  errorMsg.value = ''
  submitBusy.value = true
  const payload = {
    section: recipeForm.section,
    recipe_name: recipeForm.recipe_name,
    body: recipeForm.body,
    sort_order: recipeForm.id ? recipeForm.sort_order : null,
    is_new: recipeForm.is_new,
    ingredients: dropBlankIngredientRows(recipeForm.ingredients).map((row) => ({
      name: row.name,
      amount: row.amount,
      unit: row.unit,
    })),
    steps: dropBlankStepRows(recipeForm.steps).map((text) => String(text).trim()),
    tips: dropBlankTipRows(recipeForm.tips).map((text) => String(text).trim()),
    base_servings_qty: servingsQtyPayload(recipeForm.base_servings_qty),
    base_servings_unit: String(recipeForm.base_servings_unit || '').trim(),
  }
  try {
    if (recipeForm.id) {
      await api.put(`/api/recipes/recipes/${recipeForm.id}`, payload)
    } else {
      await api.post(`/api/recipes/stations/${encodeURIComponent(props.stationSlug)}/recipes`, payload)
    }
    emit('saved')
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    submitBusy.value = false
  }
}

async function confirmReview() {
  if (!recipeForm.id || confirmReviewBusy.value) return
  errorMsg.value = ''
  confirmReviewBusy.value = true
  try {
    const updated = await api.post(`/api/recipes/recipes/${recipeForm.id}/confirm-review`, {})
    recipeForm.needs_review = 0
    if (updated && updated.legacy_markdown != null) {
      recipeForm.legacy_markdown = String(updated.legacy_markdown)
    }
    baseline.value = snapshotRecipeForm(recipeForm)
    emit('review-confirmed', recipeForm.id)
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    confirmReviewBusy.value = false
  }
}

async function focusLastField(selector) {
  await nextTick()
  const root = recipeFormFieldsRef.value
  if (!root) return
  const nodes = root.querySelectorAll(selector)
  const last = nodes[nodes.length - 1]
  if (last && typeof last.focus === 'function') last.focus()
}

function addIngredient() {
  recipeForm.ingredients = addIngredientRow(recipeForm.ingredients)
  focusLastField('[aria-label="用料名称"]')
}

function removeIngredient(idx) {
  const removed = recipeForm.ingredients[idx]
  if (!removed) return
  recipeForm.ingredients = removeIngredientRow(recipeForm.ingredients, idx)
  ingredientUndo.value = { index: idx, row: removed }
}

function undoIngredientRemove() {
  const pending = ingredientUndo.value
  if (!pending) return
  const list = [...recipeForm.ingredients]
  const insertAt = Math.min(pending.index, list.length)
  list.splice(insertAt, 0, pending.row)
  recipeForm.ingredients = list
  ingredientUndo.value = null
}

function applyIngredientReorder(fromIndex, toIndex) {
  recipeForm.ingredients = moveIngredientRow(recipeForm.ingredients, fromIndex, toIndex)
}

function onIngredientDragStart(idx, event) {
  ingredientDragIndex.value = idx
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(idx))
  }
}

function onIngredientDragEnd() {
  ingredientDragIndex.value = null
}

function onIngredientDrop(idx) {
  if (ingredientDragIndex.value === null) return
  applyIngredientReorder(ingredientDragIndex.value, idx)
  ingredientDragIndex.value = null
}

function moveIngredient(idx, delta) {
  applyIngredientReorder(idx, idx + delta)
}

function addStep() {
  recipeForm.steps = addStepRow(recipeForm.steps)
  focusLastField('[aria-label^="步骤 "]')
}

function removeStep(idx) {
  const removed = recipeForm.steps[idx]
  if (removed === undefined) return
  recipeForm.steps = removeStepRow(recipeForm.steps, idx)
  stepUndo.value = { index: idx, row: removed }
}

function undoStepRemove() {
  const pending = stepUndo.value
  if (!pending) return
  const list = [...recipeForm.steps]
  const insertAt = Math.min(pending.index, list.length)
  list.splice(insertAt, 0, pending.row)
  recipeForm.steps = list
  stepUndo.value = null
}

function applyStepReorder(fromIndex, toIndex) {
  recipeForm.steps = moveStepRow(recipeForm.steps, fromIndex, toIndex)
}

function onStepDragStart(idx, event) {
  stepDragIndex.value = idx
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(idx))
  }
}

function onStepDragEnd() {
  stepDragIndex.value = null
}

function onStepDrop(idx) {
  if (stepDragIndex.value === null) return
  applyStepReorder(stepDragIndex.value, idx)
  stepDragIndex.value = null
}

function moveStep(idx, delta) {
  applyStepReorder(idx, idx + delta)
}

function addTip() {
  recipeForm.tips = addTipRow(recipeForm.tips)
  focusLastField('[aria-label^="小贴士 "]')
}

function removeTip(idx) {
  const removed = recipeForm.tips[idx]
  if (removed === undefined) return
  recipeForm.tips = removeTipRow(recipeForm.tips, idx)
  tipUndo.value = { index: idx, row: removed }
}

function undoTipRemove() {
  const pending = tipUndo.value
  if (!pending) return
  const list = [...recipeForm.tips]
  const insertAt = Math.min(pending.index, list.length)
  list.splice(insertAt, 0, pending.row)
  recipeForm.tips = list
  tipUndo.value = null
}

function onDocumentKey(evt) {
  if (evt.key === 'Escape') {
    evt.stopPropagation()
    if (confirmDialog.open) {
      cancelConfirm()
      return
    }
    requestClose()
    return
  }
  if (confirmDialog.open || submitBusy.value) return
  if ((evt.metaKey || evt.ctrlKey) && String(evt.key).toLowerCase() === 's') {
    evt.preventDefault()
    submitRecipeForm()
  }
}

async function boot() {
  loading.value = true
  errorMsg.value = ''
  try {
    if (props.recipeId) {
      const hist = await api.get(`/api/recipes/recipes/${props.recipeId}/history`)
      applyRecord(props.recipeId, hist.current)
    } else {
      applyBlank()
    }
    baseline.value = snapshotRecipeForm(recipeForm)
  } catch (e) {
    errorMsg.value = e.message || '无法加载配方'
    applyBlank()
    baseline.value = snapshotRecipeForm(recipeForm)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  document.addEventListener('keydown', onDocumentKey, true)
  boot()
})

onBeforeUnmount(() => {
  document.body.style.overflow = previousOverflow
  document.removeEventListener('keydown', onDocumentKey, true)
})
</script>

<template>
  <Teleport to="body">
    <!-- 弹窗：新增/编辑配方 -->
    <div class="print-preview-modal recipe-form-overlay" @click.self="requestClose">
      <div class="print-preview-modal-backdrop" @click="requestClose"></div>
      <div class="recipe-form-modal" role="dialog" aria-modal="true" aria-labelledby="recipe-form-title">
        <header class="recipe-form-chrome">
          <h2 id="recipe-form-title" class="recipe-form-title">{{ titleText }}</h2>
          <div class="recipe-form-chrome-actions">
            <button
              type="button"
              class="btn btn-primary"
              :disabled="submitBusy || loading"
              @click="submitRecipeForm"
            >{{ submitBusy ? '保存中…' : '保存' }}</button>
            <button type="button" class="btn btn-ghost" :disabled="submitBusy" @click="requestClose">取消</button>
          </div>
        </header>
        <p v-if="errorMsg" class="flash flash-error recipe-form-flash">{{ errorMsg }}</p>
        <div class="recipe-form-body">
          <div class="recipe-form-layout">
            <div ref="recipeFormFieldsRef" class="recipe-form-fields">
              <div class="recipe-form-identity">
                <div class="recipe-form-field recipe-form-field--name">
                  <label class="recipe-form-ticket-label" for="recipe-form-name">名称</label>
                  <div class="recipe-form-name-row">
                    <input id="recipe-form-name" class="form-input recipe-form-name-input" v-model="recipeForm.recipe_name" placeholder="艇仔粥">
                    <label class="recipe-form-new-flag">
                      <RecipeCheckbox v-model="recipeForm.is_new" />
                      <span>新品</span>
                    </label>
                  </div>
                </div>
                <div class="recipe-form-meta">
                  <div class="recipe-form-field">
                    <label class="recipe-form-ticket-label" for="recipe-form-section">章节</label>
                    <select id="recipe-form-section" class="form-input" v-model="recipeForm.section">
                      <option v-for="opt in sectionOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                    </select>
                  </div>
                  <div class="recipe-form-field recipe-form-field--servings">
                    <span class="recipe-form-ticket-label" id="recipe-form-servings-label">基准份数</span>
                    <div class="servings-editor" role="group" aria-labelledby="recipe-form-servings-label">
                      <span class="servings-editor-prefix" aria-hidden="true">共</span>
                      <input
                        id="recipe-form-servings-qty"
                        class="form-input"
                        type="number"
                        inputmode="decimal"
                        min="0"
                        step="any"
                        v-model="recipeForm.base_servings_qty"
                        aria-label="基准份数数值"
                        placeholder="—"
                      >
                      <input
                        id="recipe-form-servings-unit"
                        class="form-input"
                        v-model="recipeForm.base_servings_unit"
                        aria-label="基准份数单位"
                        placeholder="人份"
                      >
                    </div>
                  </div>
                </div>
              </div>
              <div v-if="recipeHasLegacyMarkdown(recipeForm)" class="legacy-review">
                <div class="legacy-review-head">
                  <span class="form-label">迁移前原文对比</span>
                  <button
                    v-if="recipeNeedsReview(recipeForm)"
                    type="button"
                    class="btn btn-sm btn-primary"
                    :disabled="confirmReviewBusy"
                    @click="confirmReview"
                  >确认拆分无误</button>
                </div>
                <details class="legacy-review-panel">
                  <summary>{{ recipeNeedsReview(recipeForm) ? '对照迁移原文' : '查看迁移原文' }}</summary>
                  <pre class="legacy-review-pre">{{ recipeForm.legacy_markdown }}</pre>
                </details>
              </div>
              <section class="recipe-form-block">
                <header class="recipe-form-block-head">
                  <h3>用料</h3>
                  <div class="recipe-form-block-actions">
                    <button
                      v-if="ingredientUndo"
                      type="button"
                      class="btn btn-sm btn-ghost"
                      aria-label="撤销删除用料"
                      @click="undoIngredientRemove"
                    >撤销删除</button>
                    <button
                      v-if="recipeForm.ingredients.length"
                      type="button"
                      class="btn btn-sm btn-ghost"
                      aria-label="添加用料"
                      @click="addIngredient"
                    >添加</button>
                  </div>
                </header>
                <button
                  v-if="!recipeForm.ingredients.length"
                  type="button"
                  class="recipe-form-empty"
                  @click="addIngredient"
                >写下第一味用料</button>
                <div v-else class="ingredient-editor-wrap">
                  <table class="ingredient-editor">
                    <thead>
                      <tr>
                        <th class="ingredient-editor-handle"></th>
                        <th>名称</th>
                        <th>用量</th>
                        <th>单位</th>
                        <th class="ingredient-editor-actions"></th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(row, idx) in recipeForm.ingredients"
                        :key="idx"
                        class="ingredient-editor-row"
                        @dragover.prevent
                        @drop.prevent="onIngredientDrop(idx)"
                      >
                        <td>
                          <div class="reorder-controls">
                            <button
                              type="button"
                              class="drag-handle"
                              draggable="true"
                              aria-label="拖拽排序用料"
                              @dragstart="onIngredientDragStart(idx, $event)"
                              @dragend="onIngredientDragEnd"
                            >⋮⋮</button>
                            <button
                              type="button"
                              class="btn btn-sm btn-ghost"
                              aria-label="上移用料"
                              :disabled="idx === 0"
                              @click="moveIngredient(idx, -1)"
                            >上移</button>
                            <button
                              type="button"
                              class="btn btn-sm btn-ghost"
                              aria-label="下移用料"
                              :disabled="idx === recipeForm.ingredients.length - 1"
                              @click="moveIngredient(idx, 1)"
                            >下移</button>
                          </div>
                        </td>
                        <td>
                          <input
                            class="form-input"
                            v-model="row.name"
                            :maxlength="INGREDIENT_FIELD_MAX_LEN"
                            aria-label="用料名称"
                          >
                        </td>
                        <td>
                          <input
                            class="form-input"
                            v-model="row.amount"
                            :maxlength="INGREDIENT_FIELD_MAX_LEN"
                            aria-label="用料用量"
                          >
                        </td>
                        <td>
                          <input
                            class="form-input"
                            list="recipe-scale-units"
                            v-model="row.unit"
                            :maxlength="INGREDIENT_FIELD_MAX_LEN"
                            aria-label="用料单位"
                          >
                        </td>
                        <td>
                          <button
                            type="button"
                            class="btn btn-sm btn-danger"
                            aria-label="删除用料"
                            @click="removeIngredient(idx)"
                          >删除</button>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <datalist id="recipe-scale-units">
                  <option v-for="unit in SCALE_UNITS" :key="unit" :value="unit" />
                </datalist>
              </section>
              <section class="recipe-form-block">
                <header class="recipe-form-block-head">
                  <h3>步骤</h3>
                  <div class="recipe-form-block-actions">
                    <button
                      v-if="stepUndo"
                      type="button"
                      class="btn btn-sm btn-ghost"
                      aria-label="撤销删除步骤"
                      @click="undoStepRemove"
                    >撤销删除</button>
                    <button
                      v-if="recipeForm.steps.length"
                      type="button"
                      class="btn btn-sm btn-ghost"
                      aria-label="添加步骤"
                      @click="addStep"
                    >添加</button>
                  </div>
                </header>
                <button
                  v-if="!recipeForm.steps.length"
                  type="button"
                  class="recipe-form-empty"
                  @click="addStep"
                >写下第一步</button>
                <div v-else class="step-editor-wrap">
                  <ol class="step-editor">
                    <li
                      v-for="(_step, idx) in recipeForm.steps"
                      :key="idx"
                      class="step-editor-row"
                      @dragover.prevent
                      @drop.prevent="onStepDrop(idx)"
                    >
                      <div class="reorder-controls">
                        <button
                          type="button"
                          class="drag-handle"
                          draggable="true"
                          aria-label="拖拽排序步骤"
                          @dragstart="onStepDragStart(idx, $event)"
                          @dragend="onStepDragEnd"
                        >⋮⋮</button>
                        <button
                          type="button"
                          class="btn btn-sm btn-ghost"
                          aria-label="上移步骤"
                          :disabled="idx === 0"
                          @click="moveStep(idx, -1)"
                        >上移</button>
                        <button
                          type="button"
                          class="btn btn-sm btn-ghost"
                          aria-label="下移步骤"
                          :disabled="idx === recipeForm.steps.length - 1"
                          @click="moveStep(idx, 1)"
                        >下移</button>
                      </div>
                      <span class="step-editor-num" aria-hidden="true">{{ idx + 1 }}</span>
                      <input
                        class="form-input"
                        v-model="recipeForm.steps[idx]"
                        :maxlength="STEP_TEXT_MAX_LEN"
                        :aria-label="'步骤 ' + (idx + 1)"
                      >
                      <button
                        type="button"
                        class="btn btn-sm btn-danger"
                        aria-label="删除步骤"
                        @click="removeStep(idx)"
                      >删除</button>
                    </li>
                  </ol>
                </div>
              </section>
              <section class="recipe-form-block">
                <header class="recipe-form-block-head">
                  <h3>小贴士</h3>
                  <div class="recipe-form-block-actions">
                    <button
                      v-if="tipUndo"
                      type="button"
                      class="btn btn-sm btn-ghost"
                      aria-label="撤销删除小贴士"
                      @click="undoTipRemove"
                    >撤销删除</button>
                    <button
                      v-if="recipeForm.tips.length"
                      type="button"
                      class="btn btn-sm btn-ghost"
                      aria-label="添加小贴士"
                      @click="addTip"
                    >添加</button>
                  </div>
                </header>
                <button
                  v-if="!recipeForm.tips.length"
                  type="button"
                  class="recipe-form-empty"
                  @click="addTip"
                >补一条小贴士</button>
                <div v-else class="step-editor-wrap">
                  <ul class="step-editor">
                    <li
                      v-for="(_tip, idx) in recipeForm.tips"
                      :key="idx"
                      class="step-editor-row"
                    >
                      <input
                        class="form-input"
                        v-model="recipeForm.tips[idx]"
                        :maxlength="TIP_TEXT_MAX_LEN"
                        :aria-label="'小贴士 ' + (idx + 1)"
                      >
                      <button
                        type="button"
                        class="btn btn-sm btn-danger"
                        aria-label="删除小贴士"
                        @click="removeTip(idx)"
                      >删除</button>
                    </li>
                  </ul>
                </div>
              </section>
              <details v-if="recipeFormHasBody" class="recipe-form-source">
                <summary>原文（阅读页有用料、步骤或小贴士时不显示）</summary>
                <textarea class="form-input recipe-form-source-input" rows="5" v-model="recipeForm.body"></textarea>
              </details>
            </div>
            <aside class="recipe-form-preview markdown-body" aria-label="档口预览">
              <h3 class="recipe-form-visually-hidden">档口预览</h3>
              <article class="recipe-card" :class="{ 'recipe-card--new': recipeForm.is_new }">
                <header class="recipe-card-head">
                  <h3 class="recipe-title">{{ recipeForm.recipe_name || '未命名' }}</h3>
                </header>
                <div v-if="structuredPreviewHtml" class="recipe-card-body" v-html="structuredPreviewHtml"></div>
                <p v-else class="recipe-form-preview-empty">用料和步骤写好后，这张卡会贴到档口。</p>
              </article>
            </aside>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="confirmDialog.open"
      class="print-preview-modal confirm-dialog"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="recipe-form-discard-title"
      aria-describedby="recipe-form-discard-body"
      @click.self="cancelConfirm"
    >
      <div class="print-preview-modal-backdrop" @click="cancelConfirm"></div>
      <div class="sop-panel confirm-dialog-panel">
        <h2 id="recipe-form-discard-title" class="page-title" style="font-size:1.2rem">{{ confirmDialog.title }}</h2>
        <p id="recipe-form-discard-body" class="page-lead">{{ confirmDialog.body }}</p>
        <div class="row-actions" style="margin-top:16px">
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
  </Teleport>
</template>

<style scoped>
.recipe-form-modal {
  position: relative;
  display: flex;
  flex-direction: column;
  max-width: 76rem;
  width: min(96vw, 76rem);
  height: min(92vh, 56rem);
  max-height: min(94vh, 58rem);
  overflow: hidden;
  padding: 0;
  z-index: 1;
  container-type: inline-size;
  container-name: recipe-form;
}
.reorder-controls {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.drag-handle {
  cursor: grab;
  border: 1px solid var(--line);
  background: var(--surface-2);
  color: var(--muted);
  border-radius: 4px;
  padding: 0.15rem 0.4rem;
  letter-spacing: -0.1em;
  line-height: 1.2;
}
.drag-handle:active {
  cursor: grabbing;
}
.legacy-review {
  margin: 1rem 0 0;
  padding: 0.75rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}
.legacy-review-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
}
.legacy-review-head .form-label {
  margin: 0;
}
.legacy-review-panel summary {
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--muted);
}
.legacy-review-pre {
  margin: 0.5rem 0 0;
  padding: 0.6rem 0.75rem;
  max-height: 16rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono);
  font-size: 0.82rem;
  line-height: 1.45;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 6px;
}
.confirm-dialog {
  z-index: 2100;
}
.confirm-dialog-panel {
  position: relative;
  max-width: 28rem;
  width: 92%;
  padding: 24px;
  z-index: 1;
}
</style>
