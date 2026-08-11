<script setup>
import { reactive, watch } from 'vue'
import SvgIcon from '../SvgIcon.vue'
import LuyunNumberInput from '../ui/LuyunNumberInput.vue'

const props = defineProps({
  dishName: { type: String, required: true },
  initial: { type: Object, default: null }, // 有值表示编辑，否则新增
})
const emit = defineEmits(['close', 'submit'])

const form = reactive({
  semi_name: '',
  position: '',
  category: '',
  factor: 1,
  unit: '',
})

watch(
  () => props.initial,
  (v) => {
    form.semi_name = v?.semi_name || ''
    form.position = v?.position || ''
    form.category = v?.category || ''
    form.factor = v?.factor || 1
    form.unit = v?.unit || ''
  },
  { immediate: true },
)

function submit() {
  if (!form.semi_name.trim()) {
    window.alert('请输入半成品名称')
    return
  }
  emit('submit', { ...form, factor: parseFloat(form.factor) || 1 })
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-box" style="width:420px">
      <div class="modal-header">
        <h3>{{ initial ? '编辑换算规则' : '添加换算规则' }}</h3>
        <button class="btn btn-sm" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>
      <p style="font-size:12px;margin-bottom:10px">菜品：<b style="color:var(--accent)">{{ dishName }}</b></p>
      <div class="form-row">
        <label>半成品名称（如：鲜虾）</label>
        <input class="input" v-model="form.semi_name">
      </div>
      <div class="form-row">
        <label>岗位（如：案板）</label>
        <input class="input" v-model="form.position">
      </div>
      <div class="form-row">
        <label>分类（如：肉类、蔬菜）</label>
        <input class="input" v-model="form.category">
      </div>
      <div style="display:flex;gap:8px">
        <div class="form-row" style="flex:1">
          <label>系数</label>
          <LuyunNumberInput v-model="form.factor" decimal :step="0.1" :min="0" />
        </div>
        <div class="form-row" style="flex:1">
          <label>单位（如：个）</label>
          <input class="input" v-model="form.unit">
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn btn-primary" @click="submit">保存</button>
      </div>
    </div>
  </div>
</template>
