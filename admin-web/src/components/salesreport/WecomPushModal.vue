<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api/client'
import SvgIcon from '../SvgIcon.vue'

const props = defineProps({
  mode: { type: String, default: 'report' }, // 'report' | 'semi'
  content: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const webhooks = ref([])
const selectedWebhook = ref('')
const status = ref('加载 webhook...')

onMounted(load)

async function load() {
  try {
    const data = await api.get('/api/wecom-push/webhooks')
    webhooks.value = (data.webhooks || []).filter((w) => w.enabled)
    status.value = webhooks.value.length
      ? (props.mode === 'semi' ? '选择目标后即可发送半成品用量' : '选择目标后即可发送当前文字版报表')
      : '请先在企微推送页配置并启用 webhook'
  } catch (e) {
    status.value = e.message || '加载失败'
  }
}

async function send() {
  if (!selectedWebhook.value) {
    status.value = '请选择 webhook'
    return
  }
  status.value = '发送中...'
  try {
    const result = await api.post('/api/wecom-push/send-text', {
      webhook_id: Number(selectedWebhook.value),
      content: props.content,
      push_type: 'sales_report_text',
    })
    status.value = result.success ? '已发送到企业微信' : `发送失败：${result.error || result.response_text || ''}`
  } catch (e) {
    status.value = `发送失败：${e.message}`
  }
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-box" style="width:480px">
      <div class="modal-header">
        <h3>{{ mode === 'semi' ? '推送半成品用量到企业微信' : '推送当前报表到企业微信' }}</h3>
        <button class="btn btn-sm" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>
      <p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">
        {{ mode === 'semi' ? '发送的是当前日期、档口筛选下的半成品用量。' : '发送的是当前日期、档口筛选下的文字版报表。' }}
      </p>
      <select class="select" v-model="selectedWebhook" style="width:100%">
        <option value="">— 选择 webhook —</option>
        <option v-for="w in webhooks" :key="w.id" :value="w.id">{{ w.name }} · {{ w.webhook_url_masked }}</option>
      </select>
      <p style="font-size:12px;color:var(--text-dim);margin-top:8px">{{ status }}</p>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">关闭</button>
        <button class="btn btn-primary" @click="send">发送</button>
      </div>
    </div>
  </div>
</template>
