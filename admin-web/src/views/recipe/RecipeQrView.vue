<script setup>
import { nextTick, onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import { api } from '../../api/client'
import { useRecipeAdmin } from '../../composables/useRecipeAdmin'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import RecipeNavIcon from './RecipeNavIcon.vue'
import RecipeExitButton from '../../components/recipe/RecipeExitButton.vue'
import {
  RECIPE_DETAIL_PATH,
  RECIPE_HOME_PATH,
  RECIPE_MANAGE_PATH,
} from '../../utils/recipePaths'
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
      // 票 07：岗位码指向工作台「后勤」组里的阅读页。岗位码从未张贴过，改地址
      // 没有存量风险；但**必须真的改** —— 老地址已经不作路由了（自然 404）。
      const url = `${window.location.origin}${RECIPE_DETAIL_PATH}?slug=${encodeURIComponent(s.slug)}`
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
