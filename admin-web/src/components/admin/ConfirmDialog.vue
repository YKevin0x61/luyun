<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import SvgIcon from '../SvgIcon.vue'

// 轻量通用确认弹窗，复用项目既有 .modal-overlay/.modal-box 弹窗风格（admin-web/src/styles/theme.css），
// 用于替代浏览器原生 window.confirm，对齐老页 public/common.js confirmDialog() 的站内弹窗体验。
//
// prompt 为可选：传了才渲染输入框，confirm 事件会带上用户填的文本（不传时是空串）。
// 驳回这类"要说清哪里不行"的场景靠它，避免再做一个专用组件。
defineProps({
  title: { type: String, default: '请确认' },
  message: { type: String, required: true },
  confirmLabel: { type: String, default: '确认' },
  cancelLabel: { type: String, default: '取消' },
  danger: { type: Boolean, default: false },
  prompt: { type: Object, default: null },
})
const emit = defineEmits(['confirm', 'cancel'])
const value = ref('')
const boxEl = ref(null)
const promptEl = ref(null)
const confirmEl = ref(null)

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusables() {
  if (!boxEl.value) return []
  return Array.from(boxEl.value.querySelectorAll(FOCUSABLE))
}

function onKeydown(event) {
  // 员工页在拍摄 sheet 打开时用全局 keydown 把 Tab 锁进 `.staff-preview-card`，
  // 确认框是在它**之上**弹出来的：这里必须用捕获阶段先拿到事件，否则 Esc 和 Tab
  // 都被页面那层截走，框里的按钮键盘够不到（鼠标能点，键盘用户就被困住了）。
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    emit('cancel')
    return
  }
  if (event.key !== 'Tab') return
  const items = focusables()
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement
  const inside = boxEl.value && boxEl.value.contains(active)
  if (event.shiftKey && (!inside || active === first)) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (!inside || active === last)) {
    event.preventDefault()
    first.focus()
  }
}

onMounted(async () => {
  document.addEventListener('keydown', onKeydown, true)
  await nextTick()
  // 需要填原因的（驳回）先聚焦输入框，纯确认的聚焦主按钮——直接回车即生效。
  const target = promptEl.value || confirmEl.value || focusables()[0] || boxEl.value
  target?.focus?.()
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown, true)
})
</script>

<template>
  <div class="modal-overlay" role="dialog" aria-modal="true" :aria-label="title" @click.self="emit('cancel')">
    <div ref="boxEl" class="modal-box" tabindex="-1" style="width:min(420px, 100%)">
      <div class="modal-header">
        <h3>{{ title }}</h3>
        <button class="btn btn-sm" aria-label="关闭" @click="emit('cancel')"><SvgIcon name="x" :size="14" /></button>
      </div>
      <p style="font-size:13px;line-height:1.6;margin:0 0 4px">{{ message }}</p>
      <label v-if="prompt" class="modal-prompt">
        <span>{{ prompt.label || '说明（可选）' }}</span>
        <textarea
          ref="promptEl"
          v-model="value"
          rows="2"
          :maxlength="prompt.maxlength || 120"
          :placeholder="prompt.placeholder || ''"
        ></textarea>
      </label>
      <div class="modal-footer">
        <button class="btn" @click="emit('cancel')">{{ cancelLabel }}</button>
        <button ref="confirmEl" class="btn" :class="danger ? 'btn-danger' : 'btn-primary'" @click="emit('confirm', value.trim())">{{ confirmLabel }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-prompt {
  display: block;
  margin: 10px 0 4px;
  font-size: 12px;
  color: var(--text-muted, #6b7280);
}
.modal-prompt textarea {
  display: block;
  width: 100%;
  margin-top: 6px;
  padding: 8px 10px;
  border: 1px solid var(--border-color, #d1d5db);
  border-radius: 8px;
  font: inherit;
  font-size: 13px;
  color: var(--text-primary, #111827);
  resize: vertical;
}
</style>
