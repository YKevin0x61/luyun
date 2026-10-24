<script setup>
/**
 * 工作区 · 日常清单（手机独立页，2026-10-19）。
 *
 * 桌面端这一块住在 `/workbench/floor/zones` 的右栏；手机端两栏放不下，改成这一页：
 * `?zone=<id>` 选工作区（缺省第一个），主体只有清单面板 —— 列表 + 编辑标注 / 换标准图 /
 * 删除 + ＋新增检查项 + 编辑区，全部来自 `HygieneZoneItemsPanel`（与桌面同一份）。
 *
 * 路由不在这一票里注册（由 Lead 加 `/workbench/floor/zone-items`）；这里只消费
 * `route.query.zone`，query 变了跟着换选中区。区不存在 / 还没有工作区时给空态 + 回工作区。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api/client'
import HygieneZoneItemsPanel from '../../components/hygiene/HygieneZoneItemsPanel.vue'
import { hygieneDocumentTitle } from '../../utils/hygieneCopy'

const route = useRoute()

const zones = ref([])
const loading = ref(true)
const errorText = ref('')

/** `?zone=<id>`（缺省 null）。数组形式（重复参数）取第一个；非数字当作没给。 */
const requestedId = computed(() => {
  const raw = route.query.zone
  const value = Array.isArray(raw) ? raw[0] : raw
  if (value == null || value === '') return null
  const id = Number(value)
  return Number.isFinite(id) ? id : null
})

const zone = computed(() => {
  if (!zones.value.length) return null
  return zones.value.find((item) => item.id === requestedId.value) || zones.value[0] || null
})

watch(zone, (current) => {
  document.title = hygieneDocumentTitle(current ? `${current.name} · 日常清单` : '日常清单')
}, { immediate: true })

onMounted(load)

async function load() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/hygiene/admin/zones')
    zones.value = data.zones || []
  } catch (err) {
    zones.value = []
    errorText.value = err.message || '无法加载卫生工作区'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="zone-items-page">
    <header class="wbx-head">
      <div class="wbx-head-main">
        <p class="wbx-kicker">现场 · 工作区</p>
        <h1 class="wbx-title">{{ zone ? zone.name : '日常清单' }}</h1>
      </div>
      <div class="wbx-pills">
        <span class="wbx-read"><span>检查项</span><b>{{ zone ? (zone.items || []).length : 0 }}</b><em>项</em></span>
        <router-link class="btn" to="/workbench/floor/zones">返回工作区</router-link>
      </div>
      <p class="wbx-sub">这个工作区的日常检查项与当前标准图。</p>
    </header>

    <p v-if="errorText" class="wbx-error" role="alert">{{ errorText }}</p>

    <div v-if="loading" class="wbx-empty-bar">正在加载…</div>
    <HygieneZoneItemsPanel v-else-if="zone" :zone="zone" @changed="load" />
    <div v-else class="wbx-empty-page">
      <p>{{ zones.length ? '没有找到这个工作区。' : '还没有卫生工作区。' }}</p>
      <router-link class="btn" to="/workbench/floor/zones">返回工作区</router-link>
    </div>
  </div>
</template>

<style scoped>
/* 手机优先的单列页：页头与其它现场页同一套；主体就是清单面板（组件自带手机布局）。 */
.zone-items-page {
  display: flex; flex-direction: column; gap: 14px;
  padding-bottom: calc(40px + var(--safe-b));
}
.wbx-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 16px; align-items: end; }
.wbx-head-main { grid-column: 1; grid-row: 1; }
.wbx-kicker { margin: 0 0 7px; font: 500 9.5px/1 var(--font-sans); letter-spacing: .26em; color: var(--ink-faint); }
.wbx-title { margin: 0; font: 600 26px/1.1 var(--font-sans); letter-spacing: .01em; color: var(--ink); overflow-wrap: anywhere; }
.wbx-pills { grid-column: 2; grid-row: 1; justify-self: end; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wbx-read { display: inline-flex; align-items: baseline; gap: 6px; font: 500 10px/1 var(--font-mono); color: var(--ink-dim); }
.wbx-read span { font: 500 9.5px/1 var(--font-sans); letter-spacing: .18em; color: var(--ink-faint); }
.wbx-read b { font: 500 20px/1 var(--font-mono); color: var(--ink); }
.wbx-read em { font-style: normal; font-size: 9.5px; color: var(--ink-faint); }
.wbx-sub { grid-column: 1 / -1; grid-row: 2; margin: 8px 0 0; font-size: 11.5px; line-height: 1.6; color: var(--ink-dim); }
.wbx-error { margin: 0; font-size: 11px; color: var(--danger-bright); }
.wbx-empty-bar {
  display: flex; align-items: center; gap: 8px;
  min-height: 44px; margin: 0; padding: 10px 14px;
  font-size: 10.5px; color: var(--ink-faint);
  background: var(--surface-1); border: 1px solid var(--line); border-radius: var(--r-1);
}
.wbx-empty-page {
  display: flex; flex-direction: column; align-items: flex-start; gap: 12px;
  padding: 16px 14px;
  background: var(--surface-1); border: 1px solid var(--line); border-radius: var(--r-2);
}
.wbx-empty-page p { margin: 0; font-size: 11px; color: var(--ink-dim); }
.zone-items-page :deep(.btn) { min-height: 44px !important; border-radius: var(--r-1); }
@media (max-width: 720px) {
  .wbx-head { grid-template-columns: minmax(0, 1fr); }
  .wbx-pills { grid-column: 1; grid-row: 2; justify-self: start; margin-top: 10px; }
  .wbx-sub { grid-row: 3; }
  .wbx-title { font-size: 22px; }
}
</style>
