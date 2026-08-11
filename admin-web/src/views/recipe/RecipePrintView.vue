<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'

useScopedStylesheet('/recipe.css')

const route = useRoute()
const slugs = computed(() =>
  String(route.query.slugs || route.query.slug || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
)

const bodyHtml = ref('加载中…')
const pageTitle = ref('打印预览')
const backHref = computed(() => (slugs.value.length === 1 ? `/recipe/detail?slug=${encodeURIComponent(slugs.value[0])}` : '/recipe'))

onMounted(async () => {
  document.body.classList.add('sop-print-preview-page')
  if (!slugs.value.length) {
    bodyHtml.value = '未指定岗位'
    return
  }
  try {
    const parts = []
    for (const slug of slugs.value) {
      try {
        const data = await api.get(`/api/recipes/stations/${encodeURIComponent(slug)}`)
        parts.push(`<section class="sop-print-station">${data.content_html}</section>`)
      } catch (e) { /* 单个岗位加载失败时跳过，不阻塞其余岗位 */ }
    }
    if (!parts.length) {
      bodyHtml.value = '未找到岗位'
      return
    }
    bodyHtml.value = parts.join('<div class="sop-print-page-break"></div>')
    pageTitle.value = slugs.value.length === 1 ? '打印预览' : `批量打印 · ${slugs.value.length} 个岗位`
    document.title = pageTitle.value
  } catch (e) {
    bodyHtml.value = '加载失败'
  }
})
onBeforeUnmount(() => {
  document.body.classList.remove('sop-print-preview-page')
})

function doPrint() {
  window.focus()
  window.print()
}
</script>

<template>
  <div>
    <header class="sop-print-preview-toolbar no-print">
      <span class="sop-print-preview-title">{{ pageTitle }}</span>
      <span class="sop-print-preview-spacer"></span>
      <button type="button" class="btn btn-primary" @click="doPrint">打印</button>
      <router-link class="btn btn-ghost" to="/">返回主页</router-link>
      <router-link class="btn btn-ghost" :to="backHref">关闭</router-link>
    </header>
    <div class="sop-print-preview-sheet-wrap">
      <div class="sop-print-preview-sheet sop-panel">
        <div class="sop-body markdown-body" v-html="bodyHtml"></div>
      </div>
    </div>
  </div>
</template>
