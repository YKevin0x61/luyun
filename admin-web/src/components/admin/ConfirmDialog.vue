<script setup>
import SvgIcon from '../SvgIcon.vue'

// 轻量通用确认弹窗，复用项目既有 .modal-overlay/.modal-box 弹窗风格（admin-web/src/styles/theme.css），
// 用于替代浏览器原生 window.confirm，对齐老页 public/common.js confirmDialog() 的站内弹窗体验。
defineProps({
  title: { type: String, default: '请确认' },
  message: { type: String, required: true },
  confirmLabel: { type: String, default: '确认' },
  cancelLabel: { type: String, default: '取消' },
  danger: { type: Boolean, default: false },
})
const emit = defineEmits(['confirm', 'cancel'])
</script>

<template>
  <div class="modal-overlay" role="dialog" aria-modal="true" :aria-label="title" @click.self="emit('cancel')">
    <div class="modal-box" style="width:min(420px, 100%)">
      <div class="modal-header">
        <h3>{{ title }}</h3>
        <button class="btn btn-sm" aria-label="关闭" @click="emit('cancel')"><SvgIcon name="x" :size="14" /></button>
      </div>
      <p style="font-size:13px;line-height:1.6;margin:0 0 4px">{{ message }}</p>
      <div class="modal-footer">
        <button class="btn" @click="emit('cancel')">{{ cancelLabel }}</button>
        <button class="btn" :class="danger ? 'btn-danger' : 'btn-primary'" @click="emit('confirm')">{{ confirmLabel }}</button>
      </div>
    </div>
  </div>
</template>
