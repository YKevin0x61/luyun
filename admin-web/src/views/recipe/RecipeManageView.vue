<script setup>
import { reactive, ref } from 'vue'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import RecipeNavIcon from './RecipeNavIcon.vue'
import RecipeCheckbox from '../../components/recipe/RecipeCheckbox.vue'
import RecipeFileDropzone from '../../components/recipe/RecipeFileDropzone.vue'

useScopedStylesheet('/recipe.css')

const view = ref('stations') // 'stations' | 'recipes'
const stations = ref([])
const currentSlug = ref('')
const currentTitle = ref('')
const recipes = ref([])
const csvDropzoneRef = ref(null)
const errorMsg = ref('')

// 各类弹窗状态（复用一个 modal 容器，按 kind 渲染不同表单）
const modal = reactive({ kind: null }) // 'add-station' | 'rename-station' | 'recipe-form' | 'history'
const stationForm = reactive({ slug: '', title: '' })
const renameForm = reactive({ slug: '', title: '' })
const recipeForm = reactive({ id: null, section: '配方', recipe_name: '', body: '', sort_order: '', is_new: false })
const historyItems = ref([])

function closeModal() {
  if (modal.kind === 'import-csv') csvDropzoneRef.value?.reset()
  modal.kind = null
  errorMsg.value = ''
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
  recipeForm.sort_order = ''
  recipeForm.is_new = false
  modal.kind = 'recipe-form'
}
async function openEditRecipe(id) {
  const hist = await api.get(`/api/recipes/recipes/${id}/history`)
  const r = hist.current
  recipeForm.id = id
  recipeForm.section = r.section
  recipeForm.recipe_name = r.recipe_name
  recipeForm.body = r.body_markdown
  recipeForm.sort_order = String(r.sort_order ?? '')
  recipeForm.is_new = !!r.is_new
  modal.kind = 'recipe-form'
}
async function submitRecipeForm() {
  errorMsg.value = ''
  const sortRaw = String(recipeForm.sort_order).trim()
  const payload = {
    section: recipeForm.section,
    recipe_name: recipeForm.recipe_name,
    body: recipeForm.body,
    sort_order: sortRaw === '' ? null : parseInt(sortRaw, 10),
    is_new: recipeForm.is_new,
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
              <tr v-for="r in recipes" :key="r.id" :class="{ 'is-inactive': !r.is_active }">
                <td class="muted">{{ r.sort_order }}</td>
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
      <div class="sop-panel" style="position:relative;max-width:640px;width:92%;max-height:86vh;overflow:auto;padding:24px;z-index:1">
        <h2 class="page-title" style="font-size:1.2rem">{{ recipeForm.id ? '编辑条目' : '新增条目' }}</h2>
        <label class="form-label">章节</label>
        <input class="form-input" v-model="recipeForm.section">
        <label class="form-label">条目名称</label>
        <input class="form-input" v-model="recipeForm.recipe_name">
        <label class="form-label">排序号（留空＝章节末尾）</label>
        <input class="form-input" v-model="recipeForm.sort_order">
        <label class="form-check"><RecipeCheckbox v-model="recipeForm.is_new" /><span>标记为新品</span></label>
        <label class="form-label">正文（Markdown）</label>
        <textarea class="form-input" rows="8" v-model="recipeForm.body"></textarea>
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
</style>
