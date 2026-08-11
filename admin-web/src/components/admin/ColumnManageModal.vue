<script setup>
import { reactive, ref } from 'vue'
import { getColumnLabel, isAutoColumn } from '../../utils/adminLabels'
import SvgIcon from '../SvgIcon.vue'
import LuyunCheckbox from '../ui/LuyunCheckbox.vue'

const props = defineProps({
  schema: { type: Array, required: true },
  table: { type: String, default: '' },
})
const emit = defineEmits(['close', 'add', 'drop'])

const form = reactive({
  column_name: '',
  column_type: 'TEXT',
  nullable: true,
  default_value: '',
})
const error = ref('')

function submitAdd() {
  error.value = ''
  if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(form.column_name)) {
    error.value = '字段名只能包含字母/数字/下划线，且不能以数字开头'
    return
  }
  emit('add', {
    column_name: form.column_name,
    column_type: form.column_type,
    nullable: form.nullable,
    default_value: form.default_value === '' ? null : form.default_value,
  })
}

function requestDrop(columnName) {
  if (!window.confirm(`确认删除字段「${columnName}」？此操作会重建表，不可撤销。`)) return
  emit('drop', columnName)
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-box">
      <div class="modal-header">
        <h3>表结构管理</h3>
        <button class="btn btn-sm" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>

      <div class="panel-title" style="font-size:12px">现有字段</div>
      <div style="display:flex;flex-direction:column;gap:6px;margin-bottom:16px;max-height:200px;overflow-y:auto" class="luyun-scrollbar">
        <div
          v-for="col in schema"
          :key="col.name"
          style="display:flex;align-items:center;gap:8px;padding:5px 8px;background:var(--card2);border-radius:6px;font-size:12px;flex-wrap:wrap"
        >
          <span style="flex:1;min-width:120px">
            <span v-if="col.pk" title="主键" style="color:var(--yellow)"><SvgIcon name="key" :size="12" /></span>
            {{ getColumnLabel(table, col.name) }} <span style="color:var(--text-dim)">({{ col.name }})</span>
          </span>
          <span class="badge">{{ col.type }}</span>
          <span class="badge" :style="col.notnull ? 'color:var(--red);border-color:var(--red)' : ''">
            {{ col.notnull ? '非空' : '可空' }}
          </span>
          <span class="badge" :title="col.dflt_value == null ? '' : String(col.dflt_value)">
            默认: {{ col.dflt_value == null ? '无' : col.dflt_value }}
          </span>
          <button
            v-if="!isAutoColumn(col.name) && col.name.toLowerCase() !== 'rowid'"
            class="btn btn-sm btn-danger"
            @click="requestDrop(col.name)"
          >删除</button>
          <span v-else class="badge" title="禁止删除">禁止删除</span>
        </div>
      </div>

      <div class="panel-title" style="font-size:12px">新增字段</div>
      <div class="form-row">
        <label>字段名</label>
        <input class="input" v-model="form.column_name" placeholder="例如 remark" />
      </div>
      <div class="form-row">
        <label>类型</label>
        <select class="select" v-model="form.column_type">
          <option value="TEXT">TEXT</option>
          <option value="INTEGER">INTEGER</option>
          <option value="REAL">REAL</option>
          <option value="NUMERIC">NUMERIC</option>
          <option value="BLOB">BLOB</option>
        </select>
      </div>
      <div class="form-row">
        <label class="luyun-check-row" style="display:flex;align-items:center;gap:6px">
          <LuyunCheckbox v-model="form.nullable" /> 允许为空
        </label>
      </div>
      <div class="form-row">
        <label>默认值（可选）</label>
        <input class="input" v-model="form.default_value" placeholder="留空表示无默认值" />
      </div>
      <div v-if="error" style="color:var(--red);font-size:12px;margin-bottom:8px">{{ error }}</div>

      <div class="modal-footer">
        <button class="btn" @click="emit('close')">关闭</button>
        <button class="btn btn-primary" @click="submitAdd">添加字段</button>
      </div>
    </div>
  </div>
</template>
