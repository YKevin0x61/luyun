<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api/client'
import { useRecipeAdmin } from '../../composables/useRecipeAdmin'
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
import { RECIPE_DETAIL_PATH, RECIPE_HOME_PATH, RECIPE_MANAGE_PATH } from '../../utils/recipePaths'
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

// 顶栏那两颗管理端入口的判据（票 07）：工作台身份，不是「有没有登录」。
const { isAdmin } = useRecipeAdmin()

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
// 岗位表脚注：岗位数 + 这些岗位的配方总数（v3：表尾补一行收边）。
const stationRecipeTotal = computed(() => stations.value.reduce((sum, s) => sum + Number(s.recipe_count || 0), 0))

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
  <div class="manage-page">
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
        </nav>
      </div>
    </header>

    <main class="site-main">
      <section v-if="view === 'stations'" class="manage-section">
        <header class="manage-head">
          <div class="manage-head-copy">
            <p class="manage-kicker">配方 · 管理</p>
            <h1 class="page-title">配方管理</h1>
            <p class="page-lead">新增、改名或删除岗位和配方。阅读和打印从岗位列表进入。</p>
          </div>
          <div class="manage-actions no-print">
            <button class="btn btn-primary" @click="openAddStation">新增岗位</button>
            <router-link class="btn btn-ghost" :to="RECIPE_HOME_PATH">返回岗位列表</router-link>
          </div>
        </header>
        <p v-if="errorMsg" class="flash flash-error manage-flash">{{ errorMsg }}</p>
        <section class="manage-panel">
          <header class="manage-panel-head no-print">
            <i class="manage-led sig" aria-hidden="true"></i>
            <h2>岗位</h2>
            <span class="manage-hint">{{ stations.length }} 个</span>
          </header>
          <div class="manage-table-wrap">
          <table class="manage-table">
            <colgroup>
              <col class="manage-col-name" />
              <col class="manage-col-code" />
              <col class="manage-col-count" />
              <col class="manage-col-actions" />
            </colgroup>
            <thead><tr><th>岗位名称</th><th>标识</th><th class="manage-num">配方数</th><th class="manage-table-actions">操作</th></tr></thead>
            <tbody>
              <tr v-for="s in stations" :key="s.slug">
                <td data-label="岗位名称">{{ s.title }}</td>
                <td data-label="标识"><code class="slug-code">{{ s.slug }}</code></td>
                <td class="manage-num" data-label="配方数">{{ s.recipe_count }}</td>
                <td class="manage-table-actions" data-label="操作">
                  <div class="row-actions">
                    <button class="btn btn-sm btn-ghost" @click="openStation(s.slug)">管理配方</button>
                    <details class="manage-more">
                      <summary class="btn btn-sm btn-ghost" aria-label="更多操作">…</summary>
                      <div class="manage-more-menu">
                        <router-link class="manage-more-item" :to="`${RECIPE_DETAIL_PATH}?slug=${encodeURIComponent(s.slug)}`">查看</router-link>
                        <button type="button" class="manage-more-item" @click="openRename(s.slug, s.title)">改名</button>
                        <button type="button" class="manage-more-item is-danger" @click="deleteStation(s)">删除</button>
                      </div>
                    </details>
                  </div>
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr class="manage-tfoot">
                <td colspan="4">共 {{ stations.length }} 个岗位 · {{ stationRecipeTotal }} 条配方</td>
              </tr>
            </tfoot>
          </table>
          </div>
        </section>
      </section>

      <section v-else class="manage-section">
        <header class="manage-head">
          <div class="manage-head-copy">
            <p class="manage-kicker">配方 · 管理</p>
            <h1 class="page-title">{{ currentTitle }}</h1>
          </div>
          <div class="manage-actions no-print">
            <button class="btn btn-primary" @click="openAddRecipe">新增配方</button>
            <router-link class="btn btn-ghost" :to="`${RECIPE_DETAIL_PATH}?slug=${encodeURIComponent(currentSlug)}`">查看岗位配方</router-link>
            <a class="btn btn-ghost" :href="`/api/recipes/stations/${encodeURIComponent(currentSlug)}/export`">导出 CSV</a>
            <button class="btn btn-ghost" @click="openImportCsv">导入 CSV</button>
            <a class="btn btn-ghost" :href="`/api/recipes/stations/${encodeURIComponent(currentSlug)}/docx`">导出 Word</a>
            <button class="btn btn-ghost" @click="loadStations">返回岗位一览</button>
          </div>
        </header>
        <div class="manage-filter-row no-print">
          <button
            type="button"
            class="sop-chip"
            :aria-pressed="reviewOnly"
            @click="reviewOnly = !reviewOnly"
          >待复核 {{ reviewCount }}</button>
        </div>
        <section class="manage-panel">
          <header class="manage-panel-head no-print">
            <i class="manage-led warn" aria-hidden="true"></i>
            <h2>配方</h2>
            <span class="manage-hint">{{ visibleRecipes.length }} / {{ recipes.length }} 条</span>
          </header>
          <div class="manage-table-wrap">
          <table class="manage-table">
            <colgroup>
              <col class="manage-col-order" />
              <col class="manage-col-section" />
              <col class="manage-col-name" />
              <col class="manage-col-new" />
              <col class="manage-col-status" />
              <col class="manage-col-actions" />
            </colgroup>
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
                    <details class="manage-more">
                      <summary class="btn btn-sm btn-ghost" aria-label="更多操作">…</summary>
                      <div class="manage-more-menu">
                        <button type="button" class="manage-more-item" @click="openHistory(r.id)">历史</button>
                        <button type="button" class="manage-more-item" @click="toggleActive(r.id)">{{ r.is_active ? '停用' : '启用' }}</button>
                        <button type="button" class="manage-more-item is-danger" @click="deleteRecipe(r)">删除</button>
                      </div>
                    </details>
                  </div>
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr class="manage-tfoot">
                <td colspan="6">共 {{ visibleRecipes.length }} 条配方 · 本岗位 {{ recipes.length }} 条</td>
              </tr>
            </tfoot>
          </table>
          </div>
        </section>
      </section>
    </main>

    <!-- 弹窗：新增岗位 -->
    <div v-if="modal.kind === 'add-station'" class="print-preview-modal" @click.self="closeModal">
      <div class="print-preview-modal-backdrop" @click="closeModal"></div>
      <div class="sop-panel manage-modal-panel">
        <h2 class="page-title manage-modal-title">新增岗位</h2>
        <label class="form-label">标识（作 URL，建议拼音/英文）</label>
        <input class="form-input" v-model="stationForm.slug">
        <label class="form-label">岗位名称</label>
        <input class="form-input" v-model="stationForm.title">
        <div class="row-actions manage-modal-actions">
          <button class="btn btn-primary" @click="submitAddStation">创建</button>
          <button class="btn btn-ghost" @click="closeModal">取消</button>
        </div>
        <p v-if="errorMsg" class="flash flash-error manage-modal-error">{{ errorMsg }}</p>
      </div>
    </div>

    <!-- 弹窗：重命名岗位 -->
    <div v-if="modal.kind === 'rename-station'" class="print-preview-modal" @click.self="closeModal">
      <div class="print-preview-modal-backdrop" @click="closeModal"></div>
      <div class="sop-panel manage-modal-panel">
        <h2 class="page-title manage-modal-title">重命名岗位</h2>
        <input class="form-input" v-model="renameForm.title">
        <div class="row-actions manage-modal-actions">
          <button class="btn btn-primary" @click="submitRename">保存</button>
          <button class="btn btn-ghost" @click="closeModal">取消</button>
        </div>
        <p v-if="errorMsg" class="flash flash-error manage-modal-error">{{ errorMsg }}</p>
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
    />

    <!-- 弹窗：导入 CSV -->
    <div v-if="modal.kind === 'import-csv'" class="print-preview-modal" @click.self="closeModal">
      <div class="print-preview-modal-backdrop" @click="closeModal"></div>
      <div class="sop-panel manage-modal-panel is-narrow">
        <h2 class="page-title manage-modal-title">导入 CSV</h2>
        <p class="form-hint manage-modal-hint">导入会追加到当前岗位「{{ currentTitle }}」。同名配方会各留一条，不会合并。</p>
        <RecipeFileDropzone
          ref="csvDropzoneRef"
          accept=".csv,text/csv"
          label="拖拽 CSV 到此处，或点击选择"
          hint="建议使用本页导出的 CSV 格式"
          @change="onCsvSelected"
        />
        <div class="row-actions manage-modal-actions">
          <button class="btn btn-ghost" @click="closeModal">取消</button>
        </div>
      </div>
    </div>

    <!-- 弹窗：历史 -->
    <div v-if="modal.kind === 'history'" class="print-preview-modal" @click.self="closeModal">
      <div class="print-preview-modal-backdrop" @click="closeModal"></div>
      <div class="sop-panel manage-modal-panel">
        <h2 class="page-title manage-modal-title">修改历史</h2>
        <ul class="station-list">
          <li v-if="!historyItems.length" class="station-item muted">暂无历史</li>
          <li v-for="x in historyItems" :key="x.id" class="station-item history-item">
            <strong>{{ x.changed_at }}</strong>
            <div>{{ x.recipe_name }} · {{ x.section }}</div>
            <div class="muted">{{ historySummary(x) }}</div>
            <button type="button" class="btn btn-sm btn-primary" @click="restoreHistoryItem(x)">写回这一版</button>
          </li>
        </ul>
        <p v-if="errorMsg" class="flash flash-error manage-modal-error">{{ errorMsg }}</p>
        <div class="row-actions manage-modal-actions">
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
        <h2 id="recipe-confirm-title" class="page-title manage-modal-title">{{ confirmDialog.title }}</h2>
        <p id="recipe-confirm-body" class="page-lead">{{ confirmDialog.body }}</p>
        <p v-if="errorMsg" class="flash flash-error">{{ errorMsg }}</p>
        <div class="row-actions manage-modal-actions">
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
/* 沉浸页自己就是滚动容器，页头不该再粘住（原来写在内联 style 里）。 */
.sop-site-header {
  position: static;
}
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
/* B 案：拖拽柄是小件（4px 方角），颜色走正文次级色。 */
.drag-handle {
  cursor: grab;
  border: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--ink-dim);
  border-radius: var(--r-1);
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
  gap: 8px;
  margin: 0;
}
.history-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.35rem;
}
.confirm-dialog {
  z-index: var(--z-queue);
}
.confirm-dialog-panel {
  position: relative;
  max-width: 28rem;
  width: 92%;
  padding: 24px;
  z-index: var(--z-shell);
}

/* ── 从模板内联 style 收下来的一次性间距 / 尺寸（设计护栏不许在源码里写
   内联 style 属性；这些值原来逐处重复，现在各有一个语义类名）。 ───────────────── */
.manage-flash {
  margin-bottom: 12px;
}
.manage-modal-panel {
  position: relative;
  width: 92%;
  max-width: 640px;
  max-height: 86vh;
  overflow: auto;
  padding: 24px;
  z-index: var(--z-shell);
}
.manage-modal-panel.is-narrow {
  max-width: 560px;
}
.manage-modal-title {
  font-size: 1.2rem;
}
.manage-modal-actions {
  margin-top: 16px;
}
.manage-modal-hint {
  margin-bottom: 12px;
}
.manage-modal-error {
  margin-top: 12px;
}

/* ── B 案 v2 页面骨架：page-head + 面板（面板里是表格行）──────────────── */
.manage-page {
  padding-bottom: calc(40px + var(--safe-b));
}
.manage-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.manage-head {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 16px;
}
.manage-head-copy {
  flex: 1 1 auto;
  min-width: 0;
}
.manage-kicker {
  margin: 0 0 7px;
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .26em;
  color: var(--ink-faint);
}
/* 标题：600 权重 + .01em 字距（`recipe.css` 旧皮是 900 重标题）。 */
.page-title {
  margin: 0 0 6px;
  font: 600 26px/1.05 var(--font-sans);
  letter-spacing: .01em;
  color: var(--ink);
}
.page-lead {
  margin: 0;
  font-size: 12.5px;
  color: var(--ink-dim);
}
.manage-actions {
  margin-top: 0;
  margin-left: auto;
  flex: 0 0 auto;
}
.manage-panel {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  overflow: hidden;
}
.manage-panel-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 11px 14px;
  border-bottom: 1px solid var(--line);
}
.manage-panel-head h2 {
  margin: 0;
  font: 600 12.5px/1 var(--font-sans);
  letter-spacing: .06em;
  color: var(--ink);
}
.manage-hint {
  margin-left: auto;
  font: 500 9.5px/1 var(--font-mono);
  color: var(--ink-faint);
}
.manage-led {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
  flex: 0 0 6px;
}
.manage-led.ok { background: var(--ok); box-shadow: 0 0 0 3px var(--wb-ok-a16); }
.manage-led.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.manage-led.bad { background: var(--danger); box-shadow: 0 0 0 3px var(--wb-danger-a16); }
.manage-led.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }

/* ── B 案「钢面 · 仪」（屏幕）────────────────────────────────────────────
   `recipe.css` 的表格容器是 16px 圆角 + 重投影、表头是薄荷软底；B 案里表格
   退成面板里的行，容器不再自带边框，表头是 11px / .06em 的刻度灰。
   圈在 `@media screen`：这一页的弹层与表格不进打印。 */
@media screen {
  .manage-table-wrap {
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    overflow: auto;
  }
  .manage-table th {
    background: var(--surface-2);
    color: var(--ink-faint);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .06em;
  }
  /* v3：列宽按内容固定，名称列吃剩余宽度（等价 minmax(200px,1fr)） */
  .manage-col-name { width: auto; }
  .manage-col-code { width: 140px; }
  .manage-col-count { width: 88px; }
  .manage-col-actions { width: 200px; }
  .manage-col-order { width: 128px; }
  .manage-col-section { width: 120px; }
  .manage-col-new { width: 64px; }
  .manage-col-status { width: 88px; }
  .manage-num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .manage-tfoot td {
    padding: 10px 14px;
    border-top: 1px solid var(--line);
    color: var(--ink-faint);
    font: 500 10px/1.4 var(--font-mono);
    letter-spacing: .02em;
  }
  /* 行内「…」菜单：details + summary，原生可键盘操作 */
  .manage-more {
    position: relative;
  }
  .manage-more > summary {
    list-style: none;
    cursor: pointer;
    min-width: 44px;
    justify-content: center;
  }
  .manage-more > summary::-webkit-details-marker {
    display: none;
  }
  .manage-more[open] > summary {
    border-color: var(--line-strong);
    color: var(--ink);
  }
  .manage-more-menu {
    position: absolute;
    right: 0;
    top: calc(100% + 4px);
    z-index: var(--z-shell);
    display: flex;
    flex-direction: column;
    min-width: 8.5rem;
    padding: 4px;
    border: 1px solid var(--line);
    border-radius: var(--r-1);
    background: var(--surface-1);
    box-shadow: var(--hy-shadow-md);
  }
  .manage-more-item {
    display: flex;
    align-items: center;
    min-height: 44px;
    padding: 0 10px;
    border: 0;
    border-radius: var(--r-0);
    background: transparent;
    color: var(--ink);
    font: inherit;
    font-size: 12px;
    text-align: left;
    text-decoration: none;
    cursor: pointer;
  }
  .manage-more-item:hover,
  .manage-more-item:focus-visible {
    background: var(--surface-2);
  }
  .manage-more-item.is-danger {
    color: var(--danger-bright);
  }
  .manage-table tbody tr:hover td {
    background: var(--surface-2);
  }
  /* 桌面档的行高；手机档交给 recipe.css 的「表格变卡片」那套（≤48rem）。 */
  .manage-table td {
    font-size: 12.5px;
    padding: 11px 14px;
  }
  .manage-modal-panel {
    border-color: var(--line);
    border-radius: var(--r-2);
    background: var(--surface-1);
    box-shadow: var(--hy-shadow-md);
  }
  .slug-code {
    border-radius: var(--r-1);
  }
  /* 状态 = 6px LED + 9–11px 小字（不再做整块填充色胶囊）。 */
  .badge-status {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    font-size: 11px;
    font-weight: 600;
    color: var(--ink-dim);
  }
  .badge-status::before {
    content: "";
    flex: 0 0 6px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--ink-faint);
  }
  .badge-status--on {
    color: var(--ink);
  }
  .badge-status--on::before {
    background: var(--ok);
  }
  .badge-status--off::before {
    background: var(--ink-faint);
  }
  .badge-new {
    min-width: 0;
    padding: 2px 8px;
    border: 1px solid var(--wb-warn-a45);
    border-radius: var(--r-full);
    background: var(--wb-warn-a10);
    color: var(--warn);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .04em;
  }
}

@media screen and (max-width: 720px) {
  .manage-table td {
    font-size: inherit;
    padding: .28rem 0;
  }
}
@media (max-width: 720px) {
  .manage-head {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
  .manage-actions {
    margin-left: 0;
  }
}
</style>
