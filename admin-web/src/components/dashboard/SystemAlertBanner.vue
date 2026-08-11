<script setup>
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import SvgIcon from '../SvgIcon.vue'

const MEM_DANGER_PERCENT = 80

const props = defineProps({
  summary: { type: Object, default: null },
})

const dismissed = ref(false)

function buildIssues(summary) {
  if (!summary) return []
  const issues = []
  const memPct = summary.system?.memory?.current_usage?.percent ?? 0
  if (memPct > MEM_DANGER_PERCENT) {
    issues.push(`内存 ${memPct.toFixed(1)}%`)
  }
  const apiFailures = summary.data_quality?.api_failures ?? 0
  if (apiFailures > 0) {
    issues.push(`API 失败 ${apiFailures} 次`)
  }
  const reconcile = summary.data_quality?.last_reconcile
  if ((reconcile?.missed_qty ?? 0) > 0 || (reconcile?.miss_rate_pct ?? 0) > 0) {
    issues.push(`对账漏抓 ${(reconcile?.miss_rate_pct ?? 0).toFixed(2)}%`)
  }
  if (summary.data_quality?.reconcile_running) {
    issues.push('日终对账进行中')
  }
  const scraper = summary.scraper
  if (scraper?.paused) {
    issues.push('采集已暂停')
  } else if (scraper?.status !== 'running') {
    issues.push('采集未运行')
  }
  return issues
}

const issues = computed(() => buildIssues(props.summary))
const issueKey = computed(() => issues.value.join('|'))
const visible = computed(() => issues.value.length > 0 && !dismissed.value)

watch(issueKey, () => {
  dismissed.value = false
})

function dismiss(event) {
  event.preventDefault()
  event.stopPropagation()
  dismissed.value = true
}
</script>

<template>
  <RouterLink v-if="visible" to="/logs" class="dash-system-alert">
    <SvgIcon name="alert-triangle" :size="14" />
    <span class="dash-system-alert-text">{{ issues.join(' · ') }}</span>
    <span class="dash-system-alert-hint">查看日志</span>
    <button type="button" class="dash-system-alert-close" aria-label="关闭" @click="dismiss">×</button>
  </RouterLink>
</template>

<style scoped>
.dash-system-alert {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid rgba(239, 68, 68, 0.45);
  background: rgba(239, 68, 68, 0.12);
  color: var(--red);
  font-size: 12px;
  text-decoration: none;
}
.dash-system-alert:hover {
  border-color: var(--red);
}
.dash-system-alert-text {
  flex: 1;
  min-width: 0;
}
.dash-system-alert-hint {
  color: var(--text-dim);
  font-size: 11px;
  white-space: nowrap;
}
.dash-system-alert-close {
  border: none;
  background: transparent;
  color: inherit;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}
</style>
