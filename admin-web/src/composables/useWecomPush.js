import { computed, reactive, ref } from 'vue'
import { api } from '../api/client'

/**
 * 本页的接口版本。**必须与后端 `services/wecom_push_topics.py` 的
 * `WECOM_PUSH_API_VERSION` 逐字一致** —— 页面加载时拿 `/meta` 返回的那个值比对，
 * 对不上就在顶部显示提示条（不阻断操作）。
 *
 * 「本地构建版本」就是这一份常量：页面 bundle 与它一起打包，改了后端接口形状又忘了
 * 改前端时，旧 bundle 里的值会与 `/meta` 对不上，店长立刻看得见（而不是以为改了、
 * 其实没生效）。漂移由两边的测试钉住：
 * `admin-web/src/composables/__tests__/useWecomPushSubscriptions.test.js`
 * 与 `tests/test_wecom_push_channels_api.py`。
 */
export const WECOM_PUSH_API_VERSION = 'v1'

/**
 * 订阅视图切「多选列表」的渠道数阈值。
 *
 * 渠道不超过这个数时用「内容类型 × 渠道」的勾选矩阵；超过就把列收起来，改成
 * 「先选内容类型、再勾渠道」的多选列表 —— 手机上一行塞十个勾选框点不准。
 * （spec 的默认值 8，可在实施时调整；两处引用这一个常量。）
 */
export const MATRIX_CHANNEL_LIMIT = 8

/** 多选列表模式的标识（`matrixViewMode` 的返回值）。 */
export const MATRIX_VIEW_MULTI_SELECT = 'multi-select'

/** 某种视图模式下怎么显示（矩阵 / 多选列表）。 */
export function matrixViewMode(channels) {
  return (channels || []).length > MATRIX_CHANNEL_LIMIT
    ? MATRIX_VIEW_MULTI_SELECT
    : 'matrix'
}

/**
 * 发送记录一页多少条（票 07）。
 *
 * 与后端 `/logs` 的 `page_size` 对齐：不给 `page_size` 时后端退回旧的 `limit`（默认
 * 50），两边默认值必须一样，否则「翻到底了没有」会在页面与后端之间对不上。
 */
export const DELIVERY_PAGE_SIZE = 50

/**
 * 出站状态在页面上的名字（值域与 `db_core/wecom_subscriptions_repo.py` 的
 * `SUB_OUTBOX_STATUS_*` 一致）。
 *
 * 「已经发出去了」（已发）与「还没轮到 / 正在发」（待发 / 发送中）必须分开显示：
 * 店长查「这封到底发了没有」时看到「待发」却以为已经发了，是最要命的一种误读。
 */
export const DELIVERY_STATUS_OPTIONS = [
  { id: 'pending', name: '待发' },
  { id: 'sending', name: '发送中' },
  { id: 'sent', name: '已发' },
  { id: 'failed', name: '失败' },
  { id: 'skipped', name: '已跳过' },
]

/** 出站状态的显示名；注册表里没有这个值就退回原值（不吞掉这一行）。 */
export function deliveryStatusLabel(status) {
  const matched = DELIVERY_STATUS_OPTIONS.find((item) => item.id === status)
  return matched ? matched.name : String(status || '')
}

/** 记录里的时间戳按秒显示；没有值就给占位符（空字符串直接渲染会是一片空白）。 */
export function formatDeliveryTime(value) {
  const raw = String(value || '')
  if (!raw) return '—'
  return raw.replace('T', ' ').slice(0, 19)
}

/**
 * 发送记录一页的查询参数。
 *
 * 空筛选**不发这个键**：`api.get` 会把空串整个丢掉，后端也就分得清「没筛这一项」与
 * 「按空值筛」（后者在 `topic_id=` 上一行都筛不出来）。带哪些键只在这里决定。
 */
export function deliveryQuery({ topicId = '', channelId = '', status = '', page = 1 } = {}) {
  const params = { page: Number(page) || 1, page_size: DELIVERY_PAGE_SIZE }
  const topic = String(topicId || '').trim()
  const channel = channelId === 0 || channelId ? Number(channelId) : 0
  const state = String(status || '').trim()
  if (topic) params.topic_id = topic
  if (channel) params.channel_id = channel
  if (state) params.status = state
  return params
}

/** 矩阵里有几行订阅（行 = 内容类型）。 */
export function matrixTopics(matrix) {
  return (matrix && matrix.topics) || []
}

/** 某一行的零订阅判定：一个**启用中**的渠道都没有，就是这类内容当前一条都不发。
 *
 * 「订阅了但渠道停着」也算零订阅 —— 投递会跳过停用渠道（出站记 skipped），页面上不
 * 亮出来，店长会以为还在发（用户故事 9 要的正是这个可见性）。所以行里带的是
 * `{id, enabled}` 而不是光秃秃的 id 数组：停用状态只有后端知道。
 */
export function topicIsUnsubscribed(row) {
  const subscribed = (row && row.channels) || []
  return !subscribed.some((channel) => channel && channel.enabled)
}

/** 某一行订阅到的渠道 id（矩阵打勾、多选列表做差集都用它）。 */
export function topicChannelIds(row) {
  return ((row && row.channels) || []).map((channel) => Number(channel.id))
}

/** 零订阅的内容类型显示名（页面上要高亮这些行）。 */
export function matrixZeroTopicNames(matrix) {
  return matrixTopics(matrix)
    .filter(topicIsUnsubscribed)
    .map((row) => row.name || row.id)
}

/**
 * 零订阅的顶部提示；一处都没有就返回空串。
 *
 * 这条提示是「以为还在发、其实停了」的可见性兜底（用户故事 9）：卫生提醒这类内容
 * 一个收件人都没有时，页面上不写出来，店长只会以为今天没有漏拍。
 */
export function zeroSubscriptionWarning(matrix) {
  const names = matrixZeroTopicNames(matrix)
  if (!names.length) return ''
  return `以下内容当前没有任何渠道订阅，不会发出：${names.join('、')}。请在「订阅」里勾选收件渠道。`
}

/**
 * 页面与服务端接口版本不一致时的顶部提示；对得上（或拿不到版本）返回空串。
 *
 * 拿不到版本（旧后端、请求失败）**不提示**：这时页面还没有资格判断谁旧谁新，
 * 提示条会变成噪音。
 */
export function contractMismatchWarning(meta) {
  const remote = (meta && meta.api_version) || ''
  if (!remote || remote === WECOM_PUSH_API_VERSION) return ''
  return `页面与服务端接口版本不一致（页面 ${WECOM_PUSH_API_VERSION} / 服务端 ${remote}），`
    + '请刷新页面（Ctrl/Cmd+Shift+R）后再操作；当前显示的字段可能已过期。'
}

/** 渠道卡片上「所属群组」那一行的文本。 */
export function channelGroupNames(channel) {
  return ((channel && channel.groups) || []).map((g) => g.name || g.id).join('、')
}

/** 渠道卡片上「订阅了哪些内容」那一行的文本。
 *
 * 名字是纯名字：订阅「经群组来的」还是「直接订的」，页面上用单独一枚徽章标
 * （`channel.topics[].via_group`）—— 混进这一行会让店长在核对时多读几个字。
 */
export function channelTopicNames(channel) {
  return ((channel && channel.topics) || []).map((topic) => topic.name || topic.id).join('、')
}

/** 「最近一次发送成功」的展示串；从未发过就说清这一点（那是渠道失效的第一信号）。 */
export function formatSentAt(channel) {
  const raw = String((channel && channel.last_sent_at) || '')
  if (!raw) return '从未发送'
  return raw.replace('T', ' ').slice(0, 19)
}

/**
 * 删除渠道的确认文案。
 *
 * 后端对「被推送任务引用」的删除是**拒绝**的（消息里点名任务）；这里先在前端说清
 * 后果：订阅与群组成员会跟着走，而被任务引用时会被拦下来。外发配置删错了要重配一遍，
 * 确认框是最后一道闸门。
 */
export function channelDeleteConfirmText(channel) {
  if (!channel) return '确定删除该渠道？'
  const name = channel.name || channel.id
  const jobs = Number(channel.job_count) || 0
  if (jobs > 0) {
    return `「${name}」被 ${jobs} 条推送任务引用，删除会被拒绝；请先改绑或删除那些任务。`
  }
  const topics = (channel.topics || []).length
  const groups = (channel.groups || []).length
  const extras = []
  if (topics) extras.push(`它的 ${topics} 条订阅会一并取消`)
  if (groups) extras.push(`它会从 ${groups} 个群组里移除`)
  const tail = extras.length ? `${extras.join('，')}。` : ''
  return `确定删除渠道「${name}」？${tail}删除后要重新配置才能恢复。`
}

/** 发送类型的短名：确认框里「销售报表 / 数据质量摘要」比内部标识好读。 */
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

/** 订阅的「保存后下一次触发即按新订阅投递」：勾选写哪一行由这里定死。 */
function subscriptionPayload({ topicId, channelId, groupId, enabled }) {
  const payload = { topic_id: topicId, enabled: !!enabled }
  if (groupId) payload.target_group_id = Number(groupId)
  else payload.target_channel_id = Number(channelId)
  return payload
}

/** 企微推送管理页面状态：渠道 / 订阅两个 tab（票 06）+ 定时任务 / 发送记录（原样）。 */
export function useWecomPush() {
  const meta = ref({ topics: [], job_templates: [] })
  const channels = ref([])
  const topics = ref([])
  const groups = ref([])
  const jobs = ref([])
  // 发送记录（票 07）：一页记录 + 筛选 + 总数，页面上的表格与分页条都读它。
  const deliveries = reactive({
    rows: [],
    total: 0,
    page: 1,
    pages: 0,
    filters: { topicId: '', channelId: '', status: '' },
  })
  const matrix = ref({ topics: [], channels: [] })
  const selectedJobId = ref(null)
  const previewContent = ref('')
  const previewMeta = ref({ bytes: 0, chunkCount: 1 })
  const loading = ref(false)
  const error = ref('')

  const activeTab = ref('channels')
  const channelForm = reactive(emptyChannelForm())
  const channelGroupForm = reactive(emptyChannelGroupForm())
  const multiSelect = reactive({ topicId: '', channelIds: [] })

  function emptyChannelForm() {
    return { id: '', name: '', webhook_url: '', notes: '', enabled: true, group_ids: [] }
  }
  function emptyChannelGroupForm() {
    return { id: '', name: '', notes: '', enabled: true }
  }
  function emptyJobForm() {
    return {
      id: '', name: '每日销售报表', push_type: 'sales_report_text', webhook_id: '',
      schedule_time: '21:30', date_range_mode: 'today', station: '', notes: '', enabled: true,
    }
  }

  function resetChannelForm() { Object.assign(channelForm, emptyChannelForm()) }
  function resetChannelGroupForm() { Object.assign(channelGroupForm, emptyChannelGroupForm()) }
  function resetJobForm() { Object.assign(jobForm, emptyJobForm()) }

  const jobForm = reactive(emptyJobForm())

  // ── 顶部提示：接口版本与零订阅 ────────────────────────────────────────────
  const contractWarning = computed(() => contractMismatchWarning(meta.value))
  const zeroSubscriptionTip = computed(() => zeroSubscriptionWarning(matrix.value))
  const viewMode = computed(() => matrixViewMode(matrix.value.channels || channels.value))
  const zeroTopicIds = computed(() => matrixTopics(matrix.value)
    .filter(topicIsUnsubscribed)
    .map((row) => row.id))

  // ── 读 ────────────────────────────────────────────────────────────────────
  async function loadMeta() {
    meta.value = await api.get('/api/wecom-push/meta')
  }

  /** 一次把渠道 / 群组 / 矩阵拉齐：三份数据同源，页面不必自己拼。 */
  async function loadChannels() {
    const data = await api.get('/api/wecom-push/webhooks')
    channels.value = data.channels || []
    groups.value = data.groups || []
    topics.value = data.topics || []
    matrix.value = { topics: data.topics || [], channels: data.channels || [] }
  }

  async function loadSubscriptions() {
    const data = await api.get('/api/wecom-push/subscriptions')
    // 每条读接口都带着自己的接口版本：整页只在启动时拉一次 /meta，页面开着不动的
    // 那段时间里后端换了版本的话，只有这里能看出来（提示条是同一份文案，不阻断）。
    if (data.api_version) meta.value = { ...meta.value, api_version: data.api_version }
    matrix.value = { topics: data.topics || [], channels: data.channels || [] }
    if (!topics.value.length) topics.value = data.topics || []
    if (!channels.value.length) channels.value = data.channels || []
    return matrix.value
  }

  async function loadChannelGroups() {
    const data = await api.get('/api/wecom-push/channel-groups')
    groups.value = data.groups || []
    return groups.value
  }

  async function loadJobs() {
    const data = await api.get('/api/wecom-push/jobs')
    jobs.value = data.jobs || []
    if (!selectedJobId.value && jobs.value.length) selectedJobId.value = jobs.value[0].id
  }

  /**
   * 发送记录：页面上要的是一页（后端算好总数），不是「最近 N 条」。
   *
   * 切到统一出站表之后（ADR 0095），**这一条就是这一页唯一的记录来源**：旧表那一份
   * （`/logs` 的 `logs` 键）只为缓存着旧 bundle 的浏览器留着，页面不读它。所以这里
   * 连 `limit` 都不发 —— 一页多少条由页码与 `page_size` 决定。
   */
  async function loadDeliveries({ page = null } = {}) {
    if (page !== null) deliveries.page = Math.max(1, Number(page) || 1)
    const data = await api.get('/api/wecom-push/logs', deliveryQuery({
      topicId: deliveries.filters.topicId,
      channelId: deliveries.filters.channelId,
      status: deliveries.filters.status,
      page: deliveries.page,
    }))
    deliveries.rows = data.rows || []
    deliveries.total = Number(data.total) || 0
    deliveries.pages = Number(data.pages) || 0
    deliveries.page = Number(data.page) || deliveries.page
    return deliveries
  }

  /** 改筛选：**回到第一页**（停在第 4 页时换了筛选条件，那一页往往已经不存在）。 */
  async function applyDeliveryFilters({ topicId = '', channelId = '', status = '' } = {}) {
    deliveries.filters.topicId = topicId
    deliveries.filters.channelId = channelId
    deliveries.filters.status = status
    deliveries.page = 1
    return loadDeliveries()
  }

  async function loadAll() {
    loading.value = true
    error.value = ''
    try {
      await loadMeta()
      await loadChannels()
      await loadJobs()
    } catch (e) {
      error.value = e.message || '加载失败'
    } finally {
      loading.value = false
    }
  }

  // ── 渠道 ──────────────────────────────────────────────────────────────────
  function editChannel(item) {
    Object.assign(channelForm, {
      id: item.id,
      name: item.name,
      webhook_url: '',
      notes: item.notes || '',
      enabled: !!item.enabled,
      group_ids: (item.groups || []).map((group) => group.id),
    })
  }

  async function saveChannel() {
    const id = channelForm.id
    const payload = {
      name: channelForm.name.trim(),
      webhook_url: channelForm.webhook_url.trim() || null,
      enabled: !!channelForm.enabled,
      notes: channelForm.notes.trim(),
    }
    if (!id && !payload.webhook_url) throw new Error('新建渠道必须填写 webhook 地址')
    // 先记下**保存前**的成员关系：保存成功那一刻页面上的 groups 还是旧的，拿保存后
    // 再拉的列表算差集会把「这次加进去的」也算成「已经在了」。
    const before = groups.value
    const data = await api[id ? 'put' : 'post'](
      id ? `/api/wecom-push/webhooks/${id}` : '/api/wecom-push/webhooks',
      payload,
    )
    const channelId = (data.channel && data.channel.id) || Number(id) || 0
    if (channelId) await syncChannelGroups(channelId, channelForm.group_ids, before)
    resetChannelForm()
    await loadChannels()
    return channelId
  }

  /**
   * 把渠道的群组成员关系对齐到勾选结果。
   *
   * 成员关系是独立的接口（一个渠道可以属于多个群组），所以这里算出差集：新增的加、
   * 取消的删，已经对上的不动 —— 每次保存都全删全加会把别的页面正在看的关系也抖一遍。
   * `knownGroups` 是「这次勾选之前的成员关系」（调用方在写渠道**之前**取的那一份）。
   */
  async function syncChannelGroups(channelId, wantedIds, knownGroups = null) {
    const wanted = new Set((wantedIds || []).map(Number))
    const current = new Set(
      (knownGroups || groups.value)
        .filter((group) => (group.member_channel_ids || []).map(Number).includes(Number(channelId)))
        .map((group) => Number(group.id)),
    )
    for (const groupId of wanted) {
      if (!current.has(groupId)) await addGroupMember({ groupId, channelId })
    }
    for (const groupId of current) {
      if (!wanted.has(groupId)) await removeGroupMember({ groupId, channelId })
    }
  }

  async function deleteChannel(id) {
    const data = await api.delete(`/api/wecom-push/webhooks/${id}`)
    await loadAll()
    return data
  }

  async function toggleChannelEnabled(item) {
    // 快捷开关只切 enabled，webhook_url 传 null 表示不动原地址（后端 `if payload.webhook_url:`
    // 才覆盖）。不碰 hygiene_feed：那一列已退役，新页面不再显示也不再写它。
    await api.put(`/api/wecom-push/webhooks/${item.id}`, {
      name: item.name,
      webhook_url: null,
      enabled: !item.enabled,
      notes: item.notes || '',
    })
    await loadChannels()
  }

  async function testChannel(id) {
    const result = await api.post(`/api/wecom-push/webhooks/${id}/test`, {})
    await loadChannels()
    return result
  }

  // ── 群组与成员 ────────────────────────────────────────────────────────────
  function editChannelGroup(item) {
    Object.assign(channelGroupForm, {
      id: item.id, name: item.name, notes: item.notes || '', enabled: !!item.enabled,
    })
  }

  async function saveChannelGroup() {
    const id = channelGroupForm.id
    const payload = {
      name: channelGroupForm.name.trim(),
      enabled: !!channelGroupForm.enabled,
      notes: channelGroupForm.notes.trim(),
    }
    if (!payload.name) throw new Error('群组名称不能为空')
    const data = await api[id ? 'put' : 'post'](
      id ? `/api/wecom-push/channel-groups/${id}` : '/api/wecom-push/channel-groups',
      payload,
    )
    resetChannelGroupForm()
    await loadChannels()
    return data.group
  }

  async function deleteChannelGroup(id) {
    await api.delete(`/api/wecom-push/channel-groups/${id}`)
    await loadChannels()
  }

  async function addGroupMember({ groupId, channelId }) {
    const data = await api.post(`/api/wecom-push/channel-groups/${groupId}/members`, {
      channel_id: Number(channelId),
    })
    return data.group
  }

  async function removeGroupMember({ groupId, channelId }) {
    const data = await api.delete(
      `/api/wecom-push/channel-groups/${groupId}/members/${channelId}`,
    )
    return data.group
  }

  // ── 订阅 ──────────────────────────────────────────────────────────────────
  async function toggleSubscription({ topicId, channelId, groupId = null, enabled }) {
    const data = await api.post(
      '/api/wecom-push/subscriptions',
      subscriptionPayload({ topicId, channelId, groupId, enabled }),
    )
    await loadSubscriptions()
    return data
  }

  /** 矩阵里某一行当前勾了哪些渠道（多选列表用它做差集）。 */
  function subscribedChannelIds(topicId) {
    const row = matrixTopics(matrix.value).find((item) => item.id === topicId)
    return ((row && row.channels) || []).map(Number)
  }

  function pickMultiSelectTopic(topicId) {
    multiSelect.topicId = topicId
    multiSelect.channelIds = subscribedChannelIds(topicId)
  }

  /**
   * 多选列表的保存：把「这次勾的」与「原来勾的」做差集，只发变化的那几条。
   *
   * 全量重发会把没动过的渠道也写一遍（更新时间被刷、页面上的「最近变更」也会漂），
   * 而且渠道多起来之后就是几十次没必要的写。
   */
  async function saveMultiSelectTopic() {
    const topicId = multiSelect.topicId
    if (!topicId) throw new Error('请先选择内容类型')
    const before = new Set(subscribedChannelIds(topicId))
    const after = new Set((multiSelect.channelIds || []).map(Number))
    for (const channelId of after) {
      if (!before.has(channelId)) {
        await api.post('/api/wecom-push/subscriptions',
          subscriptionPayload({ topicId, channelId, enabled: true }))
      }
    }
    for (const channelId of before) {
      if (!after.has(channelId)) {
        await api.post('/api/wecom-push/subscriptions',
          subscriptionPayload({ topicId, channelId, enabled: false }))
      }
    }
    await loadSubscriptions()
  }

  // ── 定时任务与发送记录（票 07/08 重做，本票原样保留）────────────────────────
  function editJob(item) {
    selectedJobId.value = item.id
    Object.assign(jobForm, {
      id: item.id, name: item.name, webhook_id: item.webhook_id,
      push_type: item.push_type || 'sales_report_text',
      schedule_time: item.schedule_time, date_range_mode: item.date_range_mode,
      station: item.station || '', notes: item.notes || '', enabled: item.enabled,
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
    if (!webhookId) throw new Error('请先选择渠道')
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
    const data = await api[id ? 'put' : 'post'](
      id ? `/api/wecom-push/jobs/${id}` : '/api/wecom-push/jobs', payload)
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
    // 这条旧入口不走统一出站、也不落发送记录（票 08 把定时任务切过去），所以这里只把
    // 记录页重新拉一遍：本轮新入队的投递（如果有）立刻可见。
    await loadDeliveries()
    return result
  }

  return {
    meta, channels, topics, groups, jobs, matrix, deliveries,
    selectedJobId, previewContent, previewMeta, loading, error,
    activeTab, channelForm, channelGroupForm, multiSelect,
    contractWarning, zeroSubscriptionTip, viewMode, zeroTopicIds,
    resetChannelForm, resetChannelGroupForm, resetJobForm, jobForm,
    loadAll, loadMeta, loadChannels, loadSubscriptions, loadChannelGroups, loadJobs,
    loadDeliveries, applyDeliveryFilters,
    editChannel, saveChannel, deleteChannel, toggleChannelEnabled, testChannel,
    editChannelGroup, saveChannelGroup, deleteChannelGroup,
    addGroupMember, removeGroupMember, syncChannelGroups,
    toggleSubscription, subscribedChannelIds, pickMultiSelectTopic, saveMultiSelectTopic,
    editJob, applyJobTemplate, saveJob, deleteJob, previewSelectedJob, sendSelectedJob,
  }
}
