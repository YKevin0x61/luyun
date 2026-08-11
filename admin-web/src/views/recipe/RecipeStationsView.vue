<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import * as RC from '../../utils/recipeCore'
import RecipeNavIcon from './RecipeNavIcon.vue'

useScopedStylesheet('/recipe.css')

const router = useRouter()
const stations = ref([])
const loading = ref(true)
const errorMsg = ref('')
const batchMode = ref(false)
const selected = ref({})

function applyTheme(t) {
  const norm = RC.normalizeTheme(t)
  if (norm === 'auto') document.documentElement.removeAttribute('data-theme')
  else document.documentElement.setAttribute('data-theme', norm)
}

onMounted(() => {
  applyTheme(RC.readPref(window.localStorage, 'sop.theme', 'auto'))
  load()
})
onBeforeUnmount(() => {
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
                <span class="station-link-title">{{ s.title }}</span>
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
</style>
