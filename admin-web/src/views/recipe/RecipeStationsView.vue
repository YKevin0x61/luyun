<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import * as RC from '../../utils/recipeCore'
import {
  SEARCH_GROUP_ITEM_CAP,
  capGroupedSearchHits,
  flattenCappedSearchHits,
  nextTypeaheadKeyboardState,
  recipeFocusLocation,
} from '../../utils/recipeSearchTypeahead'
import RecipeNavIcon from './RecipeNavIcon.vue'

useScopedStylesheet('/recipe.css')

const SEARCH_DEBOUNCE_MS = 120

const router = useRouter()
const stations = ref([])
const loading = ref(true)
const errorMsg = ref('')
const batchMode = ref(false)
const selected = ref({})

const searchRoot = ref(null)
const searchQ = ref('')
const searchGroups = ref([])
const searchLoading = ref(false)
const searchError = ref('')
const dropdownOpen = ref(false)
const activeIndex = ref(-1)
let searchDebounce = null
let searchSeq = 0
let hasSearched = false

const visibleGroups = computed(() => capGroupedSearchHits(searchGroups.value, SEARCH_GROUP_ITEM_CAP))
const flatHits = computed(() => flattenCappedSearchHits(searchGroups.value, SEARCH_GROUP_ITEM_CAP))
const activeOptionId = computed(() => (
  dropdownOpen.value && activeIndex.value >= 0 ? `station-search-opt-${activeIndex.value}` : undefined
))

function applyTheme(t) {
  const norm = RC.normalizeTheme(t)
  if (norm === 'auto') document.documentElement.removeAttribute('data-theme')
  else document.documentElement.setAttribute('data-theme', norm)
}

onMounted(() => {
  applyTheme(RC.readPref(window.localStorage, 'sop.theme', 'auto'))
  document.addEventListener('pointerdown', onDocumentPointerDown)
  load()
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  clearTimeout(searchDebounce)
  document.body.classList.remove('batch-mode')
  document.documentElement.removeAttribute('data-theme')
})

async function load() {
  loading.value = true
  try {
    const data = await api.get('/api/recipes/stations')
    stations.value = data.stations || []
    errorMsg.value = ''
  } catch (e) {
    errorMsg.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function toggleBatch() {
  batchMode.value = !batchMode.value
  document.body.classList.toggle('batch-mode', batchMode.value)
  if (!batchMode.value) selected.value = {}
}

function onItemClick(slug, evt) {
  if (!batchMode.value) return
  evt.preventDefault()
  if (selected.value[slug]) delete selected.value[slug]
  else selected.value[slug] = true
}

function printSelected() {
  const slugs = Object.keys(selected.value)
  if (!slugs.length) {
    window.alert('请先勾选岗位')
    return
  }
  router.push({ path: '/recipe/print', query: { slugs: slugs.join(',') } })
}

function onSearchInput() {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(runSearch, SEARCH_DEBOUNCE_MS)
}

function onSearchFocus() {
  if (searchQ.value.trim() && hasSearched) dropdownOpen.value = true
}

async function runSearch() {
  const q = searchQ.value.trim()
  const seq = ++searchSeq
  if (!q) {
    searchGroups.value = []
    searchError.value = ''
    searchLoading.value = false
    dropdownOpen.value = false
    activeIndex.value = -1
    hasSearched = false
    return
  }
  searchLoading.value = true
  searchError.value = ''
  dropdownOpen.value = true
  try {
    const data = await api.get('/api/recipes/search', { q })
    if (seq !== searchSeq) return
    searchGroups.value = data.groups || []
    hasSearched = true
    activeIndex.value = flattenCappedSearchHits(searchGroups.value).length ? 0 : -1
  } catch (e) {
    if (seq !== searchSeq) return
    searchGroups.value = []
    hasSearched = true
    searchError.value = e.status ? '搜索失败，请稍后重试' : '网络异常，请稍后重试'
    activeIndex.value = -1
  } finally {
    if (seq === searchSeq) searchLoading.value = false
  }
}

function onSearchKeydown(evt) {
  const key = evt.key
  if (key !== 'ArrowDown' && key !== 'ArrowUp' && key !== 'Enter' && key !== 'Escape') return
  if (!searchQ.value.trim() && key !== 'Escape') return
  evt.preventDefault()
  const next = nextTypeaheadKeyboardState(
    { open: dropdownOpen.value, activeIndex: activeIndex.value, itemCount: flatHits.value.length },
    key,
  )
  dropdownOpen.value = next.open
  activeIndex.value = next.activeIndex
  if (next.selectedIndex != null) {
    const hit = flatHits.value[next.selectedIndex]
    if (hit) goToHit(hit)
  }
}

function goToHit(hit) {
  dropdownOpen.value = false
  router.push(recipeFocusLocation(hit.station_slug, hit.recipe_id))
}

function itemFlatIndex(recipeId) {
  return flatHits.value.findIndex((hit) => hit.recipe_id === recipeId)
}

function onDocumentPointerDown(evt) {
  const root = searchRoot.value
  if (root && !root.contains(evt.target)) dropdownOpen.value = false
}
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
          <router-link class="site-nav-link" to="/"><RecipeNavIcon name="home" :size="14" />返回仪表盘</router-link>
          <router-link class="site-nav-link" to="/recipe"><RecipeNavIcon name="layout-grid" :size="14" />岗位列表</router-link>
          <router-link class="site-nav-link" to="/recipe/manage"><RecipeNavIcon name="sparkles" :size="14" />配方管理</router-link>
        </nav>
      </div>
    </header>
    <main class="site-main">
      <section class="index-section">
        <header class="index-hero">
          <div class="hero-copy">
            <span class="hero-eyebrow">SOP Command Center</span>
            <h1 class="page-title">茶楼岗位配方中枢</h1>
            <p class="page-lead">把配方、出品标准、检核和打印交付整合到一个高密度工作台。</p>
            <div class="station-search" ref="searchRoot">
              <input
                id="station-global-search"
                type="search"
                class="sop-search-input station-search-input"
                placeholder="搜索条目名称…"
                autocomplete="off"
                aria-label="搜索配方条目"
                aria-autocomplete="list"
                :aria-expanded="dropdownOpen ? 'true' : 'false'"
                aria-controls="station-search-listbox"
                :aria-activedescendant="activeOptionId"
                v-model="searchQ"
                @input="onSearchInput"
                @focus="onSearchFocus"
                @keydown="onSearchKeydown"
              >
              <div
                v-if="dropdownOpen"
                id="station-search-listbox"
                class="station-search-dropdown"
                :role="flatHits.length && !searchLoading && !searchError ? 'listbox' : 'status'"
              >
                <div v-if="searchLoading" class="station-search-status">搜索中…</div>
                <div v-else-if="searchError" class="station-search-status">{{ searchError }}</div>
                <div v-else-if="!flatHits.length" class="station-search-status">未找到匹配条目</div>
                <template v-else>
                  <div
                    v-for="group in visibleGroups"
                    :key="group.station_slug"
                    class="station-search-group"
                    role="group"
                    :aria-label="group.station_title"
                  >
                    <div class="station-search-group-title">{{ group.station_title }}</div>
                    <button
                      v-for="item in group.items"
                      :id="`station-search-opt-${itemFlatIndex(item.recipe_id)}`"
                      :key="item.recipe_id"
                      type="button"
                      class="station-search-option"
                      role="option"
                      :aria-selected="itemFlatIndex(item.recipe_id) === activeIndex ? 'true' : 'false'"
                      @mousedown.prevent
                      @click="goToHit({ ...item, station_slug: group.station_slug })"
                    >
                      <span>{{ item.recipe_name }}</span>
                      <span class="station-search-option-section">{{ item.section }}</span>
                    </button>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </header>
        <div class="section-caption"><span>Station Library</span><strong>选择岗位开始</strong></div>
        <div class="station-toolbar no-print">
          <button type="button" class="sop-chip" :aria-pressed="batchMode" @click="toggleBatch">批量打印</button>
          <button v-if="batchMode" type="button" class="btn btn-primary btn-sm" @click="printSelected">
            打印所选 ({{ Object.keys(selected).length }})
          </button>
          <router-link class="btn btn-ghost btn-sm" to="/recipe/qr">岗位二维码</router-link>
        </div>
        <div v-if="loading" class="loading-state">加载中...</div>
        <div v-else-if="errorMsg" class="empty-state">{{ errorMsg }}</div>
        <ul v-else class="station-list">
          <li
            v-for="s in stations"
            :key="s.slug"
            class="station-item"
            :class="{ 'is-checked': selected[s.slug] }"
          >
            <span class="station-check" aria-hidden="true"></span>
            <router-link class="station-link" :to="`/recipe/detail?slug=${encodeURIComponent(s.slug)}`" @click="onItemClick(s.slug, $event)">
              <span class="station-link-icon" aria-hidden="true">{{ (s.title || '·').slice(0, 1) }}</span>
              <span class="station-link-copy">
                <span class="station-link-title">
                  {{ s.title }}
                  <span
                    v-if="s.needs_review_count > 0"
                    class="station-review-badge"
                  >待复核 {{ s.needs_review_count }}</span>
                </span>
                <span class="station-link-subtitle">{{ s.recipe_count }} 个条目 · 查看配方 / 出品标准 / 检核</span>
              </span>
              <span class="station-link-arrow" aria-hidden="true">→</span>
            </router-link>
          </li>
        </ul>
      </section>
    </main>
  </div>
</template>

<style scoped>
.site-nav-link {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
.station-link-title {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
}
.station-review-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.08rem 0.4rem;
  border-radius: 999px;
  font-size: 0.68rem;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: #fff;
  background: var(--new);
  font-variant-numeric: tabular-nums;
}
</style>
