<script setup>
import { ref } from 'vue'
import SvgIcon from '../SvgIcon.vue'
import { useModalA11y } from '../../composables/useModalA11y'

const props = defineProps({
  content: { type: String, default: '' },
  allowPush: { type: Boolean, default: true },
  title: { type: String, default: '文字版报表 — 预览并复制' },
  hint: { type: String, default: '已固定为模板C（按档口→分类分组），直接 Ctrl+A 全选复制，或点击下方按钮复制' },
})
const emit = defineEmits(['close', 'push'])
const boxRef = useModalA11y(() => emit('close'))
// '' | 'ok' | 'fail'：以前 catch 之后也无条件写「已复制」，复制失败会骗用户（审查 R24）。
const copyState = ref('')

async function copyText() {
  let ok = false
  try {
    await navigator.clipboard.writeText(props.content)
    ok = true
  } catch (e) {
    // Clipboard API 不可用（非 HTTPS / 权限拒绝）时降级到 execCommand。
    try {
      const ta = document.createElement('textarea')
      ta.value = props.content
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      ok = document.execCommand('copy')
      document.body.removeChild(ta)
    } catch (_) {
      ok = false
    }
  }
  copyState.value = ok ? 'ok' : 'fail'
  setTimeout(() => { copyState.value = '' }, 2000)
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div
      ref="boxRef"
      class="modal-box"
      style="width:min(600px,100%);max-height:80vh;display:flex;flex-direction:column"
      role="dialog"
      aria-modal="true"
      aria-labelledby="text-export-title"
      tabindex="-1"
    >
      <div class="modal-header">
        <h3 id="text-export-title"><SvgIcon name="clipboard" :size="15" /> {{ title }}</h3>
        <button class="btn btn-sm" aria-label="关闭" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>
      <p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">
        {{ hint }}
      </p>
      <textarea
        class="input"
        readonly
        :value="content"
        style="flex:1;min-height:320px;font-family:monospace;resize:none;width:100%"
      ></textarea>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">关闭</button>
        <button v-if="allowPush" class="btn" @click="emit('push')">推送企业微信</button>
        <button class="btn btn-primary" @click="copyText">{{ copyState === 'ok' ? '已复制' : (copyState === 'fail' ? '复制失败' : '复制') }}</button>
      </div>
    </div>
  </div>
</template>
