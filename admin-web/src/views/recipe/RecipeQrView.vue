<script setup>
import { nextTick, onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import RecipeNavIcon from './RecipeNavIcon.vue'
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

const stations = ref([])
const loading = ref(true)
const errorMsg = ref('')

onMounted(async () => {
  document.title = recipeDocumentTitle('岗位二维码')
  try {
    const data = await api.get('/api/recipes/stations')
    stations.value = data.stations || []
    await nextTick()
    for (const s of stations.value) {
      const canvas = document.getElementById(`qr-${s.slug}`)
      if (!canvas) continue
      const url = `${window.location.origin}/recipe/detail?slug=${encodeURIComponent(s.slug)}`
      QRCode.toCanvas(canvas, url, { width: 150, margin: 1 })
    }
  } catch (e) {
    errorMsg.value = '无法加载岗位列表，请稍后重试'
  } finally {
    loading.value = false
  }
})

function doPrint() {
  window.print()
}
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
        <div class="sop-header-actions no-print">
          <button type="button" class="print-button" @click="doPrint"><RecipeNavIcon name="printer" :size="14" />打印</button>
        </div>
      </div>
    </header>
    <main class="site-main">
      <header class="index-hero no-print">
        <div class="hero-copy">
          <h1 class="page-title">岗位二维码</h1>
          <p class="page-lead">打印后张贴到岗位，扫码查看该岗位配方。</p>
        </div>
      </header>
      <div v-if="loading" class="loading-state">加载岗位列表…</div>
      <div v-else-if="errorMsg" class="empty-state">{{ errorMsg }}</div>
      <div v-else-if="!stations.length" class="empty-state">暂无岗位。可在配方管理里新增。</div>
      <div v-else class="qr-grid">
        <div v-for="s in stations" :key="s.slug" class="qr-card">
          <div class="qr-card-box"><canvas :id="`qr-${s.slug}`"></canvas></div>
          <div class="qr-card-title">{{ s.title }}</div>
        </div>
      </div>
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
