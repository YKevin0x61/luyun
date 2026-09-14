<script setup>
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import SvgIcon from '../../components/SvgIcon.vue'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import {
  HYGIENE_ADMIN_NAV,
  HYGIENE_BACK_TO_ADMIN_LABEL,
  HYGIENE_BRAND_MARK,
  HYGIENE_BRAND_TAGLINE,
  HYGIENE_BRAND_TITLE,
  hygieneDocumentTitle,
} from '../../utils/hygieneCopy'

useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()
const currentNav = computed(() => (
  HYGIENE_ADMIN_NAV.find((item) => route.path === item.path) || HYGIENE_ADMIN_NAV[0]
))

function navIndex(item) {
  return String(HYGIENE_ADMIN_NAV.indexOf(item) + 1).padStart(2, '0')
}

watch(currentNav, (item) => {
  document.title = hygieneDocumentTitle(item.title)
}, { immediate: true })
</script>

<template>
  <div class="hygiene-admin hygiene-app">
    <a class="hy-skip" href="#hygiene-admin-main">跳到内容</a>

    <nav class="hy-tabbar" aria-label="卫生管理">
      <router-link class="hy-brand hy-brand-rail" to="/hygiene-roster">
        <span class="hy-brand-mark" aria-hidden="true">{{ HYGIENE_BRAND_MARK }}</span>
        <span class="hy-brand-text">
          <span class="hy-brand-title">{{ HYGIENE_BRAND_TITLE }}</span>
          <span class="hy-brand-tagline">{{ HYGIENE_BRAND_TAGLINE }}</span>
        </span>
      </router-link>

      <div class="hy-rail-items">
        <router-link
          v-for="item in HYGIENE_ADMIN_NAV"
          :key="item.path"
          class="hy-tab"
          :to="item.path"
        >
          <SvgIcon :name="item.icon" :size="19" />
          <span class="hy-tab-full">{{ item.title }}</span>
          <span class="hy-tab-short">{{ item.shortTitle }}</span>
          <span class="hy-rail-index">{{ navIndex(item) }}</span>
        </router-link>
      </div>

      <div class="hy-rail-foot">
        <p class="hy-rail-note">对照实拍 · 当日验收<br>INSPECTION DESK</p>
        <router-link class="hy-back" to="/" :title="HYGIENE_BACK_TO_ADMIN_LABEL">
          {{ HYGIENE_BACK_TO_ADMIN_LABEL }}
        </router-link>
      </div>
    </nav>

    <div class="hy-shell">
      <header class="hy-header">
        <div class="hy-header-inner">
          <router-link class="hy-brand hy-brand-top" to="/hygiene-roster">
            <span class="hy-brand-mark" aria-hidden="true">{{ HYGIENE_BRAND_MARK }}</span>
            <span class="hy-brand-text">
              <span class="hy-brand-title">{{ HYGIENE_BRAND_TITLE }}</span>
              <span class="hy-brand-tagline">{{ HYGIENE_BRAND_TAGLINE }}</span>
            </span>
          </router-link>
          <p class="hy-crumb">
            {{ currentNav.title }}
            <code>{{ navIndex(currentNav) }} · {{ currentNav.code }}</code>
          </p>
          <router-link class="hy-back hy-back-top" to="/" :title="HYGIENE_BACK_TO_ADMIN_LABEL">
            {{ HYGIENE_BACK_TO_ADMIN_LABEL }}
          </router-link>
        </div>
      </header>
      <main id="hygiene-admin-main" class="hy-main">
        <router-view />
      </main>
    </div>
  </div>
</template>
