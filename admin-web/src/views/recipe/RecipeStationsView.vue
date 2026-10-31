<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
import { useRecipeAdmin } from '../../composables/useRecipeAdmin'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import * as RC from '../../utils/recipeCore'
import {
  RECIPE_DETAIL_PATH,
  RECIPE_HOME_PATH,
  RECIPE_MANAGE_PATH,
  RECIPE_PRINT_PATH,
  RECIPE_QR_PATH,
} from '../../utils/recipePaths'
import {
  SEARCH_GROUP_ITEM_CAP,
  capGroupedSearchHits,
  flattenCappedSearchHits,
  nextTypeaheadKeyboardState,
  recipeFocusLocation,
} from '../../utils/recipeSearchTypeahead'
import RecipeNavIcon from './RecipeNavIcon.vue'
import RecipeExitButton from '../../components/recipe/RecipeExitButton.vue'
import {
  RECIPE_BRAND_MARK,
  RECIPE_BRAND_TAGLINE,
  RECIPE_BRAND_TITLE,
  RECIPE_NAV_HOME_LABEL,
  RECIPE_NAV_MANAGE_LABEL,
  RECIPE_NAV_STATIONS_LABEL,
  recipeCountLabel,
  recipeDocumentTitle,
} from '../../utils/recipeCopy'

useScopedStylesheet('/recipe.css')

// 顶栏那两颗管理端入口的判据（票 07）：工作台身份，不是「有没有登录」。
const { isAdmin } = useRecipeAdmin()

const SEARCH_DEBOUNCE_MS = 120

const router = useRouter()
const stations = ref([])
const loading = ref(true)
const errorMsg = ref('')
const batchMode = ref(false)
const selected = ref({})
// 批量打印只在「有勾选」时才有路径（v3 审查）：没有勾选就不出现打印入口。
const selectedCount = computed(() => Object.keys(selected.value).length)

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
  document.title = recipeDocumentTitle('选择岗位')
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
    errorMsg.value = e.message || '无法加载岗位列表，请稍后重试'
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
  router.push({ path: RECIPE_PRINT_PATH, query: { slugs: slugs.join(',') } })
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
  <div class="station-page">
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
      </div>
    </header>
    <main class="site-main">
      <section class="index-section station-page-body">
        <!-- 页头：kicker / H1 / 右侧读数 + 动作（B 案 v2 页面骨架） -->
        <header class="station-head">
          <div class="station-head-copy">
            <p class="station-kicker">配方 · 岗位</p>
            <h1 class="page-title">选择岗位</h1>
            <p class="page-lead">查看该岗位配方，也可打印或张贴二维码。</p>
            <div class="station-search" ref="searchRoot">
              <input
                id="station-global-search"
                type="search"
                class="sop-search-input station-search-input"
                placeholder="搜索配方名称…"
                autocomplete="off"
                aria-label="搜索配方"
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
                <div v-else-if="!flatHits.length" class="station-search-status">未找到匹配配方</div>
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
          <div class="station-head-side no-print">
            <span class="station-pill">
              <i class="station-led sig" aria-hidden="true"></i>{{ stations.length }} 个岗位
            </span>
            <button type="button" class="sop-chip station-chip" :aria-pressed="batchMode" @click="toggleBatch">批量打印</button>
            <button v-if="batchMode && selectedCount" type="button" class="btn btn-primary btn-sm" @click="printSelected">
              打印所选 ({{ selectedCount }})
            </button>
            <!-- 印码入口按身份出现（票 04 起印码页是 `admin` 专属）：员工是**扫码**的那一方，
                 扫到的是配方详情页；这一页在清单里是 `both`（员工也看得配方列表），但这颗按钮
                 落到的是店长才能打开的印码页 —— 无条件渲染的话员工点下去只吃一张「无权访问」。
                 判据与同页顶栏那两颗入口一样（`useRecipeAdmin` 的工作台身份）。 -->
            <router-link v-if="isAdmin" class="btn btn-ghost btn-sm" :to="RECIPE_QR_PATH">岗位二维码</router-link>
          </div>
        </header>

        <!-- 岗位面板：行结构 = 字标 / 名称+副标题 / 箭头 -->
        <section class="station-panel">
          <header class="station-panel-head no-print">
            <i class="station-led sig" aria-hidden="true"></i>
            <h2>岗位</h2>
            <span class="station-hint">点一张进入配方</span>
          </header>
          <div v-if="loading" class="loading-state">加载岗位列表…</div>
          <div v-else-if="errorMsg" class="empty-state">{{ errorMsg }}</div>
          <div v-else-if="!stations.length" class="empty-state">暂无岗位。可在配方管理里新增。</div>
          <ul v-else class="station-list">
            <li
              v-for="s in stations"
              :key="s.slug"
              class="station-item"
              :class="{ 'is-checked': selected[s.slug] }"
            >
              <div class="station-card">
                <router-link class="station-link" :to="`${RECIPE_DETAIL_PATH}?slug=${encodeURIComponent(s.slug)}`" @click="onItemClick(s.slug, $event)">
                  <span class="station-link-icon" aria-hidden="true">{{ (s.title || '·').slice(0, 1) }}</span>
                  <span class="station-link-copy">
                    <span class="station-link-title">{{ s.title }}</span>
                    <span class="station-link-subtitle">
                      <span
                        v-if="s.needs_review_count > 0"
                        class="station-review-badge"
                      >待复核 {{ s.needs_review_count }}</span>
                      <span class="station-link-count">{{ recipeCountLabel(s.recipe_count) }}</span>
                    </span>
                  </span>
                </router-link>
                <div class="station-card-actions">
                  <span class="station-check" aria-hidden="true"></span>
                  <router-link
                    class="station-qr-link"
                    :to="RECIPE_QR_PATH"
                    :aria-label="`${s.title} 岗位二维码`"
                  >二维码</router-link>
                </div>
              </div>
            </li>
          </ul>
        </section>
      </section>
    </main>
  </div>
</template>

<style scoped>
/* 大标题用**配方自己的**字色（票 12 收的 O5）：这一页自从搬进 `/workbench/kitchen/recipe`
   （票 07）就套在工作台外壳里，而外壳那条 `.hygiene-admin h1/h2 { color: var(--hy-ink) }`
   （特异度 0,1,1）压过 `recipe.css` 的 `.page-title`（0,1,0）—— 深色工作台的字色漏到这张
   白底 hero 上就是白底白字，真机实测 computed color `rgb(228,240,238)`、卡片底是白色。
   这里按配方自己的令牌盖回来；只作用于这一页（scoped 的 `[data-v-…]` 提到 0,2,0），
   不动全局主题，也不动外壳那条规则（配方管理页的标题坐在深色底上，是对的）。 */
.sop-site-header {
  position: static;
}
.site-nav-link {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
/* ── B 案 v2 页面骨架：page-head + 面板 + 行 ───────────────────────────
   旧皮是「一整块 hero + 岗位大卡网格」；新骨架把标题区收成一行页头（kicker / H1 /
   右侧读数与动作），岗位本身退成面板里的一列行。打印不走这套（全部圈在
   `@media screen`，打印仍是 `recipe.css` 末尾那套白纸规则）。 */
.station-page {
  padding-bottom: calc(40px + var(--safe-b));
}
.station-page-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.station-head {
  display: flex;
  align-items: flex-end;
  gap: 16px;
}
.station-head-copy {
  flex: 1 1 auto;
  min-width: 0;
}
.station-kicker {
  margin: 0 0 7px;
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .26em;
  color: var(--ink-faint);
}
.page-title {
  margin: 0 0 6px;
  color: var(--ink);
  font: 600 26px/1.05 var(--font-sans);
  letter-spacing: .01em;
}
.page-lead {
  margin: 0;
  font-size: 12.5px;
  color: var(--ink-dim);
}
.station-head-side {
  margin-left: auto;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.station-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink-dim);
  font: 500 10px/1 var(--font-mono);
}
.station-led {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
  flex: 0 0 6px;
}
.station-led.ok { background: var(--ok); box-shadow: 0 0 0 3px var(--wb-ok-a16); }
.station-led.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.station-led.bad { background: var(--danger); box-shadow: 0 0 0 3px var(--wb-danger-a16); }
.station-led.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }
.station-search {
  max-width: 36rem;
}
.station-panel-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 11px 14px;
  border-bottom: 1px solid var(--line);
}
.station-panel-head h2 {
  margin: 0;
  font: 600 12.5px/1 var(--font-sans);
  letter-spacing: .06em;
  color: var(--ink);
}
.station-hint {
  margin-left: auto;
  font: 500 9.5px/1 var(--font-mono);
  color: var(--ink-faint);
}
.station-link-title {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
}
/* 待复核：B 案的状态语言是「6px 圆点 + 小字」，替掉旧皮那颗红底白字胶囊。 */
.station-review-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px 2px 6px;
  border: 1px solid var(--wb-warn-a45);
  border-radius: var(--r-full);
  background: var(--wb-warn-a10);
  color: var(--warn);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  font-variant-numeric: tabular-nums;
}
.station-review-badge::before {
  content: "";
  flex: 0 0 6px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--warn);
}

@media screen {
  .station-search-input {
    border: 1px solid var(--line-strong);
    border-radius: var(--r-1);
    background: var(--surface-1);
  }
  /* 唯一卡片语言：面板；岗位是面板里的行，不再是「大卡套卡片」。 */
  .station-panel {
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-radius: var(--r-2);
    box-shadow: var(--hy-shadow-sm);
    overflow: hidden;
  }
  /* v3 审查：4 行改成岗位卡网格（宽屏 2 行 × 3 列），卡内带二维码入口。 */
  .station-list {
    list-style: none;
    margin: 0;
    padding: 14px;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px;
  }
  .station-item {
    animation: none;
    min-width: 0;
  }
  .station-card {
    position: relative;
    display: flex;
    flex-direction: column;
    height: 100%;
    border: 1px solid var(--line);
    border-radius: var(--r-2);
    background: var(--surface-1);
    box-shadow: var(--hy-shadow-sm);
    overflow: hidden;
    transition: border-color .15s var(--ease), background .15s var(--ease);
  }
  .station-card:hover {
    border-color: var(--line-strong);
  }
  .station-item.is-checked .station-card {
    border-color: var(--action);
    box-shadow: 0 0 0 2px var(--wb-tint-22), var(--hy-shadow-sm);
  }
  .station-link {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
    flex: 1 1 auto;
    min-height: 44px;
    padding: 14px 14px 10px;
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    color: var(--ink);
    overflow: visible;
    transition: background .15s var(--ease);
  }
  .station-link::before {
    display: none;
  }
  .station-link:hover,
  .station-link:focus-visible {
    transform: none;
    border-color: transparent;
    background: var(--surface-2);
    box-shadow: none;
  }
  .station-link-icon {
    width: 2.4rem;
    height: 2.4rem;
    margin-left: 0;
    border: 1px solid var(--line);
    border-radius: var(--r-1);
    background: var(--surface-2);
    color: var(--action);
    font-size: 1rem;
    font-weight: 600;
  }
  .station-link-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .station-link-title {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0;
    color: var(--ink);
  }
  .station-link-subtitle {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    color: var(--ink-faint);
  }
  .station-link-count {
    font-variant-numeric: tabular-nums;
  }
  .station-link-arrow {
    display: none;
  }
  /* 卡底动作行：行内 checkbox（批量模式）+ 二维码入口 */
  .station-card-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 14px 12px;
    margin-top: auto;
  }
  .station-qr-link {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 44px;
    padding: 0 14px;
    border: 1px solid var(--line-strong);
    border-radius: var(--r-1);
    background: transparent;
    color: var(--ink-dim);
    font-size: 11px;
    text-decoration: none;
  }
  .station-qr-link:hover,
  .station-qr-link:focus-visible {
    color: var(--ink);
    border-color: var(--ink-faint);
  }
  .station-check {
    display: none;
  }
  body.batch-mode .station-card .station-check {
    display: block;
    position: static;
    width: 20px;
    height: 20px;
    margin: 0;
    border: 2px solid var(--line-strong);
    border-radius: var(--r-1);
    background: var(--surface-1);
  }
  body.batch-mode .station-item.is-checked .station-card .station-check {
    background: var(--action);
    border-color: var(--action);
    box-shadow: inset 0 0 0 2px var(--surface-1);
  }
}

@media (max-width: 720px) {
  /* 手机上保持 2 列小卡（1 列会让单卡铺满一屏、留白过大） */
  .station-list {
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
    padding: 12px;
  }
  .station-link {
    padding: 12px 12px 8px;
  }
  .station-card-actions {
    padding: 0 12px 10px;
  }
  .station-head {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
  .station-head-side {
    margin-left: 0;
  }
}
</style>
