import { reactive, ref } from 'vue'
import { api } from '../api/client'

export const PUSH_TYPE_NAMES = {
  sales_report_text: '销售报表文字版',
  data_quality_alert: '数据质量告警',
  test: '测试消息',
}

/** 删除确认文案：标了卫生群的地址要额外点名 —— 误删之后卫生消息就无处可发了。 */
export function webhookDeleteConfirmText(item) {
  if (item && item.hygiene_feed) {
    return `「${item.name}」是卫生群，删除后卫生提醒与验收照片不会再发到它。确定删除？`
  }
  return '确定删除该 webhook？'
}

/**
 * 没有「启用中的卫生群」时的顶部提示；有可用的群就返回空串。
 *
 * 这条提示是那个空态的可见性兜底：卫生消息这时**一条都不发**，页面上不写出来，
 * 店长只会以为"今天没有漏拍"。
 */
export function hygieneFeedWarning(webhooks) {
  const usable = (webhooks || []).filter((item) => item.hygiene_feed && item.enabled)
  if (usable.length) return ''
  return '还没有指定卫生群：卫生提醒与验收照片当前不会发出。请在下面的地址上勾选「这是卫生群」。'
}

/** 发送类型的短名：确认框里"销售报表 / 数据质量摘要"比内部标识好读。 */
export function pushTypeLabel(pushType) {
  if (pushType === 'data_quality_alert') return '数据质量摘要'
  return '销售报表'
}

/**
 * 「立即发送」的确认文案。
 *
 * 原来的文案是「确定立即发送当前预览对应的销售报表？」——**预览区空着的时候也这么说**：
 * 页面显示「0 / 2048 字节」「选择任务后点击刷新预览」，确认框却在描述一份"当前预览"，
 * 用户既不知道会发出什么，也不知道发给哪个群。外发消息撤不回来，确认框是最后一道闸门，
 * 它必须说清"发给谁、多少字节、开头长什么样"。
 *
 * @param {{ job?: object|null, bytes?: number, content?: string }} args
 *   `job` 是当前选中的推送任务（带 name / webhook_name），`bytes`/`content` 来自预览。
 * @returns {string}
 */
export function sendNowConfirmText({ job = null, bytes = 0, content = '' } = {}) {
  const typeLabel = pushTypeLabel(job?.push_type)
  const target = job?.webhook_name ? `「${job.webhook_name}」` : '（任务未配置目标群）'
  const lines = [`确定立即发送${typeLabel}到 ${target} 吗？`]

  const size = Number(bytes) || 0
  // 预览为空时说清这一点：后端会自己现算一份内容发出去（send-now 不读预览），
  // 所以"空预览"不等于"发空消息"，但用户必须知道自己没核对过内容。
  if (!content) {
    lines.push('注意：当前没有预览内容，将按任务配置现算后发送，你没有核对过正文。')
  }
  lines.push(`消息大小 ${size} / 2048 字节。`)

  const head = String(content || '').split('\n').map((line) => line.trim()).find(Boolean)
  if (head) {
    lines.push(`正文首行：${head.length > 40 ? `${head.slice(0, 40)}…` : head}`)
  }
  lines.push('消息发出后无法撤回。')
  return lines.join('\n')
}

/** 预览为空时不允许「立即发送」：没有可核对的内容就外发，是这一页最容易误触的一条。 */
export function canSendNow({ job = null, content = '' } = {}) {
  return Boolean(job) && String(content || '').trim().length > 0
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
    return { id: '', name: '', webhook_url: '', notes: '', enabled: true, hygiene_feed: false }
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
    Object.assign(webhookForm, {
      id: item.id, name: item.name, webhook_url: '', notes: item.notes || '',
      enabled: item.enabled, hygiene_feed: !!item.hygiene_feed,
    })
  }

  async function saveWebhook() {
    const id = webhookForm.id
    const payload = {
      name: webhookForm.name.trim(),
      webhook_url: webhookForm.webhook_url.trim() || null,
      enabled: webhookForm.enabled,
      hygiene_feed: !!webhookForm.hygiene_feed,
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

  async function toggleWebhookEnabled(item) {
    // 列表上的快捷开关：只切 enabled，webhook_url 传 null 表示不动原地址
    // （后端 `if payload.webhook_url:` 才覆盖，见 api/wecom_push.py）。
    // hygiene_feed 原样回传是双保险：后端只在明确给值时才写这一列，不发也不会被清掉。
    await api.put(`/api/wecom-push/webhooks/${item.id}`, {
      name: item.name,
      webhook_url: null,
      enabled: !item.enabled,
      hygiene_feed: !!item.hygiene_feed,
      notes: item.notes || '',
    })
    await loadWebhooks()
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
    editWebhook, saveWebhook, deleteWebhook, toggleWebhookEnabled, testWebhook,
    editJob, applyJobTemplate, saveJob, deleteJob,
    previewSelectedJob, sendSelectedJob,
  }
}
