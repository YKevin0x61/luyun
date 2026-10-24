<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { HYGIENE_SHIFTS } from '../../utils/hygieneCopy'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import HygieneZoneItemsPanel from '../../components/hygiene/HygieneZoneItemsPanel.vue'
import LuyunTimePicker from '../../components/ui/LuyunTimePicker.vue'

const zones = ref([])
const selectedId = ref(null)
const loading = ref(true)
const exporting = ref(false)
// 默认预览图：门店 36 个检查项的原图合计 80+ MB，打包和解码都慢一个量级，
// 而预览图是 1600px，页面显示 440px、打印 A4 都够用。
const exportSize = ref('preview')
const exportProgress = ref(null)
const errorText = ref('')
const newZoneName = ref('')
const newZoneShifts = ref([...HYGIENE_SHIFTS])
const creatingZone = ref(false)
const zoneShifts = ref([])
const savingShifts = ref(false)
const shiftsHint = ref('')
const dayClock = ref('15:00')
const nightClock = ref('21:30')
const savingClocks = ref(false)
const clocksHint = ref('')
const boards = ref({ zones: [] })
const deleteZoneTarget = ref(null)

const selected = computed(() => {
  return zones.value.find((zone) => zone.id === selectedId.value) || zones.value[0] || null
})

watch(selected, (zone) => {
  zoneShifts.value = zone ? [...(zone.shifts || HYGIENE_SHIFTS)] : []
  shiftsHint.value = ''
}, { immediate: true })

const missedByZone = computed(() => {
  const counts = {}
  for (const row of boards.value.zones || []) {
    if (row.zone_id == null) continue
    counts[row.zone_id] = row['逾期'] || 0
  }
  return counts
})

onMounted(() => {
  refreshPage()
})

useHygieneRealtime({
  id: 'hygiene-admin-zones',
  resources: ['zones', 'daily', 'fix', 'settings'],
  pull: refreshPage,
})

async function refreshPage() {
  await Promise.all([loadZones(), loadClocks(), loadBoardPreview()])
}

/**
 * 导出标准图：服务端把圆圈/箭头/批注烘焙进图片，按工作区分成子文件夹打包。
 *
 * 走任务式而不是一个同步请求：几十张图要解码、绘制、重编码（有的门店上百 MB），
 * 同步请求期间界面上什么都没有，员工只会以为按钮没反应。这里先 POST 拿 job_id，
 * 再按 done/total 轮询打包进度，打完才去下载。
 */
async function exportStandards() {
  if (exporting.value) return
  exporting.value = true
  errorText.value = ''
  exportProgress.value = { done: 0, total: 0, phase: 'packing' }
  try {
    const job = await api.post(
      `/api/hygiene/admin/standards-export/jobs?size=${exportSize.value}`,
    )
    for (;;) {
      await new Promise((resolve) => { setTimeout(resolve, 700) })
      const state = await api.get(`/api/hygiene/admin/standards-export/jobs/${job.job_id}`)
      if (state.state === 'failed') throw new Error(state.error || '导出失败')
      exportProgress.value = { done: state.done, total: state.total, phase: 'packing' }
      if (state.state === 'done') break
    }
    exportProgress.value = { done: 0, total: 0, phase: 'downloading' }
    await api.download(
      `/api/hygiene/admin/standards-export/jobs/${job.job_id}/download`,
      'hygiene-standards.zip',
    )
  } catch (err) {
    errorText.value = err.message || '导出标准图失败'
  } finally {
    exporting.value = false
    exportProgress.value = null
  }
}

const exportPercent = computed(() => {
  const progress = exportProgress.value
  if (!progress || !progress.total) return 0
  return Math.min(100, Math.round((progress.done / progress.total) * 100))
})

const exportLabel = computed(() => {
  const progress = exportProgress.value
  if (!exporting.value || !progress) return '导出标准图'
  if (progress.phase === 'downloading') return '正在下载…'
  if (progress.total) return `正在打包 ${progress.done}/${progress.total}…`
  return '正在准备…'
})

async function loadZones() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/hygiene/admin/zones')
    zones.value = data.zones || []
    if (!zones.value.some((zone) => zone.id === selectedId.value)) {
      selectedId.value = zones.value[0] ? zones.value[0].id : null
    }
  } catch (err) {
    errorText.value = err.message || '无法加载卫生工作区'
  } finally {
    loading.value = false
  }
}

function toHhmm(raw) {
  const text = String(raw || '').trim()
  return text.length >= 5 ? text.slice(0, 5) : text
}

async function loadClocks() {
  try {
    const data = await api.get('/api/hygiene/admin/overdue-clocks')
    dayClock.value = toHhmm(data.day_hhmm) || '15:00'
    nightClock.value = toHhmm(data.night_hhmm) || '21:30'
  } catch (err) {
    errorText.value = err.message || '无法加载日常逾期点'
  }
}

async function loadBoardPreview() {
  try {
    boards.value = await api.get('/api/hygiene/admin/boards')
  } catch (_err) {
    boards.value = { zones: [] }
  }
}

async function saveClocks() {
  if (savingClocks.value) return
  savingClocks.value = true
  clocksHint.value = ''
  errorText.value = ''
  try {
    const data = await api.patch('/api/hygiene/admin/overdue-clocks', {
      day_hhmm: toHhmm(dayClock.value),
      night_hhmm: toHhmm(nightClock.value),
    })
    dayClock.value = data.day_hhmm
    nightClock.value = data.night_hhmm
    clocksHint.value = '已保存。到点仍未交会发企微群文字，已交待验不算逾期。'
  } catch (err) {
    errorText.value = err.message || '无法保存日常逾期点'
  } finally {
    savingClocks.value = false
  }
}


function selectZone(zone) {
  selectedId.value = zone.id
}

function zoneShiftLabel(zone) {
  const shifts = (zone && zone.shifts) || []
  return shifts.length ? shifts.join(' · ') : HYGIENE_SHIFTS.join(' · ')
}

async function createZone() {
  const name = newZoneName.value.trim()
  if (!name || creatingZone.value) return
  if (!newZoneShifts.value.length) {
    errorText.value = '卫生工作区至少要有一个班次'
    return
  }
  creatingZone.value = true
  errorText.value = ''
  try {
    const data = await api.post('/api/hygiene/admin/zones', {
      name,
      shifts: newZoneShifts.value,
    })
    newZoneName.value = ''
    newZoneShifts.value = [...HYGIENE_SHIFTS]
    await loadZones()
    if (data.zone) selectedId.value = data.zone.id
  } catch (err) {
    errorText.value = err.message || '无法新增卫生工作区'
  } finally {
    creatingZone.value = false
  }
}

async function saveZoneShifts() {
  if (!selected.value || savingShifts.value) return
  if (!zoneShifts.value.length) {
    errorText.value = '卫生工作区至少要有一个班次'
    return
  }
  savingShifts.value = true
  shiftsHint.value = ''
  errorText.value = ''
  try {
    const data = await api.patch(`/api/hygiene/admin/zones/${selected.value.id}`, {
      shifts: zoneShifts.value,
    })
    const index = zones.value.findIndex((zone) => zone.id === data.zone.id)
    if (index >= 0) {
      zones.value[index].shifts = [...data.zone.shifts]
    }
    zoneShifts.value = [...data.zone.shifts]
    shiftsHint.value = '已保存。该工作区只会出现在选中的班次。'
  } catch (err) {
    errorText.value = err.message || '无法保存工作区班次'
  } finally {
    savingShifts.value = false
  }
}

async function confirmDeleteZone() {
  const zone = deleteZoneTarget.value
  deleteZoneTarget.value = null
  if (!zone) return
  errorText.value = ''
  try {
    await api.delete(`/api/hygiene/admin/zones/${zone.id}`)
    if (selectedId.value === zone.id) selectedId.value = null
    await loadZones()
  } catch (err) {
    errorText.value = err.message || '无法删除卫生工作区'
  }
}

</script>

<template>
  <div class="zones-page">
    <header class="wbx-head">
      <div class="wbx-head-main">
        <p class="wbx-kicker">ZONES · 工作区</p>
        <h1 class="wbx-title">卫生工作区</h1>
      </div>
      <div class="wbx-pills">
        <!-- 两个选项做成 chip：原生 select 的弹层由系统画、方向不可控（2026-10-19 截图里
             向上弹盖住了标题）。分段控件零弹层，语义也更直白。 -->
        <div class="export-size" role="group" aria-label="导出图片规格">
          <span>图片</span>
          <button
            class="export-chip"
            type="button"
            :class="{ on: exportSize === 'preview' }"
            :aria-pressed="exportSize === 'preview' ? 'true' : 'false'"
            :disabled="exporting"
            title="1600px 宽，导出快（门店 36 项约 8MB）"
            @click="exportSize = 'preview'"
          >预览图</button>
          <button
            class="export-chip"
            type="button"
            :class="{ on: exportSize === 'original' }"
            :aria-pressed="exportSize === 'original' ? 'true' : 'false'"
            :disabled="exporting"
            title="原始分辨率，最清晰但导出慢（原图合计 80MB+）"
            @click="exportSize = 'original'"
          >原图</button>
        </div>
        <button type="button" class="btn" :disabled="exporting" @click="exportStandards">{{ exportLabel }}</button>
        <button type="button" class="btn" :disabled="loading" @click="refreshPage">刷新</button>
      </div>
      <p class="wbx-sub">维护各卫生工作区的日常检查项和当前标准图。</p>
      <details class="rule-help">
        <summary>规则说明</summary>
        <p>卫生工作区与档口、配方岗位相互独立。每个工作区可选白班、夜班或只跑其中一个班次；没有当前标准图的检查项不会出现在员工端；更换标准图后，新检查使用新图。删除工作区或检查项会同时删除进行中的待办和该区未闭环整改单。</p>
      </details>
    </header>

    <div
      v-if="exporting && exportProgress && exportProgress.total"
      class="export-progress"
      role="status"
      :aria-label="exportLabel"
    >
      <div class="export-progress-bar" :style="{ width: exportPercent + '%' }"></div>
    </div>

    <p v-if="errorText" class="wbx-error roster-error" role="alert">{{ errorText }}</p>

    <div class="wbx-readline">
      <span class="wbx-read"><span>工作区</span><b>{{ zones.length }}</b></span>
      <span class="wbx-read"><span>检查项</span><b>{{ zones.reduce((n, z) => n + (z.items || []).length, 0) }}</b></span>
      <span class="wbx-read"><span>日常逾期点</span><b class="is-clock">{{ dayClock }} / {{ nightClock }}</b></span>
      <span class="wbx-read is-unit"><em>白班 · 夜班 · 到点未交才提醒</em></span>
    </div>

    <section class="wbx-panel">
      <header class="wbx-ph">
        <i class="led warn"></i>
        <h2>日常逾期点</h2>
        <span class="wbx-hint">全店两个钟点</span>
      </header>
      <form class="wbx-clocks" @submit.prevent="saveClocks">
        <label class="wbx-field" aria-label="白班日常逾期点">
          <span>白班</span>
          <LuyunTimePicker v-model="dayClock" />
        </label>
        <label class="wbx-field" aria-label="夜班日常逾期点">
          <span>夜班</span>
          <LuyunTimePicker v-model="nightClock" />
        </label>
        <button type="submit" class="btn btn-primary" :disabled="savingClocks">保存逾期点</button>
        <p v-if="clocksHint" class="wbx-lead">{{ clocksHint }}</p>
      </form>
    </section>

    <div class="wbx-cols">
      <section class="wbx-panel">
        <header class="wbx-ph">
          <i class="led sig"></i>
          <h2>工作区</h2>
          <span class="wbx-hint">{{ zones.length }} 个</span>
        </header>
        <div v-if="loading" class="wbx-empty-bar">正在加载…</div>
        <ul v-else-if="zones.length" class="zone-list">
          <li v-for="zone in zones" :key="zone.id" class="zone-row">
            <button
              type="button"
              class="zone-btn"
              :class="{ active: selected && selected.id === zone.id }"
              @click="selectZone(zone)"
            >
              <strong>{{ zone.name }}</strong>
              <span class="wbx-subline">
                {{ (zone.items || []).length }} 项
                <template v-if="missedByZone[zone.id]"> · 漏拍 {{ missedByZone[zone.id] }}</template>
              </span>
              <em class="zone-shifts">{{ zoneShiftLabel(zone) }}</em>
            </button>
            <button
              type="button"
              class="btn btn-sm btn-danger"
              :aria-label="`删除卫生工作区 ${zone.name}`"
              @click="deleteZoneTarget = zone"
            >删除</button>
            <router-link
              class="zone-items-link"
              :to="{ path: '/workbench/floor/zone-items', query: { zone: zone.id } }"
            >查看清单 ›</router-link>
          </li>
        </ul>
        <div v-else class="wbx-empty-bar">还没有工作区，先在下面加一个。</div>

        <!-- 班次编辑从右栏挪进工作区卡：它改的是「这个区跑哪几档」，属于工作区属性 -->
        <form v-if="selected" class="zone-shifts-editor" @submit.prevent="saveZoneShifts">
          <span class="zone-shifts-title">{{ selected.name }} · 班次</span>
          <label
            v-for="shift in HYGIENE_SHIFTS"
            :key="`zone-${shift}`"
            class="zone-shift-toggle"
          >
            <input v-model="zoneShifts" type="checkbox" :value="shift">
            {{ shift }}
          </label>
          <button type="submit" class="btn btn-sm" :disabled="savingShifts">保存班次</button>
          <p v-if="shiftsHint" class="zone-shifts-hint">{{ shiftsHint }}</p>
        </form>

        <form class="zone-add" @submit.prevent="createZone">
          <input
            v-model="newZoneName"
            class="input"
            type="text"
            maxlength="40"
            placeholder="再加一个卫生工作区，比如卫生间"
            aria-label="新卫生工作区名称"
          >
          <div class="zone-shift-picks" role="group" aria-label="新工作区班次">
            <label
              v-for="shift in HYGIENE_SHIFTS"
              :key="`new-${shift}`"
              class="zone-shift-toggle"
            >
              <input v-model="newZoneShifts" type="checkbox" :value="shift">
              {{ shift }}
            </label>
          </div>
          <button type="submit" class="btn btn-primary" :disabled="creatingZone || !newZoneName.trim()">新增</button>
        </form>
      </section>

      <!-- 桌面：右栏清单面板（手机档整块隐藏，改走 /workbench/floor/zone-items 独立页） -->
      <div class="zone-items-wrap">
        <HygieneZoneItemsPanel :zone="selected" @changed="refreshPage" />
      </div>
    </div>

    <ConfirmDialog
      v-if="deleteZoneTarget"
      title="删除卫生工作区"
      :message="`删除「${deleteZoneTarget.name}」会同时删除该区进行中的日常待办和未闭环整改单。`"
      confirm-label="删除"
      danger
      @confirm="confirmDeleteZone"
      @cancel="deleteZoneTarget = null"
    />
  </div>
</template>

<style scoped>
/* B 案 v2 + v3 布局纪律：页头右手只留范围（图片规格）与动作；一条读数行；
   左栏 320（工作区 + 班次），右栏检查项；行高压到 72；新增检查项默认折叠。 */
.zones-page {
  display: flex; flex-direction: column; gap: 14px;
  padding-bottom: calc(40px + var(--safe-b));
}
.wbx-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 16px; align-items: end; }
.wbx-head-main { grid-column: 1; grid-row: 1; }
.wbx-kicker { margin: 0 0 7px; font: 500 9.5px/1 var(--font-sans); letter-spacing: .26em; color: var(--ink-faint); }
.wbx-title { margin: 0; font: 600 26px/1.1 var(--font-sans); letter-spacing: .01em; color: var(--ink); }
.wbx-pills { grid-column: 2; grid-row: 1; justify-self: end; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wbx-sub { grid-column: 1 / -1; grid-row: 2; margin: 8px 0 0; font-size: 11.5px; line-height: 1.6; color: var(--ink-dim); }
.wbx-head .rule-help { grid-column: 1 / -1; grid-row: 3; margin-top: 6px; }
.export-size { display: inline-flex; align-items: center; gap: 6px; font-size: 10px; color: var(--ink-dim); }
.export-chip {
  min-height: 44px; padding: 0 12px; border-radius: var(--r-1);
  background: var(--surface-1); border: 1px solid var(--line-strong); color: var(--ink);
  font: inherit; font-size: 10.5px; cursor: pointer;
}
.export-chip.on {
  background: var(--wb-tint-14); border-color: var(--action); color: var(--action); font-weight: 600;
}
.export-chip:disabled { opacity: .6; cursor: default; }
.wbx-error { margin: 0; font-size: 11px; color: var(--danger-bright); }
.led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; background: var(--line-strong); }
.led.ok { background: var(--ok); }
.led.warn { background: var(--warn); }
.led.bad { background: var(--danger); }
.led.sig { background: var(--action); }
.export-progress { height: 3px; background: var(--line); border-radius: var(--r-full); overflow: hidden; margin: -6px 0 0; }
.export-progress-bar { height: 100%; background: var(--action); transition: width .2s var(--ease); }

.wbx-readline {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 26px;
  padding: 10px 14px;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08);
}
.wbx-read { display: inline-flex; align-items: baseline; gap: 6px; }
.wbx-read span { font: 500 9.5px/1 var(--font-sans); letter-spacing: .18em; color: var(--ink-faint); }
.wbx-read b { font: 500 20px/1 var(--font-mono); color: var(--ink); }
.wbx-read b.is-clock { font-size: 16px; }
.wbx-read em { font-style: normal; font-size: 9.5px; color: var(--ink-faint); }

.wbx-panel {
  display: flex; flex-direction: column;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08); overflow: hidden;
}
.wbx-ph { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.wbx-ph h2 { margin: 0; font: 600 12.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.wbx-hint { margin-left: auto; font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.wbx-empty-bar {
  display: flex; align-items: center; gap: 8px;
  min-height: 44px; margin: 0; padding: 10px 14px;
  font-size: 10.5px; color: var(--ink-faint);
}
.wbx-lead { margin: 0; font-size: 10.5px; line-height: 1.6; color: var(--ink-dim); }
.wbx-subline { font: 500 9.5px/1.5 var(--font-mono); color: var(--ink-faint); }

.wbx-clocks { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px 14px; padding: 12px 14px 14px; }
.wbx-field { display: flex; flex-direction: column; gap: 6px; font-size: 10px; color: var(--ink-dim); }
.wbx-field :deep(.luyun-time-picker) { width: 108px; }

.wbx-cols { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 14px; align-items: stretch; }
.zone-list { list-style: none; margin: 0; padding: 6px 10px; display: flex; flex-direction: column; gap: 4px; }
.zone-row { display: flex; align-items: stretch; gap: 6px; }
.zone-row .zone-btn { flex: 1; }
.zone-row .btn-danger { flex: 0 0 auto; align-self: center; }
.zone-btn {
  display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
  min-height: 56px; padding: 8px 10px; text-align: left; cursor: pointer;
  background: transparent; border: 1px solid transparent; border-radius: var(--r-1); color: var(--ink);
}
.zone-btn:hover { background: var(--surface-2); }
.zone-btn.active { background: var(--surface-2); border-color: var(--action); box-shadow: inset 3px 0 0 var(--action); }
.zone-btn strong { font-size: 11.5px; font-weight: 600; }
.zone-shifts { font-style: normal; font: 500 9px/1.4 var(--font-mono); letter-spacing: .06em; color: var(--action); }

.zone-shifts-editor {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px;
  padding: 10px 12px; border-top: 1px solid var(--wb-ink-a08);
  font-size: 10px; color: var(--ink-dim);
}
.zone-shifts-title { flex: 1 1 100%; font-weight: 600; color: var(--ink); }
.zone-shift-toggle { display: inline-flex; align-items: center; gap: 5px; min-height: 44px; cursor: pointer; white-space: nowrap; }
.zone-shift-toggle input { accent-color: var(--action); }
.zone-shifts-hint { flex: 1 1 100%; margin: 0; font: 500 9.5px/1.5 var(--font-mono); color: var(--action); }
.zone-add {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  padding: 10px 12px 12px; border-top: 1px solid var(--wb-ink-a08); margin-top: auto;
}
.zone-add .input { flex: 1; min-width: 0; }
.zone-shift-picks { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }

/* 右栏清单：桌面是第二栏；≤720 整块隐藏，手机从每张工作区卡片的入口进独立页 */
.zone-items-wrap { display: flex; min-width: 0; }
.zone-items-link {
  display: none;
  align-items: center; justify-content: center;
  min-height: 44px; padding: 0 12px;
  border: 1px solid var(--line-strong); border-radius: var(--r-1);
  background: var(--surface-1); color: var(--ink);
  font-size: 11px; text-decoration: none;
}
.zone-items-link:active { background: var(--surface-2); }

.zones-page :deep(.btn) { min-height: 44px !important; border-radius: var(--r-1); }
@media (max-width: 900px) {
  .wbx-cols { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .zone-items-wrap { display: none; }
  .zone-row { flex-wrap: wrap; }
  .zone-items-link { display: flex; width: 100%; }
  .wbx-head { grid-template-columns: minmax(0, 1fr); }
  .wbx-pills { grid-column: 1; grid-row: 2; justify-self: start; margin-top: 10px; }
  .wbx-sub { grid-row: 3; }
  .wbx-head .rule-help { grid-row: 4; }
  .wbx-title { font-size: 22px; }
}
</style>
