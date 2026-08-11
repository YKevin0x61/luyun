<script setup>
import { ref } from 'vue'
import SvgIcon from '../SvgIcon.vue'

const props = defineProps({
  content: { type: String, default: '' },
})
const emit = defineEmits(['close', 'push'])
const copied = ref(false)

async function copyText() {
  try {
    await navigator.clipboard.writeText(props.content)
  } catch (e) {
    // Clipboard API 不可用时的降级：选中 textarea 内容手动复制
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-box" style="width:600px;max-height:80vh;display:flex;flex-direction:column">
      <div class="modal-header">
        <h3><SvgIcon name="clipboard" :size="15" /> 文字版报表 — 预览并复制</h3>
        <button class="btn btn-sm" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>
      <p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">
        已固定为模板C（按档口→分类分组），直接 Ctrl+A 全选复制，或点击下方按钮复制
      </p>
      <textarea
        class="input"
        readonly
        :value="content"
        style="flex:1;min-height:320px;font-family:monospace;resize:none;width:100%"
      ></textarea>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">关闭</button>
        <button class="btn" @click="emit('push')">推送企业微信</button>
        <button class="btn btn-primary" @click="copyText">{{ copied ? '已复制' : '复制' }}</button>
      </div>
    </div>
  </div>
</template>
