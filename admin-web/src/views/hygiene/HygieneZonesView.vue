<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { useImageUploadQueueStore } from '../../stores/imageUploadQueue'
import { HYGIENE_SHIFTS } from '../../utils/hygieneCopy'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import LuyunTimePicker from '../../components/ui/LuyunTimePicker.vue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import {
  createArrowMark,
  createCaptionMark,
  createCircleMark,
  parseMarkup,
  standardImageUrl,
} from '../../utils/hygieneMarkup'

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
const deleteItemTarget = ref(null)

const itemName = ref('')
const file = ref(null)
const previewUrl = ref('')
const markup = ref([])
const mode = ref('circle')
const captionText = ref('')
const arrowStart = ref(null)
const replacingId = ref(null)
// 「编辑标注」模式：不换图，只改圈注/箭头/批注。服务层会新插一版标准并把
// current_standard_id 指过去，历史冻结标准不动。
const markupEditing = ref(false)
const savingMarkup = ref(false)
const imageUploads = useImageUploadQueueStore()

const selected = computed(() => {
  return zones.value.find((zone) => zone.id === selectedId.value) || zones.value[0] || null
})

watch(selected, (zone) => {
  zoneShifts.value = zone ? [...(zone.shifts || HYGIENE_SHIFTS)] : []
  shiftsHint.value = ''
}, { immediate: true })

const replacingItem = computed(() => {
  if (!replacingId.value || !selected.value) return null
  return (selected.value.items || []).find((item) => item.id === replacingId.value) || null
})

const editorSrc = computed(() => {
  if (previewUrl.value) return previewUrl.value
  if (replacingItem.value) return standardImageUrl('admin', replacingItem.value)
  return ''
})

const canSave = computed(() => {
  if (!selected.value) return false
  // 只改标注时不需要选文件——这正是这个模式存在的理由。
  if (markupEditing.value) return Boolean(replacingId.value)
  if (!file.value) return false
  if (replacingId.value) return true
  return Boolean(itemName.value.trim())
})

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

onBeforeUnmount(clearPreview)

useHygieneRealtime({
  id: 'hygiene-admin-zones',
  resources: ['zones', 'daily', 'fix', 'settings'],
  pull: refreshPage,
})

async function refreshPage() {
  await Promise.all([loadZones(), loadClocks(), loadBoardPreview()])
}

/**
 * 导出标准图：服务端把圆圈/箭头/批注烘焙进图片，按责任区分成子文件夹打包。
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
    errorText.value = err.message || '无法加载卫生责任区'
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
  clearEditor()
}

function zoneShiftLabel(zone) {
  const shifts = (zone && zone.shifts) || []
  return shifts.length ? shifts.join(' · ') : HYGIENE_SHIFTS.join(' · ')
}

async function createZone() {
  const name = newZoneName.value.trim()
  if (!name || creatingZone.value) return
  if (!newZoneShifts.value.length) {
    errorText.value = '卫生责任区至少要有一个班次'
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
    errorText.value = err.message || '无法新增卫生责任区'
  } finally {
    creatingZone.value = false
  }
}

async function saveZoneShifts() {
  if (!selected.value || savingShifts.value) return
  if (!zoneShifts.value.length) {
    errorText.value = '卫生责任区至少要有一个班次'
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
    shiftsHint.value = '已保存。该责任区只会出现在选中的班次。'
  } catch (err) {
    errorText.value = err.message || '无法保存责任区班次'
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
    clearEditor()
    await loadZones()
  } catch (err) {
    errorText.value = err.message || '无法删除卫生责任区'
  }
}

async function confirmDeleteItem() {
  const item = deleteItemTarget.value
  deleteItemTarget.value = null
  if (!item) return
  errorText.value = ''
  try {
    await api.delete(`/api/hygiene/admin/items/${item.id}`)
    if (replacingId.value === item.id) clearEditor()
    await loadZones()
  } catch (err) {
    errorText.value = err.message || '无法删除日常检查项'
  }
}

function clearPreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
}

function clearEditor() {
  itemName.value = ''
  file.value = null
  clearPreview()
  markup.value = []
  arrowStart.value = null
  replacingId.value = null
  markupEditing.value = false
}

function onFile(event) {
  const picked = event.target.files && event.target.files[0]
  clearPreview()
  file.value = picked || null
  previewUrl.value = picked ? URL.createObjectURL(picked) : ''
  arrowStart.value = null
}

function onPoint(point) {
  if (mode.value === 'circle') {
    markup.value = [...markup.value, createCircleMark(point.x, point.y)]
    return
  }
  if (mode.value === 'caption') {
    markup.value = [...markup.value, createCaptionMark(point.x, point.y, captionText.value)]
    return
  }
  if (!arrowStart.value) {
    arrowStart.value = point
    return
  }
  markup.value = [
    ...markup.value,
    createArrowMark(arrowStart.value.x, arrowStart.value.y, point.x, point.y),
  ]
  arrowStart.value = null
}

function removeMark(index) {
  markup.value = markup.value.filter((_, i) => i !== index)
}

function startReplace(item) {
  replacingId.value = item.id
  markupEditing.value = false
  itemName.value = item.name
  markup.value = parseMarkup(item.markup)
  file.value = null
  clearPreview()
  arrowStart.value = null
}

function startEditMarkup(item) {
  replacingId.value = item.id
  markupEditing.value = true
  itemName.value = item.name
  markup.value = parseMarkup(item.markup)
  file.value = null
  clearPreview()
  arrowStart.value = null
  mode.value = 'circle'
  captionText.value = ''
  errorText.value = ''
}

async function saveMarkup() {
  if (savingMarkup.value || !replacingId.value) return
  savingMarkup.value = true
  errorText.value = ''
  try {
    await api.patch(`/api/hygiene/admin/items/${replacingId.value}/standard/markup`, {
      markup: markup.value,
    })
    clearEditor()
    await loadZones()
  } catch (err) {
    errorText.value = err.message || '无法保存标准图标注'
  } finally {
    savingMarkup.value = false
  }
}

function saveItem() {
  if (!canSave.value) return
  if (markupEditing.value) {
    void saveMarkup()
    return
  }
  errorText.value = ''
  const replacing = Boolean(replacingId.value)
  const itemLabel = replacingItem.value ? replacingItem.value.name : itemName.value.trim()
  const form = new FormData()
  form.append('file', file.value)
  form.append('markup', JSON.stringify(markup.value))
  if (!replacing) form.append('name', itemName.value.trim())
  try {
    imageUploads.enqueue({
      path: replacing
        ? `/api/hygiene/admin/items/${replacingId.value}/standard`
        : `/api/hygiene/admin/zones/${selected.value.id}/items`,
      formData: form,
      label: replacing ? `更新标准图 · ${itemLabel}` : `标准图 · ${itemLabel}`,
      detail: file.value.name,
      onSuccess: loadZones,
      // 建检查项 / 换标准图都是无条件 INSERT：自动重试会重复建行，只允许人工重试。
      autoRetry: false,
    })
    clearEditor()
  } catch (err) {
    errorText.value = err.message || '无法加入上传队列'
  }
}

function markLabel(mark) {
  if (mark.kind === 'circle') return '圆圈'
  if (mark.kind === 'arrow') return '箭头'
  return mark.text ? `批注：${mark.text}` : '批注'
}
</script>

<template>
  <div class="zones-page">
    <div class="card roster-head">
      <div>
        <p class="hy-eyebrow">Zones · 责任区划</p>
        <h1>卫生责任区</h1>
        <p>维护各卫生责任区的日常检查项和当前标准图。</p>
        <details class="rule-help">
          <summary>规则说明</summary>
          <p>卫生责任区与档口、配方岗位相互独立。每个责任区可选白班、夜班或只跑其中一个班次；没有当前标准图的检查项不会出现在员工端；更换标准图后，新检查使用新图。删除责任区或检查项会同时删除进行中的待办和该区未闭环整改单。</p>
        </details>
      </div>
      <div class="roster-head-actions">
        <label class="export-size">
          图片
          <select v-model="exportSize" :disabled="exporting" aria-label="导出图片规格">
            <option value="preview">预览图（快）</option>
            <option value="original">原图（最清晰）</option>
          </select>
        </label>
        <button
          type="button"
          class="btn"
          :disabled="exporting"
          @click="exportStandards"
        >{{ exportLabel }}</button>
        <button type="button" class="btn" :disabled="loading" @click="refreshPage">刷新</button>
      </div>
    </div>

    <div
      v-if="exporting && exportProgress && exportProgress.total"
      class="export-progress"
      role="status"
      :aria-label="exportLabel"
    >
      <div class="export-progress-bar" :style="{ width: exportPercent + '%' }"></div>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>

    <form class="card clocks-card" @submit.prevent="saveClocks">
      <div>
        <h3>日常逾期点</h3>
        <p>白班、夜班各一个钟点，到点仍未交才给企微群发文字。已交待验不算逾期，也不会点名某个人。</p>
      </div>
      <label class="clock-field" aria-label="白班日常逾期点">
        白班
        <LuyunTimePicker v-model="dayClock" />
      </label>
      <label class="clock-field" aria-label="夜班日常逾期点">
        夜班
        <LuyunTimePicker v-model="nightClock" />
      </label>
      <button type="submit" class="btn btn-primary" :disabled="savingClocks">保存逾期点</button>
      <p v-if="clocksHint" class="clocks-hint">{{ clocksHint }}</p>
    </form>

    <div class="zones-grid">
      <div class="table-card">
        <div class="table-card-header">
          <h3>责任区 <span>{{ zones.length }}</span></h3>
        </div>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <ul v-else class="zone-list">
          <li v-for="zone in zones" :key="zone.id" class="zone-row">
            <button
              type="button"
              class="zone-btn"
              :class="{ active: selected && selected.id === zone.id }"
              @click="selectZone(zone)"
            >
              <strong>{{ zone.name }}</strong>
              <span>
                {{ (zone.items || []).length }} 项
                <template v-if="missedByZone[zone.id]"> · 漏拍 {{ missedByZone[zone.id] }}</template>
              </span>
              <em class="zone-shifts">{{ zoneShiftLabel(zone) }}</em>
            </button>
            <button
              type="button"
              class="btn btn-sm btn-danger"
              :aria-label="`删除卫生责任区 ${zone.name}`"
              @click="deleteZoneTarget = zone"
            >删除</button>
          </li>
        </ul>
        <form class="zone-add" @submit.prevent="createZone">
          <input
            v-model="newZoneName"
            class="input"
            type="text"
            maxlength="40"
            placeholder="再加一个卫生责任区，比如卫生间"
            aria-label="新卫生责任区名称"
          >
          <div class="zone-shift-picks" role="group" aria-label="新责任区班次">
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
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <h3>{{ selected ? selected.name + ' 日常清单' : '日常清单' }}</h3>
        </div>
        <template v-if="selected">
          <form class="zone-shifts-editor" @submit.prevent="saveZoneShifts">
            <span class="zone-shifts-title">班次</span>
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
          <div v-if="!(selected.items || []).length" class="roster-empty">这个区还没有带标准图的检查项。</div>
          <ul v-else class="item-list">
            <li v-for="item in selected.items" :key="item.id" class="item-row">
              <HygieneStandardOverlay
                class="item-thumb"
                :src="standardImageUrl('admin', item, 'thumb')"
                display-variant="thumb"
                :lightbox-src="standardImageUrl('admin', item)"
                :standard-id="item.current_standard_id"
                :markup="item.markup || []"
                :alt="item.name"
              />
              <div class="item-meta">
                <strong>{{ item.name }}</strong>
                <div class="item-actions">
                  <button type="button" class="btn btn-sm" @click="startEditMarkup(item)">编辑标注</button>
                  <button type="button" class="btn btn-sm" @click="startReplace(item)">换标准图</button>
                  <button
                    type="button"
                    class="btn btn-sm btn-danger"
                    :aria-label="`删除日常检查项 ${item.name}`"
                  @click="deleteItemTarget = item"
                  >删除</button>
                </div>
              </div>
            </li>
          </ul>

          <form class="item-editor" @submit.prevent="saveItem">
            <h4>{{ markupEditing ? '编辑标准图标注' : (replacingId ? '更新标准图' : '新增检查项') }}</h4>
            <p v-if="markupEditing" class="editor-lead">
              只改标注、不换图：保存后会生成新一版标准图，员工端会提示重新对照；已经交过的记录仍看当时那一版。
            </p>
            <p v-else class="editor-lead">标准图保存后检查项才会出现在员工端。可在图上画圆圈、箭头和批注。</p>
            <label v-if="!markupEditing" class="editor-field">
              检查项名称
              <input
                v-model="itemName"
                class="input"
                type="text"
                maxlength="40"
                :disabled="Boolean(replacingId)"
                placeholder="比如案板表面"
              >
            </label>
            <label v-if="!markupEditing" class="editor-field">
              标准图
              <input class="input" type="file" accept="image/*" @change="onFile">
            </label>
            <div class="mark-tools">
              <button type="button" class="btn btn-sm" :class="{ 'btn-primary': mode === 'circle' }" @click="mode = 'circle'">圆圈</button>
              <button type="button" class="btn btn-sm" :class="{ 'btn-primary': mode === 'arrow' }" @click="mode = 'arrow'; arrowStart = null">箭头</button>
              <button type="button" class="btn btn-sm" :class="{ 'btn-primary': mode === 'caption' }" @click="mode = 'caption'">批注</button>
              <input
                v-if="mode === 'caption'"
                v-model="captionText"
                class="input"
                type="text"
                maxlength="40"
                placeholder="批注文字，再点图放上去"
              >
              <span v-if="mode === 'arrow' && arrowStart" class="editor-hint">再点一次画出箭头</span>
            </div>
            <HygieneStandardOverlay
              :src="editorSrc"
              :standard-id="replacingItem ? replacingItem.current_standard_id : null"
              :markup="markup"
              editable
              :alt="itemName || '标准图预览'"
              @point="onPoint"
            />
            <ul v-if="markup.length" class="mark-list">
              <li v-for="(mark, index) in markup" :key="index">
                <span>{{ markLabel(mark) }}</span>
                <button type="button" class="btn btn-sm" @click="removeMark(index)">删除标注</button>
              </li>
            </ul>
            <div class="editor-actions">
              <button v-if="replacingId" type="button" class="btn" @click="clearEditor">取消</button>
              <button type="submit" class="btn btn-primary" :disabled="!canSave || savingMarkup">
                {{ markupEditing ? (savingMarkup ? '保存中…' : '保存标注') : (replacingId ? '保存新标准图' : '新增检查项') }}
              </button>
            </div>
          </form>
        </template>
      </div>
    </div>

    <ConfirmDialog
      v-if="deleteZoneTarget"
      title="删除卫生责任区"
      :message="`删除「${deleteZoneTarget.name}」会同时删除该区进行中的日常待办和未闭环整改单。`"
      confirm-label="删除"
      danger
      @confirm="confirmDeleteZone"
      @cancel="deleteZoneTarget = null"
    />
    <ConfirmDialog
      v-if="deleteItemTarget"
      title="删除日常检查项"
      :message="`删除「${deleteItemTarget.name}」会同时删除该项进行中的待办。`"
      confirm-label="删除"
      danger
      @confirm="confirmDeleteItem"
      @cancel="deleteItemTarget = null"
    />
  </div>
</template>

<style scoped>
@media (min-width: 721px) {
  .hygiene-admin .zones-page .clocks-card {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: 12px 16px;
  }
}
.clocks-card p {
  max-width: 42em;
}
.clock-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--hy-muted);
}
.clock-field .input {
  min-width: 140px;
}
.clock-field :deep(.luyun-time-picker) {
  width: 104px;
}
.clocks-hint {
  flex: 1 1 100%;
}
.zones-grid {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 14px;
  align-items: start;
}
@media (max-width: 900px) {
  .zones-grid { grid-template-columns: 1fr; }
}
.zone-list {
  list-style: none;
  margin: 0;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.zone-row {
  display: flex;
  align-items: stretch;
  gap: 8px;
}
.zone-row .zone-btn { flex: 1; }
.zone-row .btn-danger { flex: 0 0 auto; align-self: center; }
.zone-shifts {
  font-style: normal;
  font-size: 11px;
  color: var(--hy-mint);
}
.zone-add {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 4px 10px 12px;
}
.zone-add .input { flex: 1; }
.zone-shift-picks,
.zone-shifts-editor {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.zone-shifts-editor {
  padding: 10px 14px;
  border-bottom: 1px solid var(--hy-line);
  font-size: 12px;
  color: var(--hy-muted);
}
.zone-shifts-title { font-weight: 600; color: var(--hy-ink); }
.zone-shift-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
  white-space: nowrap;
}
.zone-shift-toggle input { accent-color: var(--hy-mint); }
.zone-shifts-hint {
  flex: 1 1 100%;
  margin: 0;
  font-size: 11px;
  color: var(--hy-mint);
}
.item-list {
  list-style: none;
  margin: 0;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.item-row {
  display: grid;
  grid-template-columns: 148px 1fr;
  gap: 12px;
  align-items: center;
  padding: 10px;
}
.item-thumb { min-height: 0; }
.item-thumb :deep(.std-frame) { min-height: 0; }
.item-thumb :deep(.std-photo img) { max-height: 104px; }
.item-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}
.item-meta strong {
  font-weight: 600;
}
.item-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.item-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-top: 1px solid var(--hy-line);
}
.item-editor h4 {
  margin: 0;
  font-family: var(--font-song);
  font-size: 15px;
  letter-spacing: .04em;
}
.editor-lead {
  margin: 0;
  color: var(--hy-muted);
  font-size: 12px;
  line-height: 1.6;
}
.editor-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--hy-muted);
}
.mark-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.mark-tools .input { min-width: 180px; flex: 1; }
.mark-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.mark-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--hy-muted);
}
.editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
