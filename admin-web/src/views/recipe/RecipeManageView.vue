<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import RecipeNavIcon from './RecipeNavIcon.vue'
import RecipeFileDropzone from '../../components/recipe/RecipeFileDropzone.vue'
import RecipeFormModal from '../../components/recipe/RecipeFormModal.vue'
import { assignSortOrders } from '../../utils/recipeManageOrder'
import { filterRecipesByReview } from '../../utils/recipeReview'
import {
  deleteRecipeConfirmCopy,
  deleteStationConfirmCopy,
  restoreHistoryCopy,
} from '../../utils/recipeConfirmCopy'
import {
  RECIPE_BRAND_MARK,
  RECIPE_BRAND_TAGLINE,
  RECIPE_BRAND_TITLE,
  RECIPE_NAV_HOME_LABEL,
  RECIPE_NAV_MANAGE_LABEL,
  RECIPE_NAV_STATIONS_LABEL,
  recipeDocumentTitle,
} from '../../utils/recipeCopy'

useScopedStylesheet('/recipe.css')

const route = useRoute()

const view = ref('stations') // 'stations' | 'recipes'
const stations = ref([])
const currentSlug = ref('')
const currentTitle = ref('')
const recipes = ref([])
const reviewOnly = ref(false)
const csvDropzoneRef = ref(null)
const errorMsg = ref('')
const dragIndex = ref(null)
const reorderBusy = ref(false)
const formRecipeId = ref(null)

// 各类弹窗状态（复用一个 modal 容器，按 kind 渲染不同表单）
const modal = reactive({ kind: null }) // 'add-station' | 'rename-station' | 'recipe-form' | 'history'
const stationForm = reactive({ slug: '', title: '' })
const renameForm = reactive({ slug: '', title: '' })
const historyItems = ref([])
const historyRecipeId = ref(null)
const confirmDialog = reactive({
  open: false,
  title: '',
  body: '',
  confirmLabel: '确认',
  busy: false,
})
let confirmAction = null

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
  errorMsg.value = ''
  try {
    await confirmAction?.()
    confirmDialog.open = false
    confirmAction = null
  } catch (e) {
    errorMsg.value = e.message || '操作失败'
  } finally {
    confirmDialog.busy = false
  }
}
const visibleRecipes = computed(() => filterRecipesByReview(recipes.value, reviewOnly.value))
const reviewCount = computed(() => filterRecipesByReview(recipes.value, true).length)

function forceCloseModal() {
  if (modal.kind === 'import-csv') csvDropzoneRef.value?.reset()
  modal.kind = null
  formRecipeId.value = null
  errorMsg.value = ''
  historyRecipeId.value = null
}

function closeModal() {
  forceCloseModal()
}

async function loadStations() {
  view.value = 'stations'
  document.title = recipeDocumentTitle('配方管理')
  try {
    const data = await api.get('/api/recipes/stations')
    stations.value = data.stations || []
    errorMsg.value = ''
  } catch (e) {
    errorMsg.value = e.message || '无法加载岗位列表，请稍后重试'
  }
}

async function openStation(slug) {
  try {
    currentSlug.value = slug
    reviewOnly.value = false
    const data = await api.get('/api/recipes/stations')
    const st = (data.stations || []).find((s) => s.slug === slug)
    currentTitle.value = st ? st.title : slug
    document.title = recipeDocumentTitle(currentTitle.value)
    view.value = 'recipes'
    await refreshRecipes()
  } catch (e) {
    window.alert(e.message || '无法加载该岗位配方，请稍后重试')
  }
}

async function refreshRecipes() {
  try {
    const data = await api.get(`/api/recipes/stations/${encodeURIComponent(currentSlug.value)}/recipes`)
    recipes.value = data.recipes || []
  } catch (e) {
    window.alert(e.message || '无法加载配方列表，请稍后重试')
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
    forceCloseModal()
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
    forceCloseModal()
    await loadStations()
  } catch (e) {
    errorMsg.value = e.message
  }
}

function deleteStation(station) {
  askConfirm(
    deleteStationConfirmCopy({ title: station.title, recipeCount: station.recipe_count }),
    async () => {
      await api.delete(`/api/recipes/stations/${encodeURIComponent(station.slug)}`)
      await loadStations()
    },
  )
}

function openAddRecipe() {
  formRecipeId.value = null
  modal.kind = 'recipe-form'
}

function openEditRecipe(id) {
  formRecipeId.value = Number(id)
  modal.kind = 'recipe-form'
}

async function onRecipeFormSaved() {
  forceCloseModal()
  await refreshRecipes()
}

async function onRecipeFormReviewConfirmed() {
  await refreshRecipes()
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

async function toggleActive(id) {
  await api.post(`/api/recipes/recipes/${id}/toggle-active`, {})
  await refreshRecipes()
}

function deleteRecipe(recipe) {
  askConfirm(deleteRecipeConfirmCopy({ recipeName: recipe.recipe_name }), async () => {
    await api.delete(`/api/recipes/recipes/${recipe.id}`)
    await refreshRecipes()
  })
}

async function openHistory(id) {
  historyRecipeId.value = id
  const h = await api.get(`/api/recipes/recipes/${id}/history`)
  historyItems.value = h.history || []
  modal.kind = 'history'
}

function historySummary(item) {
  const nIng = (item.ingredients || []).length
  const nStep = (item.steps || []).length
  const nTip = (item.tips || []).length
  return `用料 ${nIng} · 步骤 ${nStep} · 小贴士 ${nTip}`
}

function restoreHistoryItem(item) {
  const recipeId = historyRecipeId.value
  if (!recipeId) return
  askConfirm(
    restoreHistoryCopy({ recipeName: item.recipe_name, changedAt: item.changed_at }),
    async () => {
      await api.post(`/api/recipes/recipes/${recipeId}/history/${item.id}/restore`)
      await refreshRecipes()
      const h = await api.get(`/api/recipes/recipes/${recipeId}/history`)
      historyItems.value = h.history || []
    },
  )
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
    window.alert(`成功导入 ${r.imported} 条配方`)
    forceCloseModal()
    csvDropzoneRef.value?.reset()
    await refreshRecipes()
  } catch (e) {
    window.alert(e.message)
  }
}

async function applyManageQuery() {
  const slug = String(Array.isArray(route.query.slug) ? route.query.slug[0] : route.query.slug || '').trim()
  if (!slug) {
    await loadStations()
    return
  }
  await openStation(slug)
  const raw = Array.isArray(route.query.edit) ? route.query.edit[0] : route.query.edit
  const edit = String(raw == null ? '' : raw).trim()
  if (!/^\d+$/.test(edit)) return
  try {
    await openEditRecipe(Number(edit))
  } catch (e) {
    window.alert(e.message || '无法打开该配方')
  }
}

watch(
  () => `${route.query.slug || ''}|${route.query.edit || ''}`,
  () => { applyManageQuery() },
)

applyManageQuery()
</script>

<template>
  <div>
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
      </div>
    </header>

    <main class="site-main">
      <section v-if="view === 'stations'" class="manage-section">
        <header class="manage-page-head">
          <h1 class="page-title">配方管理</h1>
          <p class="page-lead">新增、改名或删除岗位和配方。阅读和打印从岗位列表进入。</p>
          <div class="manage-actions">
            <button class="btn btn-primary" @click="openAddStation">新增岗位</button>
            <router-link class="btn btn-ghost" to="/recipe">返回岗位列表</router-link>
          </div>
        </header>
        <p v-if="errorMsg" class="flash flash-error" style="margin-bottom:12px">{{ errorMsg }}</p>
        <div class="manage-table-wrap">
          <table class="manage-table">
            <thead><tr><th>岗位名称</th><th>标识</th><th>配方数</th><th class="manage-table-actions">操作</th></tr></thead>
            <tbody>
              <tr v-for="s in stations" :key="s.slug">
                <td data-label="岗位名称">{{ s.title }}</td>
                <td data-label="标识"><code class="slug-code">{{ s.slug }}</code></td>
                <td data-label="配方数">{{ s.recipe_count }}</td>
                <td class="manage-table-actions" data-label="操作">
                  <div class="row-actions">
                    <button class="btn btn-sm btn-ghost" @click="openStation(s.slug)">管理配方</button>
                    <router-link class="btn btn-sm btn-ghost" :to="`/recipe/detail?slug=${encodeURIComponent(s.slug)}`">查看</router-link>
                    <button class="btn btn-sm btn-ghost" @click="openRename(s.slug, s.title)">改名</button>
                    <button class="btn btn-sm btn-danger" @click="deleteStation(s)">删除</button>
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
            <button class="btn btn-primary" @click="openAddRecipe">新增配方</button>
            <router-link class="btn btn-ghost" :to="`/recipe/detail?slug=${encodeURIComponent(currentSlug)}`">查看岗位配方</router-link>
            <a class="btn btn-ghost" :href="`/api/recipes/stations/${encodeURIComponent(currentSlug)}/export`">导出 CSV</a>
            <button class="btn btn-ghost" @click="openImportCsv">导入 CSV</button>
            <a class="btn btn-ghost" :href="`/api/recipes/stations/${encodeURIComponent(currentSlug)}/docx`">导出 Word</a>
            <button class="btn btn-ghost" @click="loadStations">返回岗位一览</button>
          </div>
        </header>
        <div class="manage-filter-row">
          <button
            type="button"
            class="sop-chip"
            :aria-pressed="reviewOnly"
            @click="reviewOnly = !reviewOnly"
          >待复核 {{ reviewCount }}</button>
        </div>
        <div class="manage-table-wrap">
          <table class="manage-table">
            <thead><tr><th>排序</th><th>章节</th><th>配方名称</th><th>新品</th><th>状态</th><th class="manage-table-actions">操作</th></tr></thead>
            <tbody>
              <tr
                v-for="(r, idx) in visibleRecipes"
                :key="r.id"
                :class="{ 'is-inactive': !r.is_active }"
                @dragover.prevent
                @drop.prevent="onDrop(idx)"
              >
                <td data-label="排序">
                  <div class="reorder-controls">
                    <button
                      type="button"
                      class="drag-handle"
                      draggable="true"
                      aria-label="拖拽排序"
                      :disabled="reorderBusy || reviewOnly"
                      @dragstart="onDragStart(idx, $event)"
                      @dragend="onDragEnd"
                    >⋮⋮</button>
                    <button
                      type="button"
                      class="btn btn-sm btn-ghost"
                      aria-label="上移"
                      :disabled="idx === 0 || reorderBusy || reviewOnly"
                      @click="moveRecipe(idx, -1)"
                    >上移</button>
                    <button
                      type="button"
                      class="btn btn-sm btn-ghost"
                      aria-label="下移"
                      :disabled="idx === visibleRecipes.length - 1 || reorderBusy || reviewOnly"
                      @click="moveRecipe(idx, 1)"
                    >下移</button>
                  </div>
                </td>
                <td data-label="章节">{{ r.section }}</td>
                <td data-label="配方名称">{{ r.recipe_name }}</td>
                <td data-label="新品"><span v-if="r.is_new" class="badge-new">新</span><span v-else class="muted">—</span></td>
                <td data-label="状态">
                  <span :class="r.is_active ? 'badge-status badge-status--on' : 'badge-status badge-status--off'">
                    {{ r.is_active ? '启用' : '停用' }}
                  </span>
                </td>
                <td class="manage-table-actions" data-label="操作">
                  <div class="row-actions">
                    <button class="btn btn-sm btn-ghost" @click="openEditRecipe(r.id)">编辑</button>
                    <button class="btn btn-sm btn-ghost" @click="openHistory(r.id)">历史</button>
                    <button class="btn btn-sm btn-ghost" @click="toggleActive(r.id)">{{ r.is_active ? '停用' : '启用' }}</button>
                    <button class="btn btn-sm btn-danger" @click="deleteRecipe(r)">删除</button>
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
        <label class="form-label">岗位名称</label>
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

    <RecipeFormModal
      v-if="modal.kind === 'recipe-form'"
      :key="formRecipeId == null ? 'new' : formRecipeId"
      :station-slug="currentSlug"
      :station-title="currentTitle"
      :recipe-id="formRecipeId"
      @close="forceCloseModal"
      @saved="onRecipeFormSaved"
      @review-confirmed="onRecipeFormReviewConfirmed"
    />

    <!-- 弹窗：导入 CSV -->
    <div v-if="modal.kind === 'import-csv'" class="print-preview-modal" @click.self="closeModal">
      <div class="print-preview-modal-backdrop" @click="closeModal"></div>
      <div class="sop-panel" style="position:relative;max-width:560px;width:92%;max-height:86vh;overflow:auto;padding:24px;z-index:1">
        <h2 class="page-title" style="font-size:1.2rem">导入 CSV</h2>
        <p class="form-hint" style="margin-bottom:12px">导入会追加到当前岗位「{{ currentTitle }}」。同名配方会各留一条，不会合并。</p>
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
          <li v-for="x in historyItems" :key="x.id" class="station-item history-item">
            <strong>{{ x.changed_at }}</strong>
            <div>{{ x.recipe_name }} · {{ x.section }}</div>
            <div class="muted">{{ historySummary(x) }}</div>
            <button type="button" class="btn btn-sm btn-primary" @click="restoreHistoryItem(x)">写回这一版</button>
          </li>
        </ul>
        <p v-if="errorMsg" class="flash flash-error" style="margin-top:12px">{{ errorMsg }}</p>
        <div class="row-actions" style="margin-top:16px">
          <button class="btn btn-ghost" @click="closeModal">关闭</button>
        </div>
      </div>
    </div>

    <div
      v-if="confirmDialog.open"
      class="print-preview-modal confirm-dialog"
      role="alertdialog"
      aria-modal="true"
      :aria-labelledby="'recipe-confirm-title'"
      :aria-describedby="'recipe-confirm-body'"
      @click.self="cancelConfirm"
    >
      <div class="print-preview-modal-backdrop" @click="cancelConfirm"></div>
      <div class="sop-panel confirm-dialog-panel">
        <h2 id="recipe-confirm-title" class="page-title" style="font-size:1.2rem">{{ confirmDialog.title }}</h2>
        <p id="recipe-confirm-body" class="page-lead">{{ confirmDialog.body }}</p>
        <p v-if="errorMsg" class="flash flash-error">{{ errorMsg }}</p>
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
.manage-filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 0 0 0.75rem;
}
.history-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.35rem;
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
