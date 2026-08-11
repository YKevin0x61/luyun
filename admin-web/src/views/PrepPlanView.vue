<script setup>
import { computed, onMounted, ref } from 'vue'
import { usePrepPlan } from '../composables/usePrepPlan'
import { useStationsStore } from '../stores/stations'
import LuyunCheckbox from '../components/ui/LuyunCheckbox.vue'

const {
  targetStart, targetEnd, station, busy, statusText, steps, summary, items,
  lowConfidence, missingRules, doneKeys, toggleDone, runAll, runCurrent,
} = usePrepPlan()

const stationsStore = useStationsStore()
onMounted(() => stationsStore.load())

// 备货场景排除楼面（不涉及备货执行），对齐旧页硬编码档口列表
const prepStations = computed(() => stationsStore.list.filter((s) => s.id && s.id !== 'loumian'))

const STATION_ORDER = ['xibing', 'changfen', 'shulong', 'mingdang1', 'mingdang2', 'jianzha', '未分类']

function stationLabel(id) {
  if (id === '未分类') return id
  return stationsStore.nameOf(id)
}

const board = computed(() => {
  const grouped = new Map()
  for (const item of items.value) {
    const sid = (item.station || '').trim() || '未分类'
    if (!grouped.has(sid)) grouped.set(sid, [])
    grouped.get(sid).push(item)
  }
  const sortedStations = Array.from(grouped.keys()).sort((a, b) => {
    const ai = STATION_ORDER.indexOf(a); const bi = STATION_ORDER.indexOf(b)
    const ar = ai === -1 ? 999 : ai; const br = bi === -1 ? 999 : bi
    if (ar !== br) return ar - br
    return a.localeCompare(b)
  })
  return sortedStations.map((sid) => {
    const stationItems = [...grouped.get(sid)].sort((a, b) => Number(b.recommended_qty || 0) - Number(a.recommended_qty || 0))
    const totalRecommended = stationItems.reduce((sum, it) => sum + Math.round(Number(it.recommended_qty || 0)), 0)
    return { stationId: sid, items: stationItems, totalRecommended }
  })
})

function riskClass(level) {
  if (level === 'high') return 'risk-high'
  if (level === 'medium') return 'risk-medium'
  return ''
}

function roundOrZero(v) {
  return Number.isFinite(Number(v)) ? Math.round(Number(v)) : 0
}
</script>

<template>
  <div class="prep-plan">
    <div class="grid grid-2col">
      <div class="card">
        <h2 style="font-size:16px;margin-bottom:4px">备货执行台</h2>
        <p style="color:var(--text-dim);font-size:12px;margin-bottom:12px">
          先配置时间窗，再一键生成执行清单；生成后直接按档口卡片执行。
        </p>
        <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
          <label style="font-size:12px;color:var(--text-dim)">开始</label>
          <input class="input" type="datetime-local" v-model="targetStart" />
          <label style="font-size:12px;color:var(--text-dim)">结束</label>
          <input class="input" type="datetime-local" v-model="targetEnd" />
          <select class="select" v-model="station">
            <option value="">全部后厨</option>
            <option v-for="s in prepStations" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
          <button class="btn btn-primary" :disabled="busy" @click="runAll">一键生成执行清单</button>
          <button class="btn" :disabled="busy" @click="runCurrent">读取最近一次</button>
        </div>
        <div style="margin-top:10px;color:var(--accent);font-size:12px;min-height:18px">{{ statusText }}</div>
      </div>

      <div class="card">
        <div style="display:flex;flex-direction:column;gap:8px">
          <div style="border:1px solid var(--border);background:var(--card2);border-radius:8px;padding:9px">
            <div style="color:var(--accent);font-size:12px">步骤 1 / 3</div>
            <div style="margin-top:4px;font-size:13px">{{ steps.step1 }}</div>
          </div>
          <div style="border:1px solid var(--border);background:var(--card2);border-radius:8px;padding:9px">
            <div style="color:var(--accent);font-size:12px">步骤 2 / 3</div>
            <div style="margin-top:4px;font-size:13px">{{ steps.step2 }}</div>
          </div>
          <div style="border:1px solid var(--border);background:var(--card2);border-radius:8px;padding:9px">
            <div style="color:var(--accent);font-size:12px">步骤 3 / 3</div>
            <div style="margin-top:4px;font-size:13px">{{ steps.step3 }}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:12px">
      <h3 style="font-size:14px;margin-bottom:8px">全局概览</h3>
      <div class="grid" style="grid-template-columns:repeat(5, minmax(0,1fr))">
        <div class="stat-card"><span class="label">计划项</span><span class="value">{{ summary.item_count || 0 }}</span></div>
        <div class="stat-card"><span class="label">缺规则</span><span class="value yellow">{{ summary.missing_rule_count || 0 }}</span></div>
        <div class="stat-card"><span class="label">高风险</span><span class="value red">{{ summary.high_risk_count || 0 }}</span></div>
        <div class="stat-card"><span class="label">临期风险</span><span class="value yellow">{{ summary.expiry_risk_count || 0 }}</span></div>
        <div class="stat-card"><span class="label">浪费风险</span><span class="value red">{{ summary.waste_risk_count || 0 }}</span></div>
      </div>
    </div>

    <div class="card" style="margin-top:12px">
      <h3 style="font-size:14px;margin-bottom:8px">档口执行板（按建议量降序）</h3>
      <div v-if="!board.length" class="empty-state">暂无可执行计划，请先生成清单。</div>
      <div v-else class="grid" style="grid-template-columns:repeat(2, minmax(0,1fr))">
        <details v-for="b in board" :key="b.stationId" open class="station-card">
          <summary class="station-head">
            <span>档口：{{ stationLabel(b.stationId) }}</span>
            <span>{{ b.items.length }} 项 / 建议总量 {{ b.totalRecommended }}</span>
          </summary>
          <div style="max-height:260px;overflow:auto">
            <table class="data-table">
              <thead>
                <tr><th>完成</th><th>备货品</th><th>预测</th><th>建议</th><th>风险</th><th>信心</th></tr>
              </thead>
              <tbody>
                <tr v-for="it in b.items" :key="it.item_name">
                  <td>
                    <LuyunCheckbox
                      :model-value="doneKeys.has(`${b.stationId}:${it.item_name}`)"
                      :aria-label="`标记完成 ${it.item_name}`"
                      @update:model-value="toggleDone(`${b.stationId}:${it.item_name}`, $event)"
                    />
                  </td>
                  <td>{{ it.item_name }}</td>
                  <td>{{ roundOrZero(it.forecast_qty) }}</td>
                  <td><b>{{ roundOrZero(it.recommended_qty) }}</b></td>
                  <td :class="riskClass(it.risk_level)">{{ it.risk_level || '-' }}</td>
                  <td>{{ it.confidence || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </div>

    <div class="card" style="margin-top:12px">
      <details>
        <summary style="cursor:pointer;color:var(--accent);font-size:13px">展开辅助信息（低信心 / 缺规则）</summary>
        <div style="margin:8px 0;font-size:12px;color:var(--text-dim)">
          低信心 {{ lowConfidence.length }} 条，缺规则 {{ missingRules.length }} 条
        </div>
        <h4 style="margin:8px 0 4px;font-size:12px">低信心</h4>
        <ul style="margin:0;padding-left:18px;font-size:12px;color:var(--text-dim)">
          <li v-for="(it, i) in lowConfidence" :key="i">{{ it.item_name }} ({{ it.unit || '-' }}) - {{ it.reason || '' }}</li>
        </ul>
        <h4 style="margin:8px 0 4px;font-size:12px">缺失规则</h4>
        <ul style="margin:0;padding-left:18px;font-size:12px;color:var(--text-dim)">
          <li v-for="(it, i) in missingRules" :key="i">{{ it.dish_name }} @ {{ it.station || '-' }} - {{ it.reason || '' }}</li>
        </ul>
      </details>
    </div>
  </div>
</template>

<style scoped>
.station-card { border: 1px solid var(--border); border-radius: 10px; background: var(--card2); overflow: hidden; }
.station-head { display: flex; justify-content: space-between; align-items: center; padding: 10px; background: var(--card); color: var(--text); font-size: 13px; cursor: pointer; }
.risk-high { color: var(--red); font-weight: 600; }
.risk-medium { color: var(--yellow); font-weight: 600; }
@media (max-width: 1024px) {
  .prep-plan .grid { grid-template-columns: 1fr !important; }
}
</style>
