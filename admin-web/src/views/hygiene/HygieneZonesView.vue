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
/** 「新增检查项」默认折叠：v3 布局纪律 —— 一行入口，点开才展开整块编辑面板。 */
const editorOpen = ref(false)
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
    clearEditor()
    await loadZones()
  } catch (err) {
    errorText.value = err.message || '无法删除卫生工作区'
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
  editorOpen.value = false
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
  editorOpen.value = true
  replacingId.value = item.id
  markupEditing.value = false
  itemName.value = item.name
  markup.value = parseMarkup(item.markup)
  file.value = null
  clearPreview()
  arrowStart.value = null
}

function startEditMarkup(item) {
  editorOpen.value = true
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
    <header class="wbx-head">
      <div class="wbx-head-main">
        <p class="wbx-kicker">ZONES · 工作区</p>
        <h1 class="wbx-title">卫生工作区</h1>
      </div>
      <div class="wbx-pills">
        <label class="export-size">
          图片
          <select v-model="exportSize" :disabled="exporting" aria-label="导出图片规格">
            <option value="preview">预览图（快）</option>
            <option value="original">原图（最清晰）</option>
          </select>
        </label>
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

      <section class="wbx-panel">
        <header class="wbx-ph">
          <i class="led ok"></i>
          <h2>{{ selected ? selected.name + ' 日常清单' : '日常清单' }}</h2>
          <span class="wbx-hint">{{ selected ? (selected.items || []).length : 0 }} 项</span>
        </header>
        <template v-if="selected">
          <div v-if="!(selected.items || []).length" class="wbx-empty-bar">这个区还没有带标准图的检查项。</div>
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

          <!-- 新增检查项：默认折叠成一行入口（v3：折叠表单，不常驻半个空面板） -->
          <button
            v-if="!editorOpen"
            type="button"
            class="wbx-fold-entry"
            @click="editorOpen = true"
          >＋ 新增检查项</button>

          <form v-if="editorOpen" class="item-editor" @submit.prevent="saveItem">
            <h2 class="wbx-subhead"><i class="led sig"></i>{{ markupEditing ? '编辑标准图标注' : (replacingId ? '更新标准图' : '新增检查项') }}</h2>
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
              <button type="button" class="btn" @click="clearEditor">取消</button>
              <button type="submit" class="btn btn-primary" :disabled="!canSave || savingMarkup">
                {{ markupEditing ? (savingMarkup ? '保存中…' : '保存标注') : (replacingId ? '保存新标准图' : '新增检查项') }}
              </button>
            </div>
          </form>
        </template>
        <div v-else class="wbx-empty-bar">先选一个工作区。</div>
      </section>
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
.export-size select {
  min-height: 44px; padding: 0 8px; border-radius: var(--r-1);
  background: var(--surface-1); border: 1px solid var(--line-strong); color: var(--ink);
  font: inherit; font-size: 10px;
}
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
.wbx-subhead { margin: 0; display: flex; align-items: center; gap: 8px; font: 600 11px/1 var(--font-sans); letter-spacing: .18em; text-transform: uppercase; color: var(--ink-faint); }

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

.item-list { list-style: none; margin: 0; padding: 4px 12px 8px; display: flex; flex-direction: column; }
.item-row {
  display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 10px; align-items: center;
  min-height: 72px; padding: 8px 0; border-bottom: 1px solid var(--wb-ink-a08);
}
.item-row:last-child { border-bottom: 0; }
.item-thumb { min-height: 0; width: 56px; }
.item-thumb :deep(.std-frame) { min-height: 0; }
.item-thumb :deep(.std-photo img) { max-height: 56px; }
.item-meta { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-width: 0; }
.item-meta strong { font-size: 11.5px; font-weight: 600; }
.item-actions { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; align-self: stretch; }
.item-actions .btn { min-height: 44px !important; }

.wbx-fold-entry {
  display: flex; align-items: center; gap: 8px; width: 100%;
  min-height: 48px; padding: 10px 14px; margin-top: auto;
  border: 0; border-top: 1px solid var(--wb-ink-a08); background: transparent;
  color: var(--action); font: 500 11px/1 var(--font-sans); letter-spacing: .04em;
  text-align: left; cursor: pointer;
}
.wbx-fold-entry:hover { background: var(--surface-2); }
.item-editor { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px 14px; border-top: 1px solid var(--wb-ink-a08); }
.editor-lead { margin: 0; font-size: 10.5px; line-height: 1.6; color: var(--ink-dim); }
.editor-field { display: flex; flex-direction: column; gap: 6px; font-size: 10px; color: var(--ink-dim); }
.editor-hint { font: 500 9.5px/1.5 var(--font-mono); color: var(--ink-faint); }
.editor-actions { display: flex; justify-content: flex-end; gap: 8px; }
.mark-tools { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.mark-tools .input { min-width: 180px; flex: 1; }
.mark-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.mark-list li { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 10px; color: var(--ink-dim); }
.zones-page :deep(.btn) { min-height: 44px !important; border-radius: var(--r-1); }
@media (max-width: 900px) {
  .wbx-cols { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .wbx-head { grid-template-columns: minmax(0, 1fr); }
  .wbx-pills { grid-column: 1; grid-row: 2; justify-self: start; margin-top: 10px; }
  .wbx-sub { grid-row: 3; }
  .wbx-head .rule-help { grid-row: 4; }
  .wbx-title { font-size: 22px; }
}
</style>
