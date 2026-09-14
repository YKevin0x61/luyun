<script setup>
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import {
  HYGIENE_BRAND_MARK,
  HYGIENE_BRAND_TAGLINE,
  HYGIENE_BRAND_TITLE,
  hygieneDocumentTitle,
} from '../../utils/hygieneCopy'

useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()
const pageTitle = computed(() => route.meta.staffPageTitle || HYGIENE_BRAND_TITLE)

watch(pageTitle, (title) => {
  document.title = hygieneDocumentTitle(title)
}, { immediate: true })
</script>

<template>
  <div class="hygiene-staff">
    <a class="hy-skip" href="#hygiene-staff-main">跳到内容</a>
    <main id="hygiene-staff-main" class="hy-staff-main">
      <div class="hy-staff-card">
        <div class="hy-brand hy-staff-brand">
          <span class="hy-brand-mark" aria-hidden="true">{{ HYGIENE_BRAND_MARK }}</span>
          <span class="hy-brand-text">
            <span class="hy-brand-title">{{ HYGIENE_BRAND_TITLE }}</span>
            <span class="hy-brand-tagline">{{ HYGIENE_BRAND_TAGLINE }}</span>
          </span>
        </div>
        <router-view />
      </div>
    </main>
  </div>
</template>
