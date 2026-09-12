<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api/client'
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
const errorText = ref('')
const newZoneName = ref('')
const creatingZone = ref(false)
const dayClock = ref('15:00')
const nightClock = ref('21:30')
const savingClocks = ref(false)
const clocksHint = ref('')
const zoneEvents = ref([])

const itemName = ref('')
const file = ref(null)
const previewUrl = ref('')
const markup = ref([])
const mode = ref('circle')
const captionText = ref('')
const arrowStart = ref(null)
const saving = ref(false)
const replacingId = ref(null)

const selected = computed(() => {
  return zones.value.find((zone) => zone.id === selectedId.value) || zones.value[0] || null
})

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
  if (!selected.value || !file.value || saving.value) return false
  if (replacingId.value) return true
  return Boolean(itemName.value.trim())
})

const missedByZone = computed(() => {
  const counts = {}
  for (const event of zoneEvents.value) {
    if (event.event_type !== '逾期' || event.zone_id == null) continue
    counts[event.zone_id] = (counts[event.zone_id] || 0) + 1
  }
  return counts
})

onMounted(() => {
  refreshPage()
})

onBeforeUnmount(clearPreview)

async function refreshPage() {
  await Promise.all([loadZones(), loadClocks(), loadBoardPreview()])
}

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
    const data = await api.get('/api/hygiene/admin/board-events')
    zoneEvents.value = data.zones || []
  } catch (_err) {
    zoneEvents.value = []
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

async function createZone() {
  const name = newZoneName.value.trim()
  if (!name || creatingZone.value) return
  creatingZone.value = true
  errorText.value = ''
  try {
    const data = await api.post('/api/hygiene/admin/zones', { name })
    newZoneName.value = ''
    await loadZones()
    if (data.zone) selectedId.value = data.zone.id
  } catch (err) {
    errorText.value = err.message || '无法新增卫生责任区'
  } finally {
    creatingZone.value = false
  }
}

async function deleteZone(zone) {
  if (!zone) return
  if (!window.confirm(`删除卫生责任区「${zone.name}」？该区进行中的日常待办和未闭环整改单会一并去掉。`)) {
    return
  }
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

async function deleteItem(item) {
  if (!item) return
  if (!window.confirm(`删除日常检查项「${item.name}」？该项进行中的待办会一并去掉。`)) {
    return
  }
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
  itemName.value = item.name
  markup.value = parseMarkup(item.markup)
  file.value = null
  clearPreview()
  arrowStart.value = null
}

async function saveItem() {
  if (!canSave.value) return
  saving.value = true
  errorText.value = ''
  const form = new FormData()
  form.append('file', file.value)
  form.append('markup', JSON.stringify(markup.value))
  try {
    if (replacingId.value) {
      await api.upload(`/api/hygiene/admin/items/${replacingId.value}/standard`, form)
    } else {
      form.append('name', itemName.value.trim())
      await api.upload(`/api/hygiene/admin/zones/${selected.value.id}/items`, form)
    }
    clearEditor()
    await loadZones()
  } catch (err) {
    errorText.value = err.message || '保存失败'
  } finally {
    saving.value = false
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
        <h2>卫生责任区</h2>
        <p>卫生责任区不是档口、配方岗位或备货子岗位。每个区自己的日常清单；没有当前标准图的检查项不会出现在员工端。换标准图后，新检查只看新图。白班夜班共用这一套检查项。删除卫生责任区或检查项会把进行中的日常待办和该区未闭环整改单一并去掉。</p>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="refreshPage">刷新</button>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>

    <form class="card clocks-card" @submit.prevent="saveClocks">
      <div>
        <h3>日常逾期点</h3>
        <p>白班、夜班各一个钟点，到点仍未交才给企微群发文字。已交待验不算逾期，也不会点名某个人。</p>
      </div>
      <label class="clock-field">
        白班
        <input
          v-model="dayClock"
          class="input"
          type="time"
          required
          aria-label="白班日常逾期点"
        >
      </label>
      <label class="clock-field">
        夜班
        <input
          v-model="nightClock"
          class="input"
          type="time"
          required
          aria-label="夜班日常逾期点"
        >
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
            </button>
            <button
              type="button"
              class="btn btn-sm btn-danger"
              :aria-label="`删除卫生责任区 ${zone.name}`"
              @click="deleteZone(zone)"
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
          <button type="submit" class="btn btn-primary" :disabled="creatingZone || !newZoneName.trim()">新增</button>
        </form>
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <h3>{{ selected ? selected.name + ' 日常清单' : '日常清单' }}</h3>
        </div>
        <template v-if="selected">
          <div v-if="!(selected.items || []).length" class="roster-empty">这个区还没有带标准图的检查项。</div>
          <ul v-else class="item-list">
            <li v-for="item in selected.items" :key="item.id" class="item-row">
              <HygieneStandardOverlay
                class="item-thumb"
                :src="standardImageUrl('admin', item)"
                :markup="item.markup || []"
                :alt="item.name"
              />
              <div class="item-meta">
                <strong>{{ item.name }}</strong>
                <div class="item-actions">
                  <button type="button" class="btn btn-sm" @click="startReplace(item)">换标准图</button>
                  <button
                    type="button"
                    class="btn btn-sm btn-danger"
                    :aria-label="`删除日常检查项 ${item.name}`"
                    @click="deleteItem(item)"
                  >删除</button>
                </div>
              </div>
            </li>
          </ul>

          <form class="item-editor" @submit.prevent="saveItem">
            <h4>{{ replacingId ? '更新标准图' : '新增检查项' }}</h4>
            <p class="editor-lead">必须先有标准图才能上架。可在图上点圆圈、拖两下画箭头、写批注。相册选图可以，员工开单拍实拍才禁止相册。</p>
            <label class="editor-field">
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
            <label class="editor-field">
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
              :markup="markup"
              editable
              :alt="itemName || '标准图预览'"
              @point="onPoint"
            />
            <ul v-if="markup.length" class="mark-list">
              <li v-for="(mark, index) in markup" :key="index">
                <span>{{ markLabel(mark) }}</span>
                <button type="button" class="btn btn-sm" @click="removeMark(index)">去掉</button>
              </li>
            </ul>
            <div class="editor-actions">
              <button v-if="replacingId" type="button" class="btn" @click="clearEditor">取消</button>
              <button type="submit" class="btn btn-primary" :disabled="!canSave">
                {{ replacingId ? '保存新标准图' : '上架检查项' }}
              </button>
            </div>
          </form>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.zones-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 1180px;
}
.roster-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
}
.roster-head h2 { margin: 0 0 6px; font-size: 16px; }
.roster-head p {
  margin: 0;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.6;
  max-width: 56em;
}
.clocks-card {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px 16px;
  padding: 14px 16px;
}
.clocks-card h3 {
  margin: 0 0 4px;
  font-size: 14px;
}
.clocks-card p {
  margin: 0;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.55;
  max-width: 42em;
}
.clock-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-dim);
}
.clock-field .input {
  min-width: 140px;
}
.clocks-hint {
  flex: 1 1 100%;
  margin: 0;
  color: var(--cyan);
  font-size: 12px;
}
.roster-error {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 13px;
}
.roster-empty {
  padding: 28px 16px;
  text-align: center;
  color: var(--text-dim);
  font-size: 13px;
}
.zones-grid {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 12px;
  align-items: start;
}
@media (max-width: 900px) {
  .zones-grid { grid-template-columns: 1fr; }
}
.zone-list {
  list-style: none;
  margin: 0;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.zone-row {
  display: flex;
  align-items: stretch;
  gap: 6px;
}
.zone-row .zone-btn { flex: 1; }
.zone-row .btn-danger { flex: 0 0 auto; align-self: center; }
.zone-btn {
  width: 100%;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  text-align: left;
  background: var(--card2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  font-family: inherit;
}
.zone-btn span { color: var(--text-dim); font-size: 12px; }
.zone-btn.active { border-color: var(--accent); }
.zone-add {
  display: flex;
  gap: 8px;
  padding: 8px 8px 12px;
}
.zone-add .input { flex: 1; }
.item-list {
  list-style: none;
  margin: 0;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.item-row {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 12px;
  align-items: center;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--card2);
}
.item-thumb { min-height: 0; }
.item-thumb :deep(.std-frame) { min-height: 0; }
.item-thumb :deep(.std-photo img) { max-height: 96px; }
.item-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
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
  padding: 12px;
  border-top: 1px solid var(--border);
}
.item-editor h4 { margin: 0; font-size: 14px; }
.editor-lead {
  margin: 0;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.55;
}
.editor-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-dim);
}
.mark-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.mark-tools .input { min-width: 180px; flex: 1; }
.editor-hint { font-size: 12px; color: var(--cyan); }
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
}
.editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
