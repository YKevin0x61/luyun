<script setup>
import { computed, ref, watch } from 'vue'
import { useStationsStore } from '../../stores/stations'
import { getColumnLabel, isTimestampColumn } from '../../utils/adminLabels'
import SvgIcon from '../SvgIcon.vue'
import LuyunCheckbox from '../ui/LuyunCheckbox.vue'
import LuyunNumberInput from '../ui/LuyunNumberInput.vue'

const props = defineProps({
  schema: { type: Array, required: true },
  table: { type: String, required: true },
  selectedCount: { type: Number, required: true },
})
const emit = defineEmits(['close', 'submit'])

const stationsStore = useStationsStore()
stationsStore.load()

const selectedColumn = ref('')
const textValue = ref('')
const toggleValue = ref(true)
const stationValue = ref('')

const editableColumns = computed(() =>
  props.schema.filter((col) => {
    const name = String(col.name || '').toLowerCase()
    return name !== 'rowid' && !col.pk && !isTimestampColumn(col.name)
  }),
)

const currentColumn = computed(() =>
  editableColumns.value.find((col) => col.name === selectedColumn.value) || null,
)

function isNumericType(type) {
  const t = String(type || '').toUpperCase()
  return t === 'INTEGER' || t === 'REAL'
}

function isStationField(name) {
  const lower = String(name || '').toLowerCase()
  return lower === 'station' || lower === 'station_id'
}

function isToggleField(name, type) {
  const lower = String(name || '').toLowerCase()
  return isNumericType(type) && (lower === 'enabled' || lower === 'active')
}

const inputMode = computed(() => {
  const col = currentColumn.value
  if (!col) return 'text'
  if (isStationField(col.name)) return 'station'
  if (isToggleField(col.name, col.type)) return 'toggle'
  if (isNumericType(col.type)) return 'number'
  return 'text'
})

watch(
  () => selectedColumn.value,
  (name) => {
    const col = editableColumns.value.find((c) => c.name === name)
    if (!col) return
    if (isToggleField(col.name, col.type)) toggleValue.value = true
    else if (isStationField(col.name)) stationValue.value = ''
    else textValue.value = ''
  },
)

function buildValue() {
  const col = currentColumn.value
  if (!col) return null
  if (inputMode.value === 'station') {
    const v = stationValue.value.trim()
    return v || null
  }
  if (inputMode.value === 'toggle') {
    return toggleValue.value ? 1 : 0
  }
  const raw = textValue.value
  const v = typeof raw === 'string' ? raw.trim() : raw
  if (v === '' || v === null || v === undefined) return null
  if (isNumericType(col.type)) {
    const parsed = String(col.type).toUpperCase() === 'INTEGER' ? parseInt(v, 10) : parseFloat(v)
    return Number.isNaN(parsed) ? 0 : parsed
  }
  return v
}

function previewValue() {
  const col = currentColumn.value
  if (!col) return '—'
  if (inputMode.value === 'station') {
    return stationValue.value ? stationsStore.nameOf(stationValue.value) : '（空）'
  }
  if (inputMode.value === 'toggle') {
    return toggleValue.value ? '是 (1)' : '否 (0)'
  }
  const v = buildValue()
  return v === null || v === undefined || v === '' ? '（空）' : String(v)
}

function submit() {
  if (!selectedColumn.value) return
  emit('submit', { column: selectedColumn.value, value: buildValue() })
}
</script>

<template>
  <div class="modal-overlay" role="dialog" aria-modal="true" aria-label="批量修改" @click.self="emit('close')">
    <div class="modal-box" style="width:min(460px, 100%)">
      <div class="modal-header">
        <h3>批量修改</h3>
        <button class="btn btn-sm" aria-label="关闭" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>

      <p style="font-size:13px;color:var(--text-dim);margin:0 0 12px">
        将统一修改已选的 <strong>{{ selectedCount }}</strong> 条记录。
      </p>

      <div class="form-row">
        <label>修改字段</label>
        <select class="select" v-model="selectedColumn" required>
          <option value="" disabled>-- 选择字段 --</option>
          <option v-for="col in editableColumns" :key="col.name" :value="col.name">
            {{ getColumnLabel(table, col.name) }}
          </option>
        </select>
      </div>

      <div v-if="currentColumn" class="form-row">
        <label>
          新值
          <code style="color:var(--accent);margin-left:4px">{{ currentColumn.type }}</code>
        </label>

        <select v-if="inputMode === 'station'" class="select" v-model="stationValue">
          <option value="">-- 选择档口 --</option>
          <option v-for="s in stationsStore.list" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>

        <label v-else-if="inputMode === 'toggle'" class="luyun-check-row" style="font-weight:400">
          <LuyunCheckbox v-model="toggleValue" />
          {{ toggleValue ? '启用 (1)' : '停用 (0)' }}
        </label>

        <LuyunNumberInput
          v-else-if="inputMode === 'number'"
          v-model="textValue"
          :decimal="String(currentColumn.type).toUpperCase() === 'REAL'"
          :step="String(currentColumn.type).toUpperCase() === 'REAL' ? 0.1 : 1"
          :placeholder="`输入${getColumnLabel(table, currentColumn.name)}`"
        />

        <input
          v-else
          class="input"
          type="text"
          v-model="textValue"
          :placeholder="`输入${getColumnLabel(table, currentColumn.name)}`"
        />
      </div>

      <p v-if="selectedColumn" style="font-size:12px;color:var(--text-dim);margin:12px 0 0">
        预览：{{ getColumnLabel(table, selectedColumn) }} → {{ previewValue() }}
      </p>

      <div class="modal-footer">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn btn-primary" :disabled="!selectedColumn" @click="submit">确认修改</button>
      </div>
    </div>
  </div>
</template>
