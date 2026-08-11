<script setup>
import { computed, reactive, watch } from 'vue'
import { getColumnLabel, isAutoColumn, isTimestampColumn } from '../../utils/adminLabels'
import SvgIcon from '../SvgIcon.vue'
import LuyunNumberInput from '../ui/LuyunNumberInput.vue'

const props = defineProps({
  schema: { type: Array, required: true },
  initialValues: { type: Object, default: () => ({}) },
  title: { type: String, default: '编辑记录' },
  table: { type: String, default: '' },
  mode: { type: String, default: 'edit' }, // 'create' | 'edit'
})
const emit = defineEmits(['close', 'submit'])

// 新增记录：排除自增/自动列（id/rowid/created_at/updated_at），保留可填的非自增主键列（如 dish_name）。
// 编辑记录：rowid 及时间戳列由后端维护，不出现在表单中；主键列以只读形式展示，不可修改、也不随表单提交。
const visibleColumns = computed(() => {
  if (props.mode === 'create') {
    return props.schema.filter((col) => !isAutoColumn(col.name))
  }
  return props.schema.filter((col) => col.name.toLowerCase() !== 'rowid' && !isTimestampColumn(col.name))
})

function isNumericType(type) {
  const t = String(type || '').toUpperCase()
  return t === 'INTEGER' || t === 'REAL'
}

function isReadonly(col) {
  return props.mode === 'edit' && !!col.pk
}

const form = reactive({})
watch(
  [() => props.initialValues, () => props.schema, () => props.mode],
  () => {
    Object.keys(form).forEach((k) => delete form[k])
    for (const col of visibleColumns.value) {
      const v = props.initialValues?.[col.name]
      form[col.name] = v ?? ''
    }
  },
  { immediate: true },
)

function submit() {
  const values = {}
  for (const col of visibleColumns.value) {
    if (isReadonly(col)) continue
    const raw = form[col.name]
    const v = typeof raw === 'string' ? raw.trim() : raw
    if (v === '' || v === null || v === undefined) {
      values[col.name] = null
    } else if (isNumericType(col.type)) {
      const parsed = String(col.type).toUpperCase() === 'INTEGER' ? parseInt(v, 10) : parseFloat(v)
      values[col.name] = Number.isNaN(parsed) ? 0 : parsed
    } else {
      values[col.name] = v
    }
  }
  emit('submit', values)
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-box">
      <div class="modal-header">
        <h3>{{ title }}</h3>
        <button class="btn btn-sm" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>
      <div v-for="col in visibleColumns" :key="col.name" class="form-row">
        <label>
          {{ getColumnLabel(table, col.name) }}{{ col.notnull ? ' *' : '' }}
          <span v-if="col.pk" class="badge" style="margin-left:4px;padding:0 6px">主键</span>
          <code style="color:var(--accent);margin-left:4px">{{ col.type }}</code>
        </label>
        <LuyunNumberInput
          v-if="isNumericType(col.type)"
          v-model="form[col.name]"
          :decimal="String(col.type).toUpperCase() === 'REAL'"
          :step="String(col.type).toUpperCase() === 'REAL' ? 0.1 : 1"
          :readonly="isReadonly(col)"
          :placeholder="col.type"
        />
        <input
          v-else
          class="input"
          type="text"
          v-model="form[col.name]"
          :readonly="isReadonly(col)"
          :style="isReadonly(col) ? 'opacity:0.6;cursor:not-allowed' : ''"
          :placeholder="col.type"
        />
      </div>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn btn-primary" @click="submit">保存</button>
      </div>
    </div>
  </div>
</template>
