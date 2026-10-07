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
export const WECOM_PUSH_API_VERSION = 'v2'

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

/**
 * 企微群机器人常见 errcode 的人话（UI 走查 U16）。
 *
 * 发送记录的「最后一次错误」原来显示的是企微返回的英文原文（`api freq out of limit`）或
 * Python 异常栈里的一行（`HTTPSConnectionPool(...): Read timed out.`）——那是给排查的人看
 * 的，不是给店长看的：他要知道的是「要不要做点什么」。原文一个字都不丢，进「详情」。
 *
 * 只列**能给出下一步**的那几个码；注册表里没有的码原样显示，不猜。
 */
export const WECOM_ERRCODE_HINTS = {
  40001: 'webhook 地址里的 key 无效或已失效，请重新生成后更新渠道',
  40003: '企业微信应用 ID 不合法，请联系企业微信管理员',
  40008: '企业微信不接受这条消息的内容格式',
  40013: '企业微信应用 ID 不合法，请联系企业微信管理员',
  40014: '访问凭证已过期，请重新生成',
  41001: '缺少访问凭证，请重新生成 webhook 地址',
  45009: '触发企业微信调用频率上限（当天额度用尽），次日或稍后会自动重试',
  45033: '企业微信并发超限，稍后会自动重试',
  45047: '企业微信下行消息条数超过上限，稍后会自动重试',
  93000: '机器人已被移出这个群，或 webhook 已停用，请检查群里还有没有这个机器人',
}

/**
 * 认得出但不是 errcode 的那几种失败（网络层最常见）。
 *
 * 顺序有意义：先匹配到的先用。
 */
export const WECOM_ERROR_PATTERNS = [
  [/invalid webhook url/i, 'webhook 地址无效，请到「渠道」里重新填写'],
  [/Read timed out|read timeout|ConnectTimeout|timed out/i, '连接企业微信超时（网络不通或对方响应慢），稍后会自动重试'],
  [/HTTPSConnectionPool|Connection refused|Max retries exceeded|NewConnectionError|Name or service not known|Temporary failure in name resolution/i, '连不上企业微信服务器（网络或 DNS 问题），稍后会自动重试'],
]

/** 从错误原文里取 errcode；认不出返回 0。 */
export function wecomErrcode(text) {
  const matched = /errcode["'\s]*[=:：]\s*(-?\d+)/i.exec(String(text || ''))
  return matched ? Number(matched[1]) : 0
}

/**
 * 「最后一次错误」在表格里显示的那一句话（UI 走查 U16）。
 *
 * 认得出就把原文翻成人话；认不出**原样返回**——宁可让店长看见一行英文，也不能把失败
 * 原因吞掉（这时行内的「详情」按钮仍然能展开完整原文）。
 */
export function deliveryErrorSummary(text) {
  const raw = String(text || '').trim()
  if (!raw) return ''
  const hint = WECOM_ERRCODE_HINTS[wecomErrcode(raw)]
  if (hint) return hint
  const matched = WECOM_ERROR_PATTERNS.find(([pattern]) => pattern.test(raw))
  return matched ? matched[1] : raw
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

/**
 * 「变更历史」一页多少条（票 11）。
 *
 * 与后端 `/audit-log` 的 `page_size` 默认值对齐（同一套翻页手感，也和发送记录一致）：
 * 两边默认值不一样的话，「翻到底了没有」会在页面与后端之间对不上。
 */
export const AUDIT_PAGE_SIZE = 50

/**
 * 对象类型的中文名（值就是后端的表名）。
 *
 * 这份表只是**兜底**：读接口会在 `object_types` 里给出它自己的名单与名字，页面下拉读那
 * 一份（加一种对象类型只改后端）。这里留着是为了「记录里的对象类型在后端名单里已经不在」
 * 时仍显示得出中文（旧记录不该变成一串表名）。
 */
export const AUDIT_OBJECT_LABELS = {
  wecom_push_webhooks: '推送渠道',
  wecom_channel_groups: '渠道群组',
  wecom_push_subscriptions: '推送订阅',
  wecom_push_jobs: '推送任务',
}

/** 动作的中文名（后端 `actions` 的同一份口径，见上一条的理由）。 */
export const AUDIT_ACTION_LABELS = {
  create: '新增',
  update: '修改',
  enable: '启用',
  disable: '停用',
  delete: '删除',
}

/**
 * 快照里那些字段的中文名（页面上「变更内容」一列逐项显示）。
 *
 * 字段名是领域词（`enabled` / `target_name` / `members`……），翻不出来就原样显示 ——
 * 哪天后端加了一个字段，页面上多出一个英文名，总好过整条记录看不见。
 */
export const AUDIT_FIELD_LABELS = {
  name: '名称',
  enabled: '启用',
  url: 'Webhook 地址',
  members: '成员',
  topic_id: '内容类型',
  target_name: '目标',
  target_channel_id: '目标渠道',
  target_group_id: '目标群组',
  schedule_time: '推送时间',
}

/** 动作的中文名；认不出的动作退回原值（不吞掉这一行）。 */
export function auditActionLabel(action) {
  return AUDIT_ACTION_LABELS[action] || String(action || '')
}

/** 对象类型的中文名；认不出的退回原值。 */
export function auditObjectLabel(objectType) {
  return AUDIT_OBJECT_LABELS[objectType] || String(objectType || '')
}

/** 快照字段的中文名；认不出的原样显示。 */
export function auditFieldLabel(field) {
  return AUDIT_FIELD_LABELS[field] || String(field || '')
}

/**
 * 变更内容里**不显示**的字段：行内 id 与已经用名字表达过的外键。
 *
 * 它们照旧存在审计记录里（排查要靠它们），只是不该出现在页面上：目标已经写成「目标：
 * 门店群」，再列一行「目标渠道：2」只是噪音，而 `topic_id` 早就进了对象名
 * （「销售报表 → 门店群」）。`id` 同理 —— 那是页面的 key，不是人读的变更。
 */
const AUDIT_HIDDEN_FIELDS = new Set([
  'id', 'topic_id', 'target_channel_id', 'target_group_id',
])

/** 「变更内容」一列要渲染的字段（顺序 = 快照里的键序）。 */
export function auditVisibleFields(row) {
  return Object.entries(auditFieldChanges(row))
    .filter(([field]) => !AUDIT_HIDDEN_FIELDS.has(field))
}

/** 一个快照值的显示文本。
 *
 * 布尔按「启用 / 停用」渲染（快照里最常见的就是那一列），空值给占位符 ——
 * 直接渲染成空白会让人分不清「没这一项」与「被清空了」。
 */
export function formatAuditValue(value) {
  if (value === true) return '启用'
  if (value === false) return '停用'
  if (value === null || value === undefined || value === '') return '（空）'
  if (Array.isArray(value)) {
    return value.length ? value.map((item) => formatAuditValue(item)).join('、') : '（空）'
  }
  return String(value)
}

/**
 * 变更内容：``{字段: {before, after}}``，只留**真的变了**的字段。
 *
 * 新建（没有改前）与删除（没有改后）整份快照都算变更 —— 页面上那两条也要看得见写了
 * 什么、删了什么。与后端 `services/wecom_audit.py` 的 `changed_fields` 同一口径：
 * 页面不自己另算一份「什么算变了」。
 */
export function auditFieldChanges(row) {
  const before = (row && row.before) || {}
  const after = (row && row.after) || {}
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
  const changes = {}
  for (const key of keys) {
    if (before[key] !== after[key]) {
      changes[key] = {
        before: Object.prototype.hasOwnProperty.call(before, key) ? before[key] : null,
        after: Object.prototype.hasOwnProperty.call(after, key) ? after[key] : null,
      }
    }
  }
  return changes
}

/** 「变更内容」一列的一行文本：``名称：门店群 → 门店群（改名）``。 */
export function auditChangeText(field, change) {
  const label = auditFieldLabel(field)
  /** 删除（没有改后）与新建（没有改前）只说一头，别写成「X → （空）」。 */
  if (!change || change.before === null || change.before === undefined) {
    return `${label}：${formatAuditValue(change && change.after)}`
  }
  if (change.after === null || change.after === undefined) {
    return `${label}：${formatAuditValue(change.before)}（已删除）`
  }
  return `${label}：${formatAuditValue(change.before)} → ${formatAuditValue(change.after)}`
}

/**
 * 变更历史一页的查询参数。
 *
 * 空筛选**不发这个键**（与发送记录同一条规矩）：`api.get` 会把空串丢掉，后端也就分得清
 * 「没筛这一项」与「按空值筛」。
 */
export function auditQuery({ objectType = '', page = 1 } = {}) {
  const params = { page: Number(page) || 1, page_size: AUDIT_PAGE_SIZE }
  const type = String(objectType || '').trim()
  if (type) params.object_type = type
  return params
}

/** 矩阵里有几行订阅（行 = 内容类型）。 */export function matrixTopics(matrix) {
  return (matrix && matrix.topics) || []
}

/** 某一行里**现在真的会收到**这类内容的渠道 id：订阅在启用中，且渠道自己也启用着。
 *
 * 读接口给的行是 `channels: [{id, enabled}]`，其中 `enabled` 是**渠道**的启停（不是这条
 * 订阅的）：停用的渠道仍留在这份名单里（订阅保留、投递跳过）。所以取 id 必须走
 * `channel.id` —— 直接 `Number(对象)` 得到的是 NaN，页面表现是多选列表全未勾、取消勾选
 * 发出 `target_channel_id: null` 的 422 请求（D1）。
 *
 * 「勾了」＝现在真的会发到那里，所以矩阵的勾选态（`topicSubscribed`）、多选列表的勾选态、
 * 「N 个渠道」徽章与零订阅判定**共用这一份判据** —— 徽章写「1 个渠道」时，两个形态的勾
 * 都该正好是 1 个。 */
export function topicActiveChannelIds(row) {
  return ((row && row.channels) || [])
    .filter((channel) => channel && channel.enabled)
    .map((channel) => Number(channel.id))
}

/** 某一行的零订阅判定：一个**可投递**的渠道都没有，就是这类内容当前一条都不发。
 *
 * 「订阅了但渠道停着」也算零订阅 —— 投递会跳过停用渠道（出站记 skipped），页面上不
 * 亮出来，店长会以为还在发（用户故事 9 要的正是这个可见性）。
 */
export function topicIsUnsubscribed(row) {
  return topicActiveChannelIds(row).length === 0
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

/**
 * 加载失败时给店长看的那一句话（UI 走查 U5）。
 *
 * 后端 5xx 的 `detail` 是 FastAPI 的英文原文（`Internal Server Error`），原来被整句透传到
 * 页顶：「企微推送数据加载失败：Internal Server Error」。这不是店长能读懂、也不是他能处理的
 * 东西 —— 5xx 一律翻成「服务暂时不可用」+ 重试按钮；4xx 的 `detail` 是后端写好的中文
 * （「页面已更新，请刷新后重试」这类），照用；网络层在 `api/client.js` 已经翻过中文了。
 *
 * 技术原文不丢：进 `loadErrorDetail()`，页面折叠在「详情」里。
 */
export function loadErrorMessage(error) {
  const status = Number((error && error.status) || 0)
  if (status >= 500) return '服务暂时不可用，请稍后重试'
  const message = String((error && error.message) || '').trim()
  return message || '加载失败，请重试'
}

/**
 * 加载失败的**技术原文**（页面「详情」里显示）。
 *
 * 与 `loadErrorMessage()` 同一个来源、两种用途：一个给人读，一个给排查读。
 */
export function loadErrorDetail(error) {
  if (!error) return ''
  const status = Number(error.status) || 0
  const raw = String(error.detail || error.message || '').trim()
  const parts = []
  if (status) parts.push(`HTTP ${status}`)
  if (raw) parts.push(raw)
  return parts.join(' · ')
}

/**
 * 「测试」按钮的确认文案（UI 走查 U6，旧清单 A29 遗留）。
 *
 * 这个按钮**一点就真的往门店群里发一条消息**（10-05 的走查真的发进过门店群），而它跟
 * 「删除」并排、同为 42×24 的小按钮，误点一下的代价是一条撤不回来的群消息。所以确认框
 * 必须点名目标群、并说清撤不回来 —— 这是点击与外发之间唯一的一道闸门。
 */
export function testChannelConfirmText(channel) {
  const name = String((channel && (channel.name || channel.id)) || '该渠道')
  return `确定向「${name}」发送一条测试消息吗？\n`
    + `测试消息会真的发到「${name}」这个群，任何人都无法撤回。`
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
  // 任务不再绑定渠道（票 08）：删渠道只会取消它的订阅，不会让任何任务失效。
  const topics = (channel.topics || []).length
  const groups = (channel.groups || []).length
  const extras = []
  if (topics) extras.push(`它的 ${topics} 条订阅会一并取消`)
  if (groups) extras.push(`它会从 ${groups} 个群组里移除`)
  const tail = extras.length ? `${extras.join('，')}。` : ''
  return `确定删除渠道「${name}」？${tail}删除后要重新配置才能恢复。`
}

/**
 * 推送任务的内容类型下拉：只列**支持定时触发**的（票 08）。
 *
 * 事件类内容（验收照片、采集告警……）没有定时侧，也就没有表单参数，不能建成任务。
 * 「哪些能定时」是后端注册表说的事（`triggers`），页面不维护第二份名单 —— 加一类
 * 内容类型、或给某类加上定时触发，前端都不用改。
 */
export function scheduledTopics(meta) {
  return ((meta && meta.topics) || []).filter(
    (topic) => (topic.triggers || []).includes('scheduled'),
  )
}

/**
 * 某类内容类型的参数默认值：直接取它的 JSON Schema 里每个字段的 `default`。
 *
 * 切换内容类型时用它把参数区铺满（下拉要有选中项），所以**字段名一个都不写死** ——
 * 参数形状是后端给的，页面只照着渲染。
 */
export function defaultJobParams(topic) {
  const properties = (topic && topic.params_schema && topic.params_schema.properties) || {}
  const params = {}
  for (const [field, schema] of Object.entries(properties)) {
    if (schema && Object.prototype.hasOwnProperty.call(schema, 'default')) {
      params[field] = schema.default
    }
  }
  return params
}

/**
 * 任务的请求体（票 08 的形状）：内容类型 + 参数 + 时间。
 *
 * 顶层 `schedule_time` 是**调度列**的值，取自参数区那个时间控件（页面上只有这一个
 * 时间入口）；参数里没有它时退回注册表声明的默认时间。收件人不在请求体里 —— 它由
 * 该内容类型的订阅决定，带上旧的 `webhook_id` 会被后端明确拒绝。
 */
export function jobPayload({ name = '', topicId = '', params = {}, notes = '', enabled = true } = {}, topics = []) {
  const topic = (topics || []).find((item) => item.id === topicId)
  const timeFromParams = String((params || {}).schedule_time || '')
  const scheduleTime = timeFromParams || String((topic && topic.default_schedule_time) || '')
  return {
    name: String(name || '').trim(),
    topic_id: topicId,
    params: { ...params },
    schedule_time: scheduleTime,
    enabled: !!enabled,
    notes: String(notes || '').trim(),
  }
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
  const typeLabel = job?.topic_name || '推送内容'
  const targets = Number(job?.target_count) || 0
  // 收件人来自订阅（票 08）：确认框要说清"发给几个群"。一个都没有时直接说清这次会被
  // 拒绝，而不是让人以为点下去就发出去了。
  const lines = targets > 0
    ? [`确定立即发送${typeLabel}吗？将发给 ${targets} 个群。`]
    : [`确定立即发送${typeLabel}吗？当前没有任何群订阅这类内容，发送会被拒绝，请先在「订阅」里勾选收件群。`]

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
  // 变更历史（票 11）：一页记录 + 对象类型筛选 + 总数，页面第五个 tab 读它。
  // `objectTypes` / `actions` 由读接口给出（筛选下拉的选项与中文名）。
  const audit = reactive({
    rows: [],
    total: 0,
    page: 1,
    pages: 0,
    filters: { objectType: '' },
    objectTypes: [],
    actions: [],
  })
  const selectedJobId = ref(null)
  const previewContent = ref('')
  const previewMeta = ref({ bytes: 0, chunkCount: 1 })
  const loading = ref(false)
  const error = ref('')
  // 加载失败的技术原文（英文 detail / HTTP 状态）：页面上折叠在「详情」里，不进主提示
  // —— 店长读的是 `error`，排查的人展开看它（UI 走查 U5）。
  const errorDetail = ref('')

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
    return { id: '', name: '', topic_id: '', params: {}, notes: '', enabled: true }
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

  /**
   * 变更历史：一页记录（后端算好总数），按时间倒序。
   *
   * 筛选只有对象类型一项（票面要求），与发送记录同样先取一页、不取「最近 N 条」：
   * 记录长期保留，半年后回看时"最近 N 条"根本没有用。
   */
  async function loadAuditLog({ page = null } = {}) {
    if (page !== null) audit.page = Math.max(1, Number(page) || 1)
    const data = await api.get('/api/wecom-push/audit-log', auditQuery({
      objectType: audit.filters.objectType,
      page: audit.page,
    }))
    audit.rows = data.rows || []
    audit.total = Number(data.total) || 0
    audit.pages = Number(data.pages) || 0
    audit.page = Number(data.page) || audit.page
    // 筛选项读接口给：加一种对象类型只改后端，页面不维护第二份名单。
    if (Array.isArray(data.object_types)) audit.objectTypes = data.object_types
    if (Array.isArray(data.actions)) audit.actions = data.actions
    return audit
  }

  /** 改对象类型筛选：回到第一页（与发送记录同一条规矩）。 */
  async function applyAuditFilters({ objectType = '' } = {}) {
    audit.filters.objectType = objectType
    audit.page = 1
    return loadAuditLog()
  }

  async function loadAll() {
    loading.value = true
    error.value = ''
    errorDetail.value = ''
    try {
      await loadMeta()
      await loadChannels()
      await loadJobs()
    } catch (e) {
      setLoadError(e)
    } finally {
      loading.value = false
    }
  }

  /** 记一次加载失败：人话进 `error`，原文进 `errorDetail`（两处都由上面两个函数定口径）。 */
  function setLoadError(e) {
    error.value = loadErrorMessage(e)
    errorDetail.value = loadErrorDetail(e)
  }

  /**
   * 页顶错误条上的「重试」：三份首屏数据与发送记录一起重拉。
   *
   * 发送记录是另一条请求（另一套分页），它失败时**不覆盖** `loadAll` 已经给出的那条 ——
   * 两条都挂的时候，先说出口的那条更贴首屏（也是店长正在看的那一块）。
   */
  async function reloadAll() {
    await loadAll()
    try {
      await loadDeliveries()
    } catch (e) {
      if (!error.value) setLoadError(e)
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

  /** 矩阵里某一行当前勾了哪些渠道（多选列表用它做勾选态与差集）。
   *
   * 判据与矩阵、徽章、零订阅判定同一份（`topicActiveChannelIds`）：**不能**在这里对
   * `{id, enabled}` 做 `Number(对象)` —— 那是 D1，勾选列表会全空、取消勾选还会发出
   * `target_channel_id: null` 的 422 请求。
   */
  function subscribedChannelIds(topicId) {
    return topicActiveChannelIds(matrixTopics(matrix.value).find((item) => item.id === topicId))
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
   *
   * 返回 `{changed, added, removed}`：**没有差异时 changed 是 0**，调用方据此说
   * 「没有改动」而不是假报「订阅已保存」（D1）—— 一次请求都没发却提示保存成功，
   * 店长会以为改好了。
   *
   * 中途失败时把服务端状态重拉一遍（页面上的「N 个渠道」徽章要是真的），但**不动**
   * `multiSelect.channelIds`：勾选态是店长刚点的意图，失败时悄悄弹回去是最坏的一种
   * 反馈。抛出的错误里带上已经落库的条数，重试时差集按新拉到的状态重算，已成功的那
   * 几条不会重复写坏（同一条订阅写两次 enabled 是幂等的）。
   */
  async function saveMultiSelectTopic() {
    const topicId = multiSelect.topicId
    if (!topicId) throw new Error('请先选择内容类型')
    const before = new Set(subscribedChannelIds(topicId))
    const after = new Set((multiSelect.channelIds || []).map(Number).filter(Number.isFinite))
    const added = [...after].filter((channelId) => !before.has(channelId))
    const removed = [...before].filter((channelId) => !after.has(channelId))
    if (!added.length && !removed.length) {
      return { changed: 0, added: 0, removed: 0 }
    }
    let changed = 0
    try {
      for (const channelId of added) {
        await api.post('/api/wecom-push/subscriptions',
          subscriptionPayload({ topicId, channelId, enabled: true }))
        changed += 1
      }
      for (const channelId of removed) {
        await api.post('/api/wecom-push/subscriptions',
          subscriptionPayload({ topicId, channelId, enabled: false }))
        changed += 1
      }
    } catch (e) {
      await loadSubscriptions().catch(() => {})
      const message = (e && e.message) || '保存失败'
      throw new Error(changed ? `已保存 ${changed} 条，之后：${message}` : message)
    }
    await loadSubscriptions()
    return { changed, added: added.length, removed: removed.length }
  }

  // ── 定时任务与发送记录（票 08 重做定时任务那一半）──────────────────────────
  function editJob(item) {
    selectedJobId.value = item.id
    Object.assign(jobForm, {
      id: item.id,
      name: item.name,
      topic_id: item.topic_id || '',
      // 参数按注册表的默认值铺底，再用任务自己的参数覆盖：这样即便某类内容新加了字段，
      // 老任务也能在表单里看到它（而不是缺一块控件）。
      params: {
        ...defaultJobParams((meta.value.topics || []).find((t) => t.id === item.topic_id)),
        ...(item.params || {}),
      },
      notes: item.notes || '',
      enabled: !!item.enabled,
    })
    if (!jobForm.params.schedule_time && item.schedule_time) {
      jobForm.params.schedule_time = item.schedule_time
    }
  }

  /** 选一类内容类型：参数区立刻按它的 schema 铺满默认值。 */
  function pickJobTopic(topicId) {
    const topic = (meta.value.topics || []).find((item) => item.id === topicId)
    jobForm.topic_id = topicId
    jobForm.params = defaultJobParams(topic)
  }

  /** 一键填入某类内容的默认参数（原来是写死的「模板」，现在模板就是注册表的默认值）。 */
  function applyJobPreset(topicId) {
    const topic = (meta.value.topics || []).find((item) => item.id === topicId)
    if (!topic) throw new Error('内容类型不可用')
    jobForm.id = ''
    if (!jobForm.name || jobForm.name === '') jobForm.name = topic.name
    pickJobTopic(topicId)
  }

  async function saveJob() {
    const id = jobForm.id
    if (!jobForm.topic_id) throw new Error('请选择推送内容类型')
    const payload = jobPayload({
      name: jobForm.name,
      topicId: jobForm.topic_id,
      params: jobForm.params,
      notes: jobForm.notes,
      enabled: jobForm.enabled,
    }, meta.value.topics)
    if (!payload.schedule_time) throw new Error('请填写推送时间')
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
    // 「立即发送」现在是**入队**（票 08）：真正发出去由统一出站做，所以这里把发送记录
    // 重新拉一遍 —— 本轮新入队的投递（待发）立刻可见。
    await loadDeliveries()
    return result
  }

  return {
    meta, channels, topics, groups, jobs, matrix, deliveries, audit,
    selectedJobId, previewContent, previewMeta, loading, error, errorDetail,
    activeTab, channelForm, channelGroupForm, multiSelect,
    contractWarning, zeroSubscriptionTip, viewMode, zeroTopicIds,
    resetChannelForm, resetChannelGroupForm, resetJobForm, jobForm,
    loadAll, reloadAll, loadMeta, loadChannels, loadSubscriptions, loadChannelGroups, loadJobs,
    loadDeliveries, applyDeliveryFilters,
    loadAuditLog, applyAuditFilters,
    editChannel, saveChannel, deleteChannel, toggleChannelEnabled, testChannel,
    editChannelGroup, saveChannelGroup, deleteChannelGroup,
    addGroupMember, removeGroupMember, syncChannelGroups,
    toggleSubscription, subscribedChannelIds, pickMultiSelectTopic, saveMultiSelectTopic,
    editJob, pickJobTopic, applyJobPreset, saveJob, deleteJob, previewSelectedJob, sendSelectedJob,
  }
}
