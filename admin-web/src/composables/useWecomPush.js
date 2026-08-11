import { reactive, ref } from 'vue'
import { api } from '../api/client'

export const PUSH_TYPE_NAMES = {
  sales_report_text: '销售报表文字版',
  data_quality_alert: '数据质量告警',
  test: '测试消息',
}

/** 阶段三：企微推送管理页面状态管理，1:1 迁移自原 public/wecom-push.html。 */
export function useWecomPush() {
  const webhooks = ref([])
  const jobs = ref([])
  const logs = ref([])
  const meta = ref({ push_types: [], job_templates: [] })
  const selectedJobId = ref(null)
  const previewContent = ref('')
  const previewMeta = ref({ bytes: 0, chunkCount: 1 })
  const loading = ref(false)
  const error = ref('')

  const webhookForm = reactive(emptyWebhookForm())
  const jobForm = reactive(emptyJobForm())

  function emptyWebhookForm() {
    return { id: '', name: '', webhook_url: '', notes: '', enabled: true }
  }
  function emptyJobForm() {
    return {
      id: '', name: '每日销售报表', push_type: 'sales_report_text', webhook_id: '',
      schedule_time: '21:30', date_range_mode: 'today', station: '', notes: '', enabled: true,
    }
  }

  function resetWebhookForm() { Object.assign(webhookForm, emptyWebhookForm()) }
  function resetJobForm() { Object.assign(jobForm, emptyJobForm()) }

  async function loadMeta() {
    meta.value = await api.get('/api/wecom-push/meta')
  }

  async function loadWebhooks() {
    const data = await api.get('/api/wecom-push/webhooks')
    webhooks.value = data.webhooks || []
  }

  async function loadJobs() {
    const data = await api.get('/api/wecom-push/jobs')
    jobs.value = data.jobs || []
    if (!selectedJobId.value && jobs.value.length) selectedJobId.value = jobs.value[0].id
  }

  async function loadLogs() {
    const data = await api.get('/api/wecom-push/logs', { limit: 80 })
    logs.value = data.logs || []
  }

  async function loadAll() {
    loading.value = true
    error.value = ''
    try {
      await loadMeta()
      await loadWebhooks()
      await loadJobs()
      await loadLogs()
    } catch (e) {
      error.value = e.message || '加载失败'
    } finally {
      loading.value = false
    }
  }

  function editWebhook(item) {
    Object.assign(webhookForm, { id: item.id, name: item.name, webhook_url: '', notes: item.notes || '', enabled: item.enabled })
  }

  async function saveWebhook() {
    const id = webhookForm.id
    const payload = {
      name: webhookForm.name.trim(),
      webhook_url: webhookForm.webhook_url.trim() || null,
      enabled: webhookForm.enabled,
      notes: webhookForm.notes.trim(),
    }
    if (!id && !payload.webhook_url) throw new Error('新建 webhook 必须填写地址')
    await api[id ? 'put' : 'post'](id ? `/api/wecom-push/webhooks/${id}` : '/api/wecom-push/webhooks', payload)
    resetWebhookForm()
    await loadWebhooks()
  }

  async function deleteWebhook(id) {
    await api.delete(`/api/wecom-push/webhooks/${id}`)
    await loadAll()
  }

  async function testWebhook(id) {
    const result = await api.post(`/api/wecom-push/webhooks/${id}/test`, {})
    await loadLogs()
    return result
  }

  function editJob(item) {
    selectedJobId.value = item.id
    Object.assign(jobForm, {
      id: item.id, name: item.name, webhook_id: item.webhook_id, push_type: item.push_type || 'sales_report_text',
      schedule_time: item.schedule_time, date_range_mode: item.date_range_mode, station: item.station || '',
      notes: item.notes || '', enabled: item.enabled,
    })
  }

  function applyJobTemplate(templateId) {
    const tpl = (meta.value.job_templates || []).find((t) => t.id === templateId)
    if (!tpl) throw new Error('模板不可用')
    Object.assign(jobForm, {
      id: '', name: tpl.name || '数据质量日报', push_type: tpl.push_type || 'data_quality_alert',
      schedule_time: tpl.schedule_time || '22:10', date_range_mode: tpl.date_range_mode || 'today',
      station: '', notes: tpl.notes || '', enabled: true,
    })
  }

  async function saveJob() {
    const id = jobForm.id
    const webhookId = Number(jobForm.webhook_id)
    if (!webhookId) throw new Error('请先选择 webhook')
    const payload = {
      name: jobForm.name.trim(),
      webhook_id: webhookId,
      push_type: jobForm.push_type,
      schedule_time: jobForm.schedule_time,
      date_range_mode: jobForm.date_range_mode,
      station: jobForm.push_type === 'data_quality_alert' ? '' : jobForm.station,
      enabled: jobForm.enabled,
      notes: jobForm.notes.trim(),
    }
    const data = await api[id ? 'put' : 'post'](id ? `/api/wecom-push/jobs/${id}` : '/api/wecom-push/jobs', payload)
    selectedJobId.value = (data.job && data.job.id) || Number(id) || selectedJobId.value
    resetJobForm()
    await loadJobs()
  }

  async function deleteJob(id) {
    await api.delete(`/api/wecom-push/jobs/${id}`)
    if (selectedJobId.value === id) selectedJobId.value = null
    await loadJobs()
  }

  async function previewSelectedJob() {
    if (!selectedJobId.value) throw new Error('请先选择任务')
    const data = await api.post(`/api/wecom-push/jobs/${selectedJobId.value}/preview`, {})
    previewContent.value = data.content || ''
    previewMeta.value = { bytes: data.byte_length || 0, chunkCount: data.chunk_count || 1 }
  }

  async function sendSelectedJob() {
    if (!selectedJobId.value) throw new Error('请先选择任务')
    const result = await api.post(`/api/wecom-push/jobs/${selectedJobId.value}/send-now`, {})
    await loadLogs()
    return result
  }

  return {
    webhooks, jobs, logs, meta, selectedJobId, previewContent, previewMeta, loading, error,
    webhookForm, jobForm, resetWebhookForm, resetJobForm,
    loadAll, loadWebhooks, loadJobs, loadLogs,
    editWebhook, saveWebhook, deleteWebhook, testWebhook,
    editJob, applyJobTemplate, saveJob, deleteJob,
    previewSelectedJob, sendSelectedJob,
  }
}
