<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useNudgePull } from '../../composables/useNudgePull'
import SvgIcon from '../SvgIcon.vue'
import LuyunDatePicker from '../ui/LuyunDatePicker.vue'

const emit = defineEmits(['close'])

const loading = ref(true)
const error = ref('')
const health = ref({})
const reconcileDate = ref('')
const starting = ref(false)

const progress = ref({ running: false, biz_date: null, stage: null, stage_label: '', current: 0, total: 0, error: null })
const result = ref(null)
const resultError = ref('')
let pollTimer = null

const sortedDiffs = computed(() => {
  const diffs = result.value?.diffs || []
  return [...diffs].sort((a, b) => (b.missed_qty ?? 0) - (a.missed_qty ?? 0))
})

const progressPercent = computed(() => {
  const p = progress.value
  if (p.stage === 'done') return 100
  if (p.total > 0) return Math.min(100, Math.round((p.current / p.total) * 100))
  return p.running ? 8 : 0
})

const busy = computed(() => starting.value || progress.value.running)

function fmtTime(v) {
  if (!v) return '—'
  try {
    return new Date(v).toLocaleString('zh-CN', { hour12: false })
  } catch (_) {
    return String(v)
  }
}

function missLine(lastReconcile) {
  const lr = lastReconcile || {}
  if (lr.missed_qty == null) return '尚未对账'
  const rate = Number(lr.miss_rate_pct ?? 0).toFixed(2)
  return `${lr.missed_qty} 份 / ${lr.missed_keys ?? 0} 键，漏抓率 ${rate}%`
}

async function loadHealth() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/api/admin/scraper-health')
    health.value = data.health || {}
    if (!reconcileDate.value) reconcileDate.value = health.value.biz_date || ''
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(loadProgress, 2000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function loadProgress() {
  try {
    const data = await api.get('/api/admin/reconcile-status')
    const wasRunning = progress.value.running
    progress.value = data.progress || {}
    if (progress.value.running) {
      if (!pollTimer) startPolling()
    } else {
      stopPolling()
      if (wasRunning) await onReconcileFinished()
    }
  } catch (_) {
    // 轮询失败不打断交互，等待下一次轮询/nudge 重试
  }
}

async function onReconcileFinished() {
  await loadHealth()
  if (progress.value.stage === 'error') {
    resultError.value = progress.value.error || '对账失败'
    result.value = null
    return
  }
  resultError.value = ''
  const bizDate = progress.value.biz_date || reconcileDate.value
  try {
    const data = await api.get('/api/admin/reconcile-result', { date: bizDate })
    if (data.success) {
      result.value = data.result
    } else {
      resultError.value = data.error || '未获取到对账结果'
    }
  } catch (e) {
    resultError.value = e.message || '对账结果加载失败'
  }
}

async function startReconcile() {
  error.value = ''
  resultError.value = ''
  result.value = null
  starting.value = true
  try {
    await api.post('/api/admin/reconcile', { date: reconcileDate.value || null, fix: false, notify: true })
    await loadProgress()
  } catch (e) {
    error.value = e.message || '启动失败'
  } finally {
    starting.value = false
  }
}

async function fixMissing() {
  const bizDate = progress.value.biz_date || reconcileDate.value || '当前营业日'
  if (!window.confirm(`确认对 ${bizDate} 的漏抓差异进行修复？将补写漏抓订单，并重新对账一次。`)) return
  error.value = ''
  resultError.value = ''
  starting.value = true
  try {
    await api.post('/api/admin/reconcile', { date: progress.value.biz_date || reconcileDate.value || null, fix: true, notify: true })
    await loadProgress()
  } catch (e) {
    error.value = e.message || '启动失败'
  } finally {
    starting.value = false
  }
}

// 对账进行中的 2s 轮询仍留在组件内；此处只负责 admin/reconcile nudge → loadProgress
useNudgePull({
  id: 'admin-reconcile-progress',
  topics: ['admin'],
  pull: loadProgress,
  match: (ev) => ev.type === 'nudge' && ev.topic === 'admin' && ev.scope?.kind === 'reconcile',
})

onMounted(() => {
  loadHealth()
  loadProgress()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-box" style="width: min(720px, 100%)">
      <div class="modal-header">
        <h3>数据质量</h3>
        <button class="btn btn-sm" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>

      <div v-if="loading" class="loading-state">加载中...</div>
      <div v-else-if="error && !health.biz_date" class="empty-state">{{ error }}</div>
      <template v-else>
        <div style="background: var(--card2); border-radius: 8px; padding: 12px; line-height: 1.8; font-size: 12px; margin-bottom: 12px">
          <div><b>营业日</b> {{ health.biz_date || '—' }}</div>
          <div>
            <b>API 失败次数</b>
            <span :style="(health.api_failures || 0) > 0 ? 'color: var(--yellow)' : 'color: var(--green)'">
              {{ health.api_failures ?? 0 }}
            </span>
          </div>
          <div><b>最后采集</b> {{ fmtTime(health.last_scrape_at) }}</div>
          <div><b>最后对账</b> {{ fmtTime(health.last_reconcile?.at) }} — {{ missLine(health.last_reconcile) }}</div>
          <div v-if="health.last_reconcile?.report_md" style="margin-top: 6px; color: var(--text-dim)">
            报告：<code style="font-size: 11px">{{ health.last_reconcile.report_md }}</code>
          </div>
        </div>

        <div class="form-row">
          <label>对账日期</label>
          <LuyunDatePicker v-model="reconcileDate" :disabled="busy" />
        </div>

        <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px">
          <button class="btn" :disabled="loading" @click="loadHealth">刷新健康状态</button>
          <button class="btn btn-primary" :disabled="busy" @click="startReconcile">
            {{ progress.running ? '对账进行中...' : (starting ? '启动中...' : '开始对账') }}
          </button>
        </div>

        <div v-if="progress.running || progress.stage" style="margin-top: 12px">
          <div class="bar-row" style="grid-template-columns: 100px 1fr 70px">
            <span class="bar-label">{{ progress.stage_label || '—' }}</span>
            <div class="bar-track">
              <div
                class="bar-fill"
                :class="{ danger: progress.stage === 'error' }"
                :style="{ width: progressPercent + '%' }"
              ></div>
            </div>
            <span class="bar-value">{{ progress.total ? `${progress.current}/${progress.total}` : '' }}</span>
          </div>
        </div>

        <div v-if="resultError" style="color: var(--red); font-size: 12px; margin-top: 10px">{{ resultError }}</div>

        <div v-if="result" style="margin-top: 12px">
          <div style="font-size: 12px; color: var(--text-dim)">
            {{ result.biz_date }} — 漏抓 {{ result.missed_qty }} 份 / {{ result.missed_keys }} 键，
            漏抓率 {{ Number(result.miss_rate_pct ?? 0).toFixed(2) }}%，影响 {{ result.affected_bills }} 单
          </div>

          <div v-if="sortedDiffs.length" style="margin-top: 8px">
            <button class="btn btn-primary" :disabled="busy" @click="fixMissing">
              修复漏抓（{{ sortedDiffs.length }} 项）
            </button>
            <div class="data-table-wrap" style="max-height: 320px; overflow-y: auto; margin-top: 8px">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>账单号</th>
                    <th>菜品</th>
                    <th>类型</th>
                    <th>网页数量</th>
                    <th>DB数量</th>
                    <th>漏抓数量</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in sortedDiffs" :key="`${item.bs_code}_${item.dish_name}`">
                    <td>{{ item.bs_code }}</td>
                    <td>{{ item.dish_name }}</td>
                    <td>{{ item.diff_type === 'full' ? '完全漏抓' : '部分漏抓' }}</td>
                    <td>{{ item.pos_qty }}</td>
                    <td>{{ item.db_qty }}</td>
                    <td>{{ item.missed_qty }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div v-else style="color: var(--green); font-size: 12px; margin-top: 8px">无差异，对账通过 ✓</div>
        </div>

        <div v-if="error" style="color: var(--red); font-size: 12px; margin-top: 8px">{{ error }}</div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.bar-fill.danger { background: var(--red); }
</style>
