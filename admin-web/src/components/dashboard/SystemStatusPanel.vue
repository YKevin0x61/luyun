<script setup>
import SvgIcon from '../SvgIcon.vue'

const props = defineProps({
  summary: { type: Object, default: null },
})

// 与旧 public/index.html loadDashboard() 内存告警配色阈值一致（L1569：pct>80 danger / pct>60 warn）。
const MEM_WARN_PERCENT = 60
const MEM_DANGER_PERCENT = 80

function formatUptime(seconds) {
  if (!seconds) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}

function formatDatetime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

function formatMb(value) {
  return value != null ? `${Number(value).toFixed(1)} MB` : '—'
}

function memPressureClass(pct) {
  if (pct > MEM_DANGER_PERCENT) return 'danger'
  if (pct > MEM_WARN_PERCENT) return 'warn'
  return ''
}
</script>

<template>
  <div class="card">
    <div class="panel-title" style="justify-content:space-between">
      <span><SvgIcon name="settings" :size="16" /> 系统状态</span>
      <span class="badge">v{{ summary?.system?.version || '0.0.1' }}</span>
    </div>
    <div style="display:flex;flex-direction:column;gap:6px;font-size:12px">
      <div style="display:flex;align-items:center;gap:8px">
        <span style="color:var(--text-dim);width:70px;flex-shrink:0">内存</span>
        <div class="bar-track" style="flex:1">
          <div
            class="bar-fill"
            :class="memPressureClass(summary?.system?.memory?.current_usage?.percent ?? 0)"
            :style="{ width: (summary?.system?.memory?.current_usage?.percent ?? 0) + '%' }"
          ></div>
        </div>
        <span style="width:44px;text-align:right">{{ (summary?.system?.memory?.current_usage?.percent ?? 0).toFixed(1) }}%</span>
      </div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">系统内存</span>
        <span>{{ formatMb(summary?.system?.memory?.current_usage?.available_mb) }} 可用 / {{ formatMb(summary?.system?.memory?.current_usage?.total_mb) }}</span>
      </div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">进程内存</span>
        <span>{{ formatMb(summary?.system?.memory?.current_usage?.rss_mb) }} RSS</span>
      </div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">峰值内存</span>
        <span>{{ formatMb(summary?.system?.memory?.peak_memory_mb) }}</span>
      </div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">数据库</span>
        <span>{{ summary?.system?.database?.orders?.count ?? '—' }} 条订单</span>
      </div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">菜品映射</span>
        <span>{{ summary?.system?.database?.dish_stations?.count ?? '—' }} 条</span>
      </div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">运行时长</span>
        <span>{{ formatUptime(summary?.system?.uptime) }}</span>
      </div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">采集 API 失败</span>
        <span :class="summary?.data_quality?.api_failures > 0 ? 'warn' : ''">
          {{ summary?.data_quality?.api_failures ?? 0 }}
        </span>
      </div>
      <div v-if="summary?.data_quality?.last_reconcile" style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">对账漏抓</span>
        <span :class="(summary?.data_quality?.last_reconcile?.missed_qty ?? 0) > 0 ? 'warn' : 'ok'">
          {{ summary?.data_quality?.last_reconcile?.missed_qty ?? 0 }} 份 / {{ summary?.data_quality?.last_reconcile?.missed_keys ?? 0 }} 键
          ({{ (summary?.data_quality?.last_reconcile?.miss_rate_pct ?? 0).toFixed(2) }}%)
        </span>
      </div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">日终对账</span>
        <span>{{ summary?.data_quality?.reconcile_running ? '进行中' : (summary?.data_quality?.last_reconcile ? '已完成' : '未运行') }}</span>
      </div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">最近一次采集</span>
        <span>{{ formatDatetime(summary?.data_quality?.last_scrape_at) }}</span>
      </div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:var(--text-dim)">最后更新</span>
        <span>{{ formatDatetime(summary?.timestamp) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.warn { color: var(--yellow); }
.danger { color: var(--red); }
.ok { color: var(--green); }
.bar-fill.warn { background: var(--yellow); }
.bar-fill.danger { background: var(--red); }
</style>
