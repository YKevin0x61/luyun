<script setup>
import { computed, reactive, ref } from 'vue'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import RecipeNavIcon from './RecipeNavIcon.vue'
import RecipeCheckbox from '../../components/recipe/RecipeCheckbox.vue'
import RecipeFileDropzone from '../../components/recipe/RecipeFileDropzone.vue'
import {
  NEW_SECTION_VALUE,
  assignSortOrders,
  buildSectionOptions,
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

useScopedStylesheet('/recipe.css')

const INGREDIENT_FIELD_MAX_LEN = 120
const STEP_TEXT_MAX_LEN = 120
const TIP_TEXT_MAX_LEN = 120

const view = ref('stations') // 'stations' | 'recipes'
const stations = ref([])
const currentSlug = ref('')
const currentTitle = ref('')
const recipes = ref([])
const csvDropzoneRef = ref(null)
const errorMsg = ref('')
const dragIndex = ref(null)
const reorderBusy = ref(false)
const ingredientDragIndex = ref(null)
const ingredientUndo = ref(null)
const stepDragIndex = ref(null)
const stepUndo = ref(null)
const tipUndo = ref(null)

// 各类弹窗状态（复用一个 modal 容器，按 kind 渲染不同表单）
const modal = reactive({ kind: null }) // 'add-station' | 'rename-station' | 'recipe-form' | 'history'
const stationForm = reactive({ slug: '', title: '' })
const renameForm = reactive({ slug: '', title: '' })
const recipeForm = reactive({
  id: null, section: '配方', recipe_name: '', body: '', sort_order: null, is_new: false,
  ingredients: [], steps: [], tips: [],
  base_servings_qty: '', base_servings_unit: '',
})
const historyItems = ref([])

const structuredPreviewHtml = computed(() => renderStructuredRecipePreviewHtml(
  recipeForm.ingredients.map((row) => ({
    name: row.name,
    amount: row.amount,
    unit: row.unit,
  })),
  recipeForm.steps,
  recipeForm.tips,
))

const sectionOptions = computed(() => buildSectionOptions(recipes.value))
const existingSections = computed(() =>
  sectionOptions.value.filter((opt) => opt.value !== NEW_SECTION_VALUE).map((opt) => opt.value),
)
const sectionSelectValue = computed({
  get() {
    return existingSections.value.includes(recipeForm.section)
      ? recipeForm.section
      : NEW_SECTION_VALUE
  },
  set(value) {
    if (value === NEW_SECTION_VALUE) {
      if (existingSections.value.includes(recipeForm.section)) recipeForm.section = ''
    } else {
      recipeForm.section = value
    }
  },
})

function closeModal() {
  if (modal.kind === 'import-csv') csvDropzoneRef.value?.reset()
  modal.kind = null
  errorMsg.value = ''
  ingredientUndo.value = null
  stepUndo.value = null
  tipUndo.value = null
}

async function loadStations() {
  view.value = 'stations'
  try {
    const data = await api.get('/api/recipes/stations')
    stations.value = data.stations || []
    errorMsg.value = ''
  } catch (e) {
    errorMsg.value = e.message || '加载失败'
  }
}

async function openStation(slug) {
  try {
    currentSlug.value = slug
    const data = await api.get('/api/recipes/stations')
    const st = (data.stations || []).find((s) => s.slug === slug)
    currentTitle.value = st ? st.title : slug
    view.value = 'recipes'
    await refreshRecipes()
  } catch (e) {
    window.alert(e.message || '加载失败')
  }
}

async function refreshRecipes() {
  try {
    const data = await api.get(`/api/recipes/stations/${encodeURIComponent(currentSlug.value)}/recipes`)
    recipes.value = data.recipes || []
  } catch (e) {
    window.alert(e.message || '加载失败')
  }
}

function openAddStation() {
  stationForm.slug = ''
  stationForm.title = ''
  modal.kind = 'add-station'
}
async function submitAddStation() {
  errorMsg.value = ''
  try {
    await api.post('/api/recipes/stations', { slug: stationForm.slug, title: stationForm.title })
    closeModal()
    await loadStations()
  } catch (e) {
    errorMsg.value = e.message
  }
}

function openRename(slug, title) {
  renameForm.slug = slug
  renameForm.title = title
  modal.kind = 'rename-station'
}
async function submitRename() {
  errorMsg.value = ''
  try {
    await api.post(`/api/recipes/stations/${encodeURIComponent(renameForm.slug)}/rename`, { title: renameForm.title })
    closeModal()
    await loadStations()
  } catch (e) {
    errorMsg.value = e.message
  }
}

async function deleteStation(slug) {
  if (!window.confirm('确认删除该岗位及其全部条目？')) return
  await api.delete(`/api/recipes/stations/${encodeURIComponent(slug)}`)
  await loadStations()
}

function openAddRecipe() {
  recipeForm.id = null
  recipeForm.section = '配方'
  recipeForm.recipe_name = ''
  recipeForm.body = ''
  recipeForm.sort_order = null
  recipeForm.is_new = false
  recipeForm.ingredients = []
  recipeForm.steps = []
  recipeForm.tips = []
  recipeForm.base_servings_qty = ''
  recipeForm.base_servings_unit = ''
  ingredientUndo.value = null
  stepUndo.value = null
  tipUndo.value = null
  modal.kind = 'recipe-form'
}
async function openEditRecipe(id) {
  const hist = await api.get(`/api/recipes/recipes/${id}/history`)
  const r = hist.current
  recipeForm.id = id
  recipeForm.section = r.section
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
  ingredientUndo.value = null
  stepUndo.value = null
  tipUndo.value = null
  modal.kind = 'recipe-form'
}
function servingsQtyPayload(raw) {
  if (raw === '' || raw == null) return 0
  const qty = Number(raw)
  if (!Number.isFinite(qty)) return 0
  return qty
}

async function submitRecipeForm() {
  errorMsg.value = ''
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
      await api.post(`/api/recipes/stations/${encodeURIComponent(currentSlug.value)}/recipes`, payload)
    }
    closeModal()
    await refreshRecipes()
  } catch (e) {
    errorMsg.value = e.message
  }
}

async function persistRecipeOrder(ordered) {
  const assignments = assignSortOrders(ordered)
  reorderBusy.value = true
  try {
    await api.put(
      `/api/recipes/stations/${encodeURIComponent(currentSlug.value)}/recipes/reorder`,
      { ids: assignments.map((row) => row.id) },
    )
    recipes.value = ordered.map((row, index) => ({
      ...row,
      sort_order: assignments[index].sort_order,
    }))
  } catch (e) {
    window.alert(e.message || '排序保存失败')
    await refreshRecipes()
  } finally {
    reorderBusy.value = false
  }
}

function applyRecipeReorder(fromIndex, toIndex) {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) return
  if (toIndex >= recipes.value.length) return
  const list = [...recipes.value]
  const [moved] = list.splice(fromIndex, 1)
  list.splice(toIndex, 0, moved)
  recipes.value = list
  persistRecipeOrder(list)
}

function onDragStart(idx, event) {
  dragIndex.value = idx
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(idx))
  }
}

function onDragEnd() {
  dragIndex.value = null
}

function onDrop(idx) {
  if (dragIndex.value === null) return
  applyRecipeReorder(dragIndex.value, idx)
  dragIndex.value = null
}

function moveRecipe(idx, delta) {
  applyRecipeReorder(idx, idx + delta)
}

function addIngredient() {
  recipeForm.ingredients = addIngredientRow(recipeForm.ingredients)
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

async function toggleActive(id) {
  await api.post(`/api/recipes/recipes/${id}/toggle-active`, {})
  await refreshRecipes()
}

async function deleteRecipe(id) {
  if (!window.confirm('确认删除该条目？')) return
  await api.delete(`/api/recipes/recipes/${id}`)
  await refreshRecipes()
}

async function openHistory(id) {
  const h = await api.get(`/api/recipes/recipes/${id}/history`)
  historyItems.value = h.history || []
  modal.kind = 'history'
}

function openImportCsv() {
  modal.kind = 'import-csv'
}
async function onCsvSelected(file) {
  if (!file) return
  const fd = new FormData()
  fd.append('csv_file', file)
  try {
    const r = await api.upload(`/api/recipes/stations/${encodeURIComponent(currentSlug.value)}/import`, fd)
    window.alert(`成功导入 ${r.imported} 条`)
    closeModal()
    csvDropzoneRef.value?.reset()
    await refreshRecipes()
  } catch (e) {
    window.alert(e.message)
  }
}

loadStations()
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
      </div>
    </header>

    <main class="site-main">
      <section v-if="view === 'stations'" class="manage-section">
        <header class="manage-page-head">
          <h1 class="page-title">配方管理</h1>
          <p class="page-lead">管理岗位与条目；浏览与打印从岗位列表进入。</p>
          <div class="manage-actions">
            <button class="btn btn-primary" @click="openAddStation">新增岗位</button>
            <router-link class="btn btn-ghost" to="/recipe">返回岗位列表</router-link>
          </div>
        </header>
        <p v-if="errorMsg" class="flash flash-error" style="margin-bottom:12px">{{ errorMsg }}</p>
        <div class="manage-table-wrap">
          <table class="manage-table">
            <thead><tr><th>岗位标题</th><th>标识</th><th>条目数</th><th class="manage-table-actions">操作</th></tr></thead>
            <tbody>
              <tr v-for="s in stations" :key="s.slug">
                <td>{{ s.title }}</td>
                <td><code class="slug-code">{{ s.slug }}</code></td>
                <td>{{ s.recipe_count }}</td>
                <td class="manage-table-actions">
                  <div class="row-actions">
                    <button class="btn btn-sm btn-ghost" @click="openStation(s.slug)">管理条目</button>
                    <router-link class="btn btn-sm btn-ghost" :to="`/recipe/detail?slug=${encodeURIComponent(s.slug)}`">查看</router-link>
                    <button class="btn btn-sm btn-ghost" @click="openRename(s.slug, s.title)">改名</button>
                    <button class="btn btn-sm btn-danger" @click="deleteStation(s.slug)">删除</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-else class="manage-section">
        <header class="manage-page-head">
          <h1 class="page-title">{{ currentTitle }}</h1>
          <div class="manage-actions">
            <button class="btn btn-primary" @click="openAddRecipe">＋ 新增条目</button>
            <router-link class="btn btn-ghost" :to="`/recipe/detail?slug=${encodeURIComponent(currentSlug)}`">◉ 预览岗位页</router-link>
            <a class="btn btn-ghost" :href="`/api/recipes/stations/${encodeURIComponent(currentSlug)}/export`">⇩ 导出 CSV</a>
            <button class="btn btn-ghost" @click="openImportCsv">⇧ 导入 CSV</button>
            <a class="btn btn-ghost" :href="`/api/recipes/stations/${encodeURIComponent(currentSlug)}/docx`">⇩ 导出 Word</a>
            <button class="btn btn-ghost" @click="loadStations">返回管理首页</button>
          </div>
        </header>
        <div class="manage-table-wrap">
          <table class="manage-table">
            <thead><tr><th>排序</th><th>章节</th><th>条目名称</th><th>新品</th><th>状态</th><th class="manage-table-actions">操作</th></tr></thead>
            <tbody>
              <tr
                v-for="(r, idx) in recipes"
                :key="r.id"
                :class="{ 'is-inactive': !r.is_active }"
                @dragover.prevent
                @drop.prevent="onDrop(idx)"
              >
                <td>
                  <div class="reorder-controls">
                    <button
                      type="button"
                      class="drag-handle"
                      draggable="true"
                      aria-label="拖拽排序"
                      :disabled="reorderBusy"
                      @dragstart="onDragStart(idx, $event)"
                      @dragend="onDragEnd"
                    >⋮⋮</button>
                    <button
                      type="button"
                      class="btn btn-sm btn-ghost"
                      aria-label="上移"
                      :disabled="idx === 0 || reorderBusy"
                      @click="moveRecipe(idx, -1)"
                    >上移</button>
                    <button
                      type="button"
                      class="btn btn-sm btn-ghost"
                      aria-label="下移"
                      :disabled="idx === recipes.length - 1 || reorderBusy"
                      @click="moveRecipe(idx, 1)"
                    >下移</button>
                  </div>
                </td>
                <td>{{ r.section }}</td>
                <td>{{ r.recipe_name }}</td>
                <td><span v-if="r.is_new" class="badge-new">新</span><span v-else class="muted">—</span></td>
                <td>
                  <span :class="r.is_active ? 'badge-status badge-status--on' : 'badge-status badge-status--off'">
                    {{ r.is_active ? '启用' : '停用' }}
                  </span>
                </td>
                <td class="manage-table-actions">
                  <div class="row-actions">
                    <button class="btn btn-sm btn-ghost" @click="openEditRecipe(r.id)">编辑</button>
                    <button class="btn btn-sm btn-ghost" @click="openHistory(r.id)">历史</button>
                    <button class="btn btn-sm btn-ghost" @click="toggleActive(r.id)">{{ r.is_active ? '停用' : '启用' }}</button>
                    <button class="btn btn-sm btn-danger" @click="deleteRecipe(r.id)">删除</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>

    <!-- 弹窗：新增岗位 -->
    <div v-if="modal.kind === 'add-station'" class="print-preview-modal" @click.self="closeModal">
      <div class="print-preview-modal-backdrop" @click="closeModal"></div>
      <div class="sop-panel" style="position:relative;max-width:640px;width:92%;max-height:86vh;overflow:auto;padding:24px;z-index:1">
        <h2 class="page-title" style="font-size:1.2rem">新增岗位</h2>
        <label class="form-label">标识（作 URL，建议拼音/英文）</label>
        <input class="form-input" v-model="stationForm.slug">
        <label class="form-label">显示名称</label>
        <input class="form-input" v-model="stationForm.title">
        <div class="row-actions" style="margin-top:16px">
          <button class="btn btn-primary" @click="submitAddStation">创建</button>
          <button class="btn btn-ghost" @click="closeModal">取消</button>
        </div>
        <p v-if="errorMsg" class="flash flash-error" style="margin-top:12px">{{ errorMsg }}</p>
      </div>
    </div>

    <!-- 弹窗：重命名岗位 -->
    <div v-if="modal.kind === 'rename-station'" class="print-preview-modal" @click.self="closeModal">
      <div class="print-preview-modal-backdrop" @click="closeModal"></div>
      <div class="sop-panel" style="position:relative;max-width:640px;width:92%;max-height:86vh;overflow:auto;padding:24px;z-index:1">
        <h2 class="page-title" style="font-size:1.2rem">重命名岗位</h2>
        <input class="form-input" v-model="renameForm.title">
        <div class="row-actions" style="margin-top:16px">
          <button class="btn btn-primary" @click="submitRename">保存</button>
          <button class="btn btn-ghost" @click="closeModal">取消</button>
        </div>
        <p v-if="errorMsg" class="flash flash-error" style="margin-top:12px">{{ errorMsg }}</p>
      </div>
    </div>

    <!-- 弹窗：新增/编辑条目 -->
    <div v-if="modal.kind === 'recipe-form'" class="print-preview-modal" @click.self="closeModal">
      <div class="print-preview-modal-backdrop" @click="closeModal"></div>
      <div class="sop-panel recipe-form-modal">
        <h2 class="page-title" style="font-size:1.2rem">{{ recipeForm.id ? '编辑条目' : '新增条目' }}</h2>
        <div class="recipe-form-layout">
          <div class="recipe-form-fields">
            <label class="form-label">章节</label>
            <select class="form-input" v-model="sectionSelectValue">
              <option v-for="opt in sectionOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </select>
            <input
              v-if="sectionSelectValue === NEW_SECTION_VALUE"
              class="form-input new-section-input"
              v-model="recipeForm.section"
              aria-label="新章节名"
              placeholder="输入新章节名"
            >
            <label class="form-label">条目名称</label>
            <input class="form-input" v-model="recipeForm.recipe_name">
            <label class="form-check"><RecipeCheckbox v-model="recipeForm.is_new" /><span>标记为新品</span></label>
            <label class="form-label">基准份数</label>
            <div class="servings-editor">
              <input
                class="form-input"
                type="number"
                inputmode="decimal"
                min="0"
                step="any"
                v-model="recipeForm.base_servings_qty"
                aria-label="基准份数数值"
                placeholder="数值"
              >
              <input
                class="form-input"
                v-model="recipeForm.base_servings_unit"
                aria-label="基准份数单位"
                placeholder="人份"
              >
            </div>
            <label class="form-label">用料</label>
            <div v-if="recipeForm.ingredients.length" class="ingredient-editor-wrap">
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
            <div class="row-actions ingredient-editor-toolbar">
              <button type="button" class="btn btn-sm btn-ghost" aria-label="添加用料" @click="addIngredient">添加用料</button>
              <button
                v-if="ingredientUndo"
                type="button"
                class="btn btn-sm btn-ghost"
                aria-label="撤销删除用料"
                @click="undoIngredientRemove"
              >撤销删除</button>
            </div>
            <label class="form-label">步骤</label>
            <div v-if="recipeForm.steps.length" class="step-editor-wrap">
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
            <div class="row-actions ingredient-editor-toolbar">
              <button type="button" class="btn btn-sm btn-ghost" aria-label="添加步骤" @click="addStep">添加步骤</button>
              <button
                v-if="stepUndo"
                type="button"
                class="btn btn-sm btn-ghost"
                aria-label="撤销删除步骤"
                @click="undoStepRemove"
              >撤销删除</button>
            </div>
            <label class="form-label">小贴士</label>
            <div v-if="recipeForm.tips.length" class="step-editor-wrap">
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
            <div class="row-actions ingredient-editor-toolbar">
              <button type="button" class="btn btn-sm btn-ghost" aria-label="添加小贴士" @click="addTip">添加小贴士</button>
              <button
                v-if="tipUndo"
                type="button"
                class="btn btn-sm btn-ghost"
                aria-label="撤销删除小贴士"
                @click="undoTipRemove"
              >撤销删除</button>
            </div>
            <label class="form-label">正文（Markdown）</label>
            <textarea class="form-input" rows="6" v-model="recipeForm.body"></textarea>
          </div>
          <div class="recipe-form-preview markdown-body">
            <p class="form-label">预览</p>
            <article class="recipe-card">
              <header class="recipe-card-head">
                <h3 class="recipe-title">{{ recipeForm.recipe_name || '未命名' }}</h3>
              </header>
              <div class="recipe-card-body" v-html="structuredPreviewHtml"></div>
            </article>
          </div>
        </div>
        <div class="row-actions" style="margin-top:16px">
          <button class="btn btn-primary" @click="submitRecipeForm">保存</button>
          <button class="btn btn-ghost" @click="closeModal">取消</button>
        </div>
        <p v-if="errorMsg" class="flash flash-error" style="margin-top:12px">{{ errorMsg }}</p>
      </div>
    </div>

    <!-- 弹窗：导入 CSV -->
    <div v-if="modal.kind === 'import-csv'" class="print-preview-modal" @click.self="closeModal">
      <div class="print-preview-modal-backdrop" @click="closeModal"></div>
      <div class="sop-panel" style="position:relative;max-width:560px;width:92%;max-height:86vh;overflow:auto;padding:24px;z-index:1">
        <h2 class="page-title" style="font-size:1.2rem">导入 CSV</h2>
        <p class="form-hint" style="margin-bottom:12px">导入到当前岗位「{{ currentTitle }}」，重复条目按服务端规则处理。</p>
        <RecipeFileDropzone
          ref="csvDropzoneRef"
          accept=".csv,text/csv"
          label="拖拽 CSV 到此处，或点击选择"
          hint="建议使用本页导出的 CSV 格式"
          @change="onCsvSelected"
        />
        <div class="row-actions" style="margin-top:16px">
          <button class="btn btn-ghost" @click="closeModal">取消</button>
        </div>
      </div>
    </div>

    <!-- 弹窗：历史 -->
    <div v-if="modal.kind === 'history'" class="print-preview-modal" @click.self="closeModal">
      <div class="print-preview-modal-backdrop" @click="closeModal"></div>
      <div class="sop-panel" style="position:relative;max-width:640px;width:92%;max-height:86vh;overflow:auto;padding:24px;z-index:1">
        <h2 class="page-title" style="font-size:1.2rem">修改历史</h2>
        <ul class="station-list">
          <li v-if="!historyItems.length" class="station-item muted">暂无历史</li>
          <li v-for="(x, i) in historyItems" :key="i" class="station-item" style="display:block">
            <strong>{{ x.changed_at }}</strong><br>
            {{ x.recipe_name }} · {{ x.section }}
            <pre style="white-space:pre-wrap">{{ x.body_markdown }}</pre>
          </li>
        </ul>
        <div class="row-actions" style="margin-top:16px">
          <button class="btn btn-ghost" @click="closeModal">关闭</button>
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
.reorder-controls {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.new-section-input {
  margin-top: 0.35rem;
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
.drag-handle:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.recipe-form-modal {
  position: relative;
  max-width: 960px;
  width: 94%;
  max-height: 86vh;
  overflow: auto;
  padding: 24px;
  z-index: 1;
}
.ingredient-editor-toolbar {
  justify-content: flex-start;
  margin: 0.5rem 0 0;
}
.servings-editor {
  display: flex;
  gap: 0.5rem;
}
.servings-editor .form-input {
  flex: 1;
}
</style>
