<script setup>
import { nextTick, onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import RecipeNavIcon from './RecipeNavIcon.vue'

useScopedStylesheet('/recipe.css')

const stations = ref([])
const loading = ref(true)
const errorMsg = ref('')

onMounted(async () => {
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
    errorMsg.value = '加载失败'
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
          <span class="site-brand-mark" aria-hidden="true"><span class="site-brand-mark-inner">SOP</span></span>
          <span class="site-brand-text">
            <span class="site-brand-title">配方 SOP</span>
            <span class="site-brand-tagline">岗位二维码 · 张贴</span>
          </span>
        </router-link>
        <nav class="site-nav no-print">
          <router-link class="site-nav-link" to="/"><RecipeNavIcon name="home" :size="14" />返回主页</router-link>
          <router-link class="site-nav-link" to="/recipe"><RecipeNavIcon name="layout-grid" :size="14" />岗位列表</router-link>
          <router-link class="site-nav-link" to="/recipe/manage"><RecipeNavIcon name="sparkles" :size="14" />配方管理</router-link>
        </nav>
        <div class="sop-header-actions no-print">
          <button type="button" class="print-button" @click="doPrint"><RecipeNavIcon name="printer" :size="14" />打印</button>
        </div>
      </div>
    </header>
    <main class="site-main">
      <header class="index-hero no-print">
        <div class="hero-copy">
          <span class="hero-eyebrow">QR Posters</span>
          <h1 class="page-title">岗位二维码</h1>
          <p class="page-lead">打印张贴到各档口，扫码即可在手机查看该岗位 SOP。</p>
        </div>
      </header>
      <div v-if="loading" class="loading-state">加载中...</div>
      <div v-else-if="errorMsg" class="empty-state">{{ errorMsg }}</div>
      <div v-else-if="!stations.length" class="empty-state">暂无岗位</div>
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
