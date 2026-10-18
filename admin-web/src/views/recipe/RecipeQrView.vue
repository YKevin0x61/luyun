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

/** 把每个岗位的码画进对应画布。**必须等 `.qr-grid` 渲染出来之后**才能调。 */
async function renderCodes() {
  for (const s of stations.value) {
    const canvas = document.getElementById(`qr-${s.slug}`)
    if (!canvas) continue
    // 票 07：岗位码指向工作台「后勤」组里的阅读页。岗位码从未张贴过，改地址
    // 没有存量风险；但**必须真的改** —— 老地址已经不作路由了（自然 404）。
    const url = `${window.location.origin}${RECIPE_DETAIL_PATH}?slug=${encodeURIComponent(s.slug)}`
    await QRCode.toCanvas(canvas, url, { width: 150, margin: 1 })
  }
}

onMounted(async () => {
  document.title = recipeDocumentTitle('岗位二维码')
  try {
    const data = await api.get('/api/recipes/stations')
    stations.value = data.stations || []
  } catch (e) {
    errorMsg.value = '无法加载岗位列表，请稍后重试'
  } finally {
    loading.value = false
  }
  // 画码要等模板真的渲染出 `.qr-grid`：`loading` 还是 true 时页面走的是
  // 「加载岗位列表…」那一支，`<canvas>` 根本不在 DOM 里，`getElementById` 全返回
  // null、`continue` 掉 —— 7 张码全是空白画布就是这么来的。
  await nextTick()
  if (!errorMsg.value && stations.value.length) await renderCodes()
})

function doPrint() {
  window.print()
}
</script>

<template>
  <div class="qr-page">
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
      <section class="qr-page-body">
        <!-- 页头：kicker / H1 / 右侧读数 + 主动作 -->
        <header class="qr-head">
          <div class="qr-head-copy">
            <p class="qr-kicker">配方 · 岗位码</p>
            <h1 class="page-title">岗位二维码</h1>
            <p class="page-lead">打印后张贴到岗位，扫码查看该岗位配方。</p>
          </div>
          <div class="qr-head-side no-print">
            <span class="qr-pill">
              <i class="qr-led sig" aria-hidden="true"></i>{{ stations.length }} 张码
            </span>
            <button type="button" class="print-button" @click="doPrint"><RecipeNavIcon name="printer" :size="14" />打印</button>
          </div>
        </header>

        <div v-if="loading" class="loading-state">加载岗位列表…</div>
        <div v-else-if="errorMsg" class="empty-state">{{ errorMsg }}</div>
        <div v-else-if="!stations.length" class="empty-state">暂无岗位。可在配方管理里新增。</div>
        <!-- v3：屏幕版式 = A4 纸面预览（与打印预览共用同一张纸的观感） -->
        <section v-else class="qr-sheet no-print" aria-label="岗位码纸面预览">
          <div class="qr-grid">
            <div v-for="s in stations" :key="s.slug" class="qr-card">
              <div class="qr-card-title">{{ s.title }}</div>
              <div class="qr-card-box"><canvas :id="`qr-${s.slug}`"></canvas></div>
            </div>
          </div>
        </section>
      </section>
    </main>
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
/* 标题用正文色 + B 案的 600 权重 / .01em 字距（`recipe.css` 旧皮是 900 重标题）。 */
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

.qr-page {
  padding-bottom: calc(40px + var(--safe-b));
}
.qr-page-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.qr-head {
  display: flex;
  align-items: flex-end;
  gap: 16px;
}
.qr-head-copy {
  flex: 1 1 auto;
  min-width: 0;
}
.qr-kicker {
  margin: 0 0 7px;
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .26em;
  color: var(--ink-faint);
}
.qr-head-side {
  margin-left: auto;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.qr-pill {
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
.qr-led {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
  flex: 0 0 6px;
}
.qr-led.ok { background: var(--ok); box-shadow: 0 0 0 3px var(--wb-ok-a16); }
.qr-led.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.qr-led.bad { background: var(--danger); box-shadow: 0 0 0 3px var(--wb-danger-a16); }
.qr-led.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }

/* ── v3：屏幕版式 = A4 纸面预览 ─────────────────────────────────────────
   一张 210×297mm 的纸浮在钢面底上，码按 3 列排在纸里；站名在码上方。
   **二维码底版不动**（`recipe.css` 的 `.qr-card-box` 是纯白）：扫码要真白底。
   整段圈在 `@media screen`，打印仍是白纸黑码（`recipe.css` 的 `@media print` 管）。 */
@media screen {
  .qr-sheet {
    margin: 0 auto;
    width: 210mm;
    min-height: 297mm;
    box-sizing: border-box;
    padding: 12mm 10mm;
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-radius: var(--r-1);
    box-shadow: var(--hy-shadow-md);
  }
  .qr-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8mm;
    padding: 0;
  }
  .qr-card {
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    padding: 0;
    text-align: center;
  }
  .qr-card-title {
    margin: 0 0 6px;
    color: var(--ink);
    font: 600 14px/1.3 var(--font-sans);
    letter-spacing: .02em;
  }
}

@media (max-width: 720px) {
  .qr-sheet {
    width: 100%;
    min-height: 0;
    padding: 14px;
  }
  .qr-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 14px;
  }
  .qr-head {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
  .qr-head-side {
    margin-left: 0;
  }
}
</style>
