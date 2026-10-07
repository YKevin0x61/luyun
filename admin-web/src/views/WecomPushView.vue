<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  useWecomPush, WECOM_PUSH_API_VERSION, MATRIX_CHANNEL_LIMIT, scheduledTopics,
  channelDeleteConfirmText, channelGroupNames, channelTopicNames, formatSentAt,
  canSendNow, sendNowConfirmText, testChannelConfirmText, DELIVERY_STATUS_OPTIONS,
  deliveryStatusLabel, deliveryErrorSummary, formatDeliveryTime, auditActionLabel,
  auditObjectLabel, auditVisibleFields, auditChangeText,
} from '../composables/useWecomPush'
import SvgIcon from '../components/SvgIcon.vue'
import PushParamsForm from '../components/wecom/PushParamsForm.vue'
import LuyunCheckbox from '../components/ui/LuyunCheckbox.vue'

// 页内 tab：不改路由（页面清单契约保持绿）。「变更历史」是票 11 的第五个 tab。
const TABS = [
  { id: 'channels', name: '渠道' },
  { id: 'subscriptions', name: '订阅' },
  { id: 'jobs', name: '定时任务' },
  { id: 'logs', name: '发送记录' },
  { id: 'audit', name: '变更历史' },
]

const {
  meta, channels, topics, groups, jobs, matrix, deliveries, audit, selectedJobId,
  previewContent, previewMeta, loading, error, errorDetail, activeTab, channelForm,
  channelGroupForm, multiSelect, contractWarning, zeroSubscriptionTip, viewMode,
  zeroTopicIds,
  resetChannelForm, resetChannelGroupForm, resetJobForm, jobForm,
  // `loadAll` 不再直接调：首屏与「重试」都走 `reloadAll`（它还负责把发送记录一页带上）。
  reloadAll, loadSubscriptions, loadJobs, loadDeliveries,
  applyDeliveryFilters, loadChannels, loadAuditLog, applyAuditFilters,
  editChannel, saveChannel, deleteChannel, toggleChannelEnabled, testChannel,
  editChannelGroup, saveChannelGroup, deleteChannelGroup,
  addGroupMember, removeGroupMember,
  toggleSubscription, pickMultiSelectTopic, saveMultiSelectTopic, subscribedChannelIds,
  editJob, pickJobTopic, applyJobPreset, saveJob, deleteJob, previewSelectedJob,
  sendSelectedJob,
} = useWecomPush()

const toastMsg = ref('')
const toastType = ref('info')

function flash(msg, type = 'info') {
  toastMsg.value = msg
  toastType.value = type
  setTimeout(() => { if (toastMsg.value === msg) toastMsg.value = '' }, 3000)
}

/**
 * 渠道 / 群组两块数据的加载态（UI 走查 U4）。
 *
 * `loading` 一为真就说明首屏数据还没回来：这时**不能**渲染「0 个 / 暂无渠道」那种空态文案
 * —— 那是"确实没有"，与"还没拿到"是两件事，店长按前者就会去新建一个已经存在的渠道。
 * 判据带上 `!length`：重试时已经有旧数据在屏幕上，这时再闪骨架反而更乱。
 */
const channelsLoading = computed(() => loading.value && !channels.value.length)
const groupsLoading = computed(() => loading.value && !groups.value.length)

/** 页顶错误条上的「重试」：三份首屏数据 + 发送记录一起重拉（口径在 composable 里）。 */
async function handleRetryLoad() {
  await reloadAll()
}

// 发送记录（票 07）：筛选控件是**本地草稿**，点「筛选」才写回 composable 并回第一页。
// 每次选择都立刻请求的话，店长调三个下拉就要看三次中途结果。
const deliveryFilterForm = ref({
  topicId: deliveries.filters.topicId,
  channelId: deliveries.filters.channelId,
  status: deliveries.filters.status,
})
// 内容类型下拉来自 `/meta` 的注册表（ADR 0097）：加一类内容只改后端，页面不写死。
// 用注册表而不是「记录里出现过的类型」：某一类还没发过也要能选中它（那正是要查的场景）。
const deliveryTopicOptions = computed(() => meta.value.topics || [])
// 一次筛选都没设：空表时说「暂无发送记录」，否则说「没有符合条件的记录」。
const deliveryFiltered = computed(() => Boolean(
  deliveries.filters.topicId || deliveries.filters.channelId || deliveries.filters.status,
))
const deliveryStatusOptions = DELIVERY_STATUS_OPTIONS

// 变更历史（票 11）：筛选控件同样是**本地草稿**，点「筛选」才写回 composable 并回第一页。
const auditFilterForm = ref({ objectType: audit.filters.objectType })
// 一次筛选都没设：空表时说「暂无变更记录」，否则说「没有符合条件的记录」。
const auditFiltered = computed(() => Boolean(audit.filters.objectType))

async function handleApplyAuditFilters() {
  try {
    await applyAuditFilters(auditFilterForm.value)
  } catch (e) { flash(e.message, 'error') }
}
async function handleAuditPage(page) {
  try {
    await loadAuditLog({ page })
  } catch (e) { flash(e.message, 'error') }
}
function handleRefreshAudit() {
  loadAuditLog().catch((e) => flash(e.message, 'error'))
}

/**
 * 切页内 tab。
 *
 * 「变更历史」**按需加载**（与发送记录在挂载时另拉一页同一个道理，只是更懒）：它长期
 * 保留、条数只会涨，而大多数时候店长是来看渠道 / 订阅的 —— 每次打开这一页都顺手拉一页
 * 历史没有意义。切过去的那一刻拉一次，之后靠「刷新」与翻页。
 */
function handleSwitchTab(tabId) {
  activeTab.value = tabId
  if (tabId === 'audit') handleRefreshAudit()
}

/**
 * 一行记录里「变更内容」那几行的文本。
 *
 * 逐字段算差异，页面只负责渲染「改前 → 改后」（口径在 composable 的 `auditFieldChanges`
 * 里，与后端 `changed_fields` 同一份）：一次保存动了名字又动了启停时，两行都要看得见。
 * 行内 id 与已用名字表达过的外键由 `auditVisibleFields` 挡掉（记录里还在，只是不显示）。
 */
function auditChanges(row) {
  return auditVisibleFields(row).map(([field, change]) => auditChangeText(field, change))
}

async function handleApplyDeliveryFilters() {
  try {
    await applyDeliveryFilters(deliveryFilterForm.value)
  } catch (e) { flash(e.message, 'error') }
}
async function handleDeliveryPage(page) {
  try {
    await loadDeliveries({ page })
  } catch (e) { flash(e.message, 'error') }
}
function handleRefreshDeliveries() {
  loadDeliveries().catch((e) => flash(e.message, 'error'))
  // 渠道名是服务端配在记录里的：新建渠道后刷新记录顺带把渠道列表拉一遍，
  // 下拉里立刻能按新渠道筛（不然要整页刷新才看得到）。
  loadChannels().catch(() => {})
}

const isEditingChannel = computed(() => !!channelForm.id)
const isEditingGroup = computed(() => !!channelGroupForm.id)
const selectedJob = computed(() => jobs.value.find((j) => j.id === selectedJobId.value))
// 定时任务的内容类型下拉：只列**支持定时触发**的（来自 /meta 的注册表，票 08）。
// 参数区跟着选中的这一类走 —— 字段名、标签、下拉选项全部由它的 schema + uischema 给。
const jobTopics = computed(() => scheduledTopics(meta.value))
const selectedJobTopic = computed(
  () => jobTopics.value.find((topic) => topic.id === jobForm.topic_id) || null,
)
// 预览为空时「立即发送」不可点：这时页面没有任何可核对的内容，一点却会真的外发
// （后端 send-now 自己现算正文），是全页最容易误触的一条。
const sendReady = computed(() => canSendNow({
  job: selectedJob.value,
  content: previewContent.value,
}))
// 订阅视图：渠道超过阈值就切「先选内容、再勾群」的多选列表（窄屏可点）
const isMultiSelectView = computed(() => viewMode.value === 'multi-select')
const matrixRows = computed(() => matrix.value.topics || [])
const matrixColumns = computed(() => matrix.value.channels || [])
// 某个内容类型当前勾了哪些渠道：判据在 composable 里（`subscribedChannelIds` →
// `topicActiveChannelIds`），矩阵与多选列表共用同一份 —— 徽章「N 个渠道」数的就是它。
const subscriptionIds = computed(() => {
  const map = {}
  for (const row of matrixRows.value) {
    map[row.id] = subscribedChannelIds(row.id)
  }
  return map
})

function topicSubscribed(topicId, channelId) {
  return (subscriptionIds.value[topicId] || []).includes(Number(channelId))
}
function topicHasPhotos(row) {
  return !!row.contains_employee_photos
}

// 矩阵加载态（U4）：与渠道 / 群组同一套判据，理由见上面 channelsLoading 的注释。
const subscriptionsLoading = computed(() => loading.value && !matrixRows.value.length)

/**
 * 矩阵格子的可访问名（U3）。
 *
 * 每一格原来只有一个 16×16 的勾选框、`<td>` 自己不可点，读屏读到的也只是「复选框」——
 * 既不知道是哪一类内容、也不知道发给哪个群。名字就说这两件事，勾选态由 `aria-checked` 给。
 */
function matrixCellLabel(row, channel) {
  return `${row.name} 发给 ${channel.name}`
}
function matrixCellId(row, channel) {
  return `wp-matrix-${row.id}-${channel.id}`
}
/**
 * 点矩阵格子（U3）：整格都是热区。
 *
 * 勾选框自己那一路（指针点在框上、或键盘 Space/Enter 触发原生 click）会由 CheckboxRoot
 * 处理并抛出 `update:model-value`，事件再冒泡到这里 —— 不按 target 挡掉的话一次点击会切
 * 两下（勾上又取消），所以这里只接管"点在格子空白处"的那些点击。
 */
function handleMatrixCellClick(row, channel, event) {
  if (event.target && event.target.closest && event.target.closest('.luyun-checkbox')) return
  handleToggleSubscription(row, channel)
}

async function handleSaveChannel() {
  try {
    await saveChannel()
    flash('渠道已保存', 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleDeleteChannel(item) {
  if (!window.confirm(channelDeleteConfirmText(item))) return
  try {
    const data = await deleteChannel(item.id)
    flash(data.message || '渠道已删除', 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleToggleChannel(item) {
  try {
    await toggleChannelEnabled(item)
    flash(item.enabled ? `已停用「${item.name}」` : `已启用「${item.name}」`, 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleTestChannel(item) {
  // 一点就真外发（旧清单 A29 真发进过门店群）：确认框是唯一一道闸门，文案点名目标群。
  if (!window.confirm(testChannelConfirmText(item))) return
  try {
    const result = await testChannel(item.id)
    flash(result.success ? '测试消息已发送' : `测试失败：${result.error || result.response_text}`, result.success ? 'success' : 'error')
  } catch (e) { flash(e.message, 'error') }
}
async function handleSaveChannelGroup() {
  try {
    await saveChannelGroup()
    flash('群组已保存', 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleDeleteChannelGroup(item) {
  if (!window.confirm(`确定删除群组「${item.name}」？成员与指向它的订阅会一起取消。`)) return
  try {
    await deleteChannelGroup(item.id)
    flash('群组已删除', 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleToggleMember(group, channelId) {
  const isMember = (group.member_channel_ids || []).map(Number).includes(Number(channelId))
  try {
    if (isMember) await removeGroupMember({ groupId: group.id, channelId })
    else await addGroupMember({ groupId: group.id, channelId })
    await loadChannels()
  } catch (e) { flash(e.message, 'error') }
}
async function handleToggleSubscription(row, channel) {
  try {
    await toggleSubscription({
      topicId: row.id,
      channelId: channel.id,
      enabled: !topicSubscribed(row.id, channel.id),
    })
  } catch (e) { flash(e.message, 'error') }
}
function handlePickTopic(topicId) {
  pickMultiSelectTopic(topicId)
}
async function handleSaveMultiSelect() {
  try {
    const result = await saveMultiSelectTopic()
    // 没有差异时**不能**报「已保存」：一个请求都没发，那句提示是假的（D1）。
    if (!result || !result.changed) {
      flash('没有改动，无需保存 —— 勾选与已保存的订阅一致', 'info')
      return
    }
    const parts = []
    if (result.added) parts.push(`新增 ${result.added} 个渠道`)
    if (result.removed) parts.push(`停用 ${result.removed} 个渠道`)
    flash(`订阅已保存（${parts.join('、')}），下一次触发即按新订阅投递`, 'success')
  } catch (e) { flash(e.message, 'error') }
}
/**
 * 保存任务后把这张卡"点"一下（UI 走查 U1）。
 *
 * 列表按 id 稳定排序（后端 `wecom_jobs_all`），所以保存不会换位；但这条 tab 上还有别的
 * 卡片与滚动，店长刚点完「保存任务」得能一眼确认**哪张卡被改了**、改成了什么。短时高亮
 * 把它标出来，`scrollIntoView` 保证它在视口里（长列表里保存的那张可能正在屏幕外）。
 *
 * 高亮是"刚发生的事"，2.4 秒后自己退掉 —— 常驻的话就变成了另一种选中态，与卡片本来
 * 就有的选中边框分不开。
 */
const flashJobId = ref(null)
/**
 * 任务卡的 DOM 引用（按 id）。用普通 Map 而不是响应式对象：函数式 ref 每次重渲染都会
 * 被调一次，写进响应式状态会自己触发下一轮渲染。
 */
const jobCards = new Map()
function setJobCardRef(id) {
  return (el) => {
    if (el) jobCards.set(id, el)
    else jobCards.delete(id)
  }
}
let flashTimer = null
function flashSavedJob(id) {
  if (!id) return
  flashJobId.value = id
  clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { flashJobId.value = null }, 2400)
  nextTick(() => {
    const card = jobCards.get(id)
    // jsdom 没有 scrollIntoView；真机上滚动失败也不该影响保存这条路。
    if (card && typeof card.scrollIntoView === 'function') {
      card.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  })
}
onBeforeUnmount(() => {
  clearTimeout(flashTimer)
  jobCards.clear()
})

// 发送记录的「最后一次错误」：展开看完整原文（U16）。一次只展开一行 —— 一屏几十行里
// 同时摊开多条错误，反而看不出哪一行是哪一条。
const expandedDeliveryId = ref(null)
function toggleDeliveryDetail(id) {
  expandedDeliveryId.value = expandedDeliveryId.value === id ? null : id
}

async function handleSaveJob() {
  // 名称必填：点击保存这条路不经过原生表单校验（见按钮上的 `.prevent`），这里补上 ——
  // 否则能存出一条没有名字的任务。
  if (!String(jobForm.name || '').trim()) {
    flash('请填写任务名称', 'error')
    return
  }
  try {
    await saveJob()
    flashSavedJob(selectedJobId.value)
    flash('推送任务已保存', 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleDeleteJob(id) {
  if (!window.confirm('确定删除该任务？')) return
  try {
    await deleteJob(id)
    flash('推送任务已删除', 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handlePreview() {
  try {
    await previewSelectedJob()
  } catch (e) { flash(e.message, 'error') }
}
async function handleSend() {
  // 确认框的文案必须跟着页面状态走：预览空着的时候原来的文案照样说"当前预览对应的
  // 销售报表"，用户既不知道发给谁也不知道多少字节（见 sendNowConfirmText 的注释）。
  const text = sendNowConfirmText({
    job: selectedJob.value,
    bytes: previewMeta.value.bytes,
    content: previewContent.value,
  })
  if (!window.confirm(text)) return
  try {
    const result = await sendSelectedJob()
    flash(
      result.success
        ? `已加入发送队列：将发给 ${result.target_count} 个群，结果见「发送记录」`
        : `发送失败：${result.error || result.response_text}`,
      result.success ? 'success' : 'error',
    )
  } catch (e) { flash(e.message, 'error') }
}
async function copyPreview() {
  if (!previewContent.value) return
  await navigator.clipboard.writeText(previewContent.value)
  flash('预览内容已复制', 'success')
}
function handlePickJobTopic(topicId) {
  // 换内容类型就是换一整套参数：直接按新类型的 schema 默认值铺满参数区。
  pickJobTopic(topicId)
}
function handleApplyPreset(topicId) {
  try {
    applyJobPreset(topicId)
    flash('已新建一条草稿：这一类的默认参数已填好，改完名称与时间后保存', 'success')
  } catch (e) { flash(e.message, 'error') }
}

onMounted(async () => {
  // 首屏三份数据（meta / 渠道 / 任务）+ 发送记录一页一起拉；失败的落点与「重试」按钮
  // 走同一条路（composable 的 reloadAll），页面只管展示 `error` / `errorDetail`。
  await reloadAll()
})
</script>

<template>
  <div style="display:flex;flex-direction:column;gap:12px">
    <!-- 加载失败：只给人话 + 重试，技术原文折叠进「详情」（U5）。后端 5xx 的 detail 是
         英文的 `Internal Server Error`，直接透传给店长等于没说。 -->
    <div v-if="error" class="dash-error-banner wp-load-error">
      <SvgIcon name="alert-triangle" :size="14" />
      <span class="wp-load-error__text">企微推送数据加载失败：{{ error }}</span>
      <button class="btn btn-sm" type="button" @click="handleRetryLoad">重试</button>
      <details v-if="errorDetail && errorDetail !== error" class="wp-load-error__detail">
        <summary>详情</summary>
        <pre>{{ errorDetail }}</pre>
      </details>
    </div>
    <!-- 接口版本不匹配：只提示，不阻断操作（旧页面照样能读，写接口由后端把关） -->
    <div v-if="contractWarning" class="dash-error-banner"><SvgIcon name="alert-triangle" :size="14" /> {{ contractWarning }}</div>
    <!-- 零订阅：这类内容当前一条都不发，页面上不写出来店长只会以为"今天没有" -->
    <div v-if="zeroSubscriptionTip" class="dash-error-banner"><SvgIcon name="alert-triangle" :size="14" /> {{ zeroSubscriptionTip }}</div>
    <div v-if="toastMsg" class="badge" :style="toastType === 'error' ? 'color:var(--red);border-color:var(--red)' : 'color:var(--green);border-color:var(--green)'">
      {{ toastMsg }}
    </div>

    <!-- 页内 tab 条：390 下 5 个标签 + 版本徽章抢同一行，标签会被压成「渠 道」这种
         竖排（D4）。所以标签进一个可横向滚动的行、版本徽章窄屏让位（不匹配时页顶
         有专门的提示条），见 <style scoped> 里的 .wp-tabbar / .wp-api-version。 -->
    <div class="view-tabs">
      <div class="wp-tabbar luyun-scrollbar">
        <button
          v-for="tab in TABS"
          :key="tab.id"
          class="view-tab"
          :class="{ active: activeTab === tab.id }"
          type="button"
          @click="handleSwitchTab(tab.id)"
        >{{ tab.name }}</button>
      </div>
      <span class="wp-api-version">接口版本 {{ WECOM_PUSH_API_VERSION }}</span>
    </div>

    <!-- ═══ 渠道 ══════════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'channels'" class="grid" style="grid-template-columns: minmax(280px, 420px) minmax(0, 1fr)">
      <div style="display:flex;flex-direction:column;gap:12px">
        <!-- 渠道地址 -->
        <div class="card">
          <div class="panel-title" style="display:flex;justify-content:space-between">
            <span>渠道</span>
            <!-- 数据没回来时说「加载中…」，不说「0 个」——后者是"确实一个都没有"（U4）。 -->
            <span style="color:var(--text-dim);font-size:12px">
              {{ channelsLoading ? '加载中…' : `${channels.length} 个` }}
            </span>
          </div>
          <form @submit.prevent="handleSaveChannel" style="display:flex;flex-direction:column;gap:10px">
            <div class="badge" :style="isEditingChannel ? 'color:var(--yellow);border-color:var(--yellow)' : ''">
              {{ isEditingChannel ? `正在编辑：${channelForm.name}` : '新增渠道' }}
              <button v-if="isEditingChannel" type="button" class="btn btn-sm" style="margin-left:8px" @click="resetChannelForm">取消编辑</button>
            </div>
            <div class="form-row">
              <label for="wp-channel-name">名称</label>
              <input id="wp-channel-name" class="input" v-model="channelForm.name" placeholder="例如：管理群日报" maxlength="60" required />
            </div>
            <div class="form-row">
              <label for="wp-channel-url">Webhook 地址</label>
              <input id="wp-channel-url" class="input" v-model="channelForm.webhook_url" :placeholder="isEditingChannel ? '留空表示不更换地址' : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...'" maxlength="500" />
            </div>
            <div class="form-row">
              <label for="wp-channel-notes">备注</label>
              <textarea id="wp-channel-notes" class="input" v-model="channelForm.notes" maxlength="200" placeholder="可选" style="min-height:74px;resize:vertical"></textarea>
            </div>
            <div class="form-row">
              <label>所属群组（可多选）</label>
              <div v-if="!groups.length" style="color:var(--text-dim);font-size:12px">还没有群组，可在「渠道群组」卡片里新建</div>
              <label v-for="g in groups" :key="g.id" class="luyun-check-row">
                <LuyunCheckbox
                  :model-value="channelForm.group_ids.includes(g.id)"
                  @update:model-value="(checked) => {
                    channelForm.group_ids = checked
                      ? [...channelForm.group_ids, g.id]
                      : channelForm.group_ids.filter((id) => id !== g.id)
                  }"
                />
                {{ g.name }}<span v-if="!g.enabled" style="color:var(--text-dim)">（停用）</span>
              </label>
            </div>
            <label class="luyun-check-row">
              <LuyunCheckbox v-model="channelForm.enabled" /> 启用
            </label>
            <div style="display:flex;gap:8px">
              <button class="btn btn-primary" type="submit">{{ isEditingChannel ? '保存修改' : '新增渠道' }}</button>
              <button class="btn" type="button" @click="resetChannelForm">清空 / 新增</button>
            </div>
          </form>

          <div style="display:flex;flex-direction:column;gap:8px;margin-top:12px">
            <div v-if="channelsLoading" class="wp-skeleton" role="status" aria-live="polite" aria-label="正在加载渠道">
              <span class="wp-skeleton__line"></span>
              <span class="wp-skeleton__line"></span>
              <span class="wp-skeleton__line wp-skeleton__line--short"></span>
            </div>
            <div v-else-if="!channels.length" class="empty-state">暂无渠道</div>
            <div v-for="item in channels" :key="item.id" class="card wp-hook-card" style="padding:10px">
              <div class="wp-hook-head">
                <strong class="wp-hook-name">{{ item.name }}</strong>
                <span class="wp-hook-badges">
                  <span class="badge" :style="item.enabled ? 'color:var(--green);border-color:var(--green)' : ''">{{ item.enabled ? '启用' : '停用' }}</span>
                  <span v-if="(item.topics || []).length === 0" class="badge" style="color:var(--yellow);border-color:var(--yellow)">未订阅</span>
                </span>
              </div>
              <div style="color:var(--text-dim);font-size:12px;word-break:break-all">{{ item.webhook_url_masked }}</div>
              <div v-if="item.notes" style="color:var(--text-dim);font-size:12px">{{ item.notes }}</div>
              <div style="color:var(--text-dim);font-size:12px">
                群组：{{ channelGroupNames(item) || '未分组' }}
              </div>
              <div style="color:var(--text-dim);font-size:12px">
                订阅内容：{{ channelTopicNames(item) || '未订阅任何内容' }}
                <span v-if="(item.topics || []).some((t) => t.via_group)" class="badge" style="margin-left:4px">含群组订阅</span>
              </div>
              <div style="color:var(--text-dim);font-size:12px">最近一次发送成功：{{ formatSentAt(item) }}</div>
              <div v-if="item.job_count" style="color:var(--text-dim);font-size:12px">被 {{ item.job_count }} 条推送任务引用</div>
              <div style="display:flex;gap:6px;margin-top:8px">
                <button class="btn btn-sm" @click="editChannel(item)">编辑</button>
                <button class="btn btn-sm" @click="handleToggleChannel(item)">{{ item.enabled ? '停用' : '启用' }}</button>
                <button class="btn btn-sm" @click="handleTestChannel(item)">测试</button>
                <button class="btn btn-sm btn-danger" @click="handleDeleteChannel(item)">删除</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:12px">
        <!-- 群组与成员 -->
        <div class="card">
          <div class="panel-title" style="display:flex;justify-content:space-between">
            <span>渠道群组</span>
            <span style="color:var(--text-dim);font-size:12px">
              {{ groupsLoading ? '加载中…' : `${groups.length} 个` }}
            </span>
          </div>
          <p style="color:var(--text-dim);font-size:12px;margin:0 0 8px">
            群组是可以整体订阅的一组渠道；一个渠道可以同时属于多个群组。停用群组只暂停这一组的订阅，
            不影响成员自己的订阅。
          </p>
          <form @submit.prevent="handleSaveChannelGroup" style="display:flex;flex-direction:column;gap:10px">
            <div class="badge" :style="isEditingGroup ? 'color:var(--yellow);border-color:var(--yellow)' : ''">
              {{ isEditingGroup ? `正在编辑：${channelGroupForm.name}` : '新增群组' }}
              <button v-if="isEditingGroup" type="button" class="btn btn-sm" style="margin-left:8px" @click="resetChannelGroupForm">取消编辑</button>
            </div>
            <div class="form-row">
              <label for="wp-group-name">群组名称</label>
              <input id="wp-group-name" class="input" v-model="channelGroupForm.name" maxlength="60" placeholder="例如：日报群组" required />
            </div>
            <div class="form-row">
              <label for="wp-group-notes">备注</label>
              <input id="wp-group-notes" class="input" v-model="channelGroupForm.notes" maxlength="200" placeholder="可选" />
            </div>
            <label class="luyun-check-row">
              <LuyunCheckbox v-model="channelGroupForm.enabled" /> 启用
            </label>
            <div style="display:flex;gap:8px">
              <button class="btn btn-primary" type="submit">{{ isEditingGroup ? '保存修改' : '新增群组' }}</button>
              <button class="btn" type="button" @click="resetChannelGroupForm">清空</button>
            </div>
          </form>

          <div style="display:flex;flex-direction:column;gap:8px;margin-top:12px">
            <div v-if="groupsLoading" class="wp-skeleton" role="status" aria-live="polite" aria-label="正在加载群组">
              <span class="wp-skeleton__line"></span>
              <span class="wp-skeleton__line wp-skeleton__line--short"></span>
            </div>
            <div v-else-if="!groups.length" class="empty-state">暂无群组</div>
            <div v-for="item in groups" :key="item.id" class="card" style="padding:10px">
              <div class="wp-hook-head">
                <strong class="wp-hook-name">{{ item.name }}</strong>
                <span class="wp-hook-badges">
                  <span class="badge" :style="item.enabled ? 'color:var(--green);border-color:var(--green)' : ''">{{ item.enabled ? '启用' : '停用' }}</span>
                  <span class="badge">{{ (item.member_channel_ids || []).length }} 个成员</span>
                </span>
              </div>
              <div v-if="item.notes" style="color:var(--text-dim);font-size:12px">{{ item.notes }}</div>
              <div style="display:flex;flex-direction:column;gap:4px;margin-top:6px">
                <label v-for="channel in channels" :key="channel.id" class="luyun-check-row">
                  <LuyunCheckbox
                    :model-value="(item.member_channel_ids || []).includes(channel.id)"
                    @update:model-value="() => handleToggleMember(item, channel.id)"
                  />
                  {{ channel.name }}<span v-if="!channel.enabled" style="color:var(--text-dim)">（停用）</span>
                </label>
              </div>
              <div v-if="!channels.length" style="color:var(--text-dim);font-size:12px;margin-top:6px">还没有渠道可加入</div>
              <div style="display:flex;gap:6px;margin-top:8px">
                <button class="btn btn-sm" @click="editChannelGroup(item)">编辑</button>
                <button class="btn btn-sm btn-danger" @click="handleDeleteChannelGroup(item)">删除</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ 订阅 ══════════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'subscriptions'" style="display:flex;flex-direction:column;gap:12px">
      <div class="card">
        <div class="panel-title" style="display:flex;justify-content:space-between;align-items:center">
          <span>订阅：哪类内容发给哪些渠道</span>
          <span style="color:var(--text-dim);font-size:12px">
            {{ subscriptionsLoading ? '加载中…' : `${matrixColumns.length} 个渠道 · ${matrixRows.length} 类内容` }}
          </span>
        </div>

        <!-- 渠道不超过阈值：内容 × 渠道的勾选矩阵 -->
        <template v-if="!isMultiSelectView">
          <div v-if="subscriptionsLoading" class="wp-skeleton" role="status" aria-live="polite" aria-label="正在加载订阅">
            <span class="wp-skeleton__line"></span>
            <span class="wp-skeleton__line"></span>
            <span class="wp-skeleton__line wp-skeleton__line--short"></span>
          </div>
          <p v-else-if="!matrixColumns.length" class="empty-state">还没有渠道。先到「渠道」tab 新增一个。</p>
          <template v-else>
            <!-- 窄屏只有这条横向滚动的表格（发送记录 / 变更历史都改成了卡片）：没有提示
                 的话「右边还有渠道」是看不出来的（U2）。桌面档整张表摆得下，不显示。 -->
            <p class="wp-scroll-hint">表格可左右滑动，查看其余渠道 →</p>
            <div class="data-table-wrap wp-matrix-wrap luyun-scrollbar" style="max-height:520px">
              <table class="data-table wp-matrix">
                <thead>
                  <tr>
                    <th style="cursor:default">内容类型</th>
                    <th v-for="channel in matrixColumns" :key="channel.id" style="cursor:default">
                      {{ channel.name }}<span v-if="!channel.enabled" style="color:var(--text-dim)">（停用）</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in matrixRows" :key="row.id" :class="{ 'wp-matrix-zero': zeroTopicIds.includes(row.id) }">
                    <td>
                      <strong>{{ row.name }}</strong>
                      <span v-if="topicHasPhotos(row)" class="badge" style="margin-left:6px;color:var(--cyan);border-color:var(--cyan)">含员工实拍照片</span>
                      <div v-if="zeroTopicIds.includes(row.id)" style="color:var(--yellow);font-size:11px">零订阅：当前不会发出</div>
                    </td>
                    <!-- 整格都是热区（U3）：原来只有 16×16 的勾选框可点，矩阵 64 格实测 192 次
                         点空。格子里留一个 ≥44×44 的命中区，点击由整格接管；勾选框自己是
                         可聚焦的按钮，键盘 Space/Enter 照旧可切（见 handleMatrixCellClick）。 -->
                    <td
                      v-for="channel in matrixColumns"
                      :key="channel.id"
                      class="wp-matrix-cell"
                      @click="handleMatrixCellClick(row, channel, $event)"
                    >
                      <span class="wp-matrix-hit">
                        <LuyunCheckbox
                          :id="matrixCellId(row, channel)"
                          :model-value="topicSubscribed(row.id, channel.id)"
                          :aria-label="matrixCellLabel(row, channel)"
                          @update:model-value="() => handleToggleSubscription(row, channel)"
                        />
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </template>

        <!-- 渠道超过阈值：先选内容、再勾群（手机上可点） -->
        <template v-else>
          <p style="color:var(--text-dim);font-size:12px;margin:0 0 8px">
            渠道已有 {{ matrixColumns.length }} 个（超过 {{ MATRIX_CHANNEL_LIMIT }} 个），
            改为「先选内容类型、再勾渠道」—— 手机上更好点。
          </p>
          <div style="display:flex;flex-direction:column;gap:8px">
            <button
              v-for="row in matrixRows"
              :key="row.id"
              class="btn wp-pick-row"
              :class="{ active: multiSelect.topicId === row.id, 'wp-matrix-zero': zeroTopicIds.includes(row.id) }"
              type="button"
              @click="handlePickTopic(row.id)"
            >
              <span>{{ row.name }}</span>
              <span v-if="topicHasPhotos(row)" class="badge" style="color:var(--cyan);border-color:var(--cyan)">含员工实拍照片</span>
              <span class="badge">{{ (subscriptionIds[row.id] || []).length }} 个渠道</span>
              <span v-if="zeroTopicIds.includes(row.id)" class="badge" style="color:var(--yellow);border-color:var(--yellow)">零订阅</span>
            </button>
          </div>
          <div v-if="multiSelect.topicId" style="margin-top:12px;display:flex;flex-direction:column;gap:6px">
            <div style="font-size:13px">
              勾选要接收「{{ (matrixRows.find((r) => r.id === multiSelect.topicId) || {}).name }}」的渠道：
            </div>
            <label v-for="channel in matrixColumns" :key="channel.id" class="luyun-check-row">
              <LuyunCheckbox
                :model-value="(multiSelect.channelIds || []).includes(channel.id)"
                @update:model-value="(checked) => {
                  multiSelect.channelIds = checked
                    ? [...multiSelect.channelIds, channel.id]
                    : multiSelect.channelIds.filter((id) => id !== channel.id)
                }"
              />
              {{ channel.name }}<span v-if="!channel.enabled" style="color:var(--text-dim)">（停用，投递会跳过）</span>
            </label>
            <div style="display:flex;gap:8px;margin-top:6px">
              <button class="btn btn-primary" type="button" @click="handleSaveMultiSelect">保存订阅</button>
              <button class="btn" type="button" @click="multiSelect.channelIds = []">全不选</button>
            </div>
          </div>
          <p v-else style="color:var(--text-dim);font-size:12px;margin-top:12px">先选一类内容，再勾渠道。</p>
        </template>

        <p style="color:var(--text-dim);font-size:12px;margin:10px 0 0">
          取消勾选是停用那条订阅（保留配置，重新勾上不用重配）；保存后下一次触发即按新订阅投递。
          渠道停用时订阅保留、投递跳过。
        </p>
      </div>
    </div>

    <!-- ═══ 定时任务（左：任务表单 + 任务卡；右：消息预览）══════════════════
         两列 grid 只属于「定时任务」这一个 tab：它左列窄、右列是预览，摆得下。
         「发送记录」以前被并进这个 `v-if`，而左列自己带 `v-if="activeTab === 'jobs'"` ——
         切过去时左列不渲染，剩下的卡片被 grid 自动放进第一列 `minmax(280px,420px)`，
         1440 下 7 列表格只有 388px 可视宽（要横向滚动），右边 1000px 空白（D2）。 -->
    <div v-if="activeTab === 'jobs'" class="grid" style="grid-template-columns: minmax(280px, 420px) minmax(0, 1fr)">
      <div style="display:flex;flex-direction:column;gap:12px">
        <!-- 推送任务 -->
        <div class="card">
          <div class="panel-title" style="display:flex;justify-content:space-between">
            <span>推送任务</span>
            <span style="color:var(--text-dim);font-size:12px">{{ jobs.length }} 个</span>
          </div>
          <form @submit.prevent="handleSaveJob" style="display:flex;flex-direction:column;gap:10px">
            <div class="form-row">
              <label for="wp-job-name">任务名称</label>
              <input id="wp-job-name" class="input" v-model="jobForm.name" maxlength="60" required />
            </div>
            <!-- 内容类型下拉来自 `/meta` 的注册表，只列**支持定时触发**的（票 08）：事件类
                 内容没有定时侧、也就没有表单参数，不能建成任务。收件人不再由任务指定 ——
                 它由该内容类型的订阅决定。 -->
            <div class="form-row">
              <label for="wp-job-topic">内容类型</label>
              <select
                id="wp-job-topic"
                class="select"
                :value="jobForm.topic_id"
                @change="handlePickJobTopic($event.target.value)"
              >
                <option value="">请选择内容类型</option>
                <option v-for="topic in jobTopics" :key="topic.id" :value="topic.id">
                  {{ topic.name }}
                </option>
              </select>
            </div>
            <!-- 参数区：字段名、标签、下拉选项、默认值全部来自这一类内容的 `params_schema` +
                 `uischema`（ADR 0097）。页面不认识任何一类内容 —— 注册表加一类就是加一个
                 下拉项和一套它的字段，前端零改动。 -->
            <PushParamsForm
              v-if="selectedJobTopic"
              :schema="selectedJobTopic.params_schema"
              :uischema="selectedJobTopic.uischema"
              :data="jobForm.params"
              @update:data="jobForm.params = $event"
            />
            <div v-else class="wp-job-hint">
              {{ jobTopics.length ? '选择内容类型后，这里出现它的参数。' : '注册表里还没有支持定时触发的内容类型。' }}
            </div>
            <div class="form-row">
              <label for="wp-job-notes">备注</label>
              <textarea id="wp-job-notes" class="input" v-model="jobForm.notes" maxlength="200" placeholder="可选" style="min-height:74px;resize:vertical"></textarea>
            </div>
            <label class="luyun-check-row">
              <LuyunCheckbox v-model="jobForm.enabled" /> 启用定时推送
            </label>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <!-- 点击自己走保存（`.prevent` 挡掉随之而来的表单提交，否则一次点击会存两遍）；
                   在输入框里回车仍走 `<form>` 的 submit。两条路径都指向 handleSaveJob。 -->
              <button class="btn btn-primary" type="submit" @click.prevent="handleSaveJob">保存任务</button>
              <button class="btn" type="button" @click="resetJobForm">清空</button>
              <!-- 同一内容类型可以有多条任务（不同时间 / 参数），这是新建第二条的入口：
                   按这一类内容的默认值铺一份草稿，改完名称与时间再保存。 -->
              <button
                v-if="selectedJobTopic"
                class="btn"
                type="button"
                :title="`按「${selectedJobTopic.name}」的默认参数新建一条任务`"
                @click="handleApplyPreset(selectedJobTopic.id)"
              >+ 新建此类任务</button>
            </div>
          </form>

          <div style="display:flex;flex-direction:column;gap:8px;margin-top:12px">
            <div v-if="!jobs.length" class="empty-state">暂无推送任务</div>
            <!-- 卡片顺序 = 后端给的顺序，**按 id 稳定排**（`wecom_jobs_all` 的 ORDER BY id）：
                 推送时间只显示在卡上，不参与排序 —— 改完时间保存后这张卡不会跳到别处（U1），
                 按位置连点「编辑」也就不会改到另一条任务上。 -->
            <div
              v-for="item in jobs"
              :key="item.id"
              :ref="setJobCardRef(item.id)"
              class="card wp-job-card"
              :class="{ 'is-saved': flashJobId === item.id }"
              :data-job-id="item.id"
              style="padding:10px;cursor:pointer"
              :style="selectedJobId === item.id ? 'border-color:var(--accent)' : ''"
              @click="selectedJobId = item.id"
            >
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                <strong style="font-size:13px">{{ item.name }}</strong>
                <span class="badge" :style="item.enabled ? 'color:var(--green);border-color:var(--green)' : ''">{{ item.enabled ? item.schedule_time : '停用' }}</span>
              </div>
              <div style="color:var(--text-dim);font-size:12px">内容类型：{{ item.topic_name || item.topic_id }}</div>
              <!-- 收件人来自订阅（票 08）：卡片说清这类内容当前有几个群会收到，而不是某个群名。
                   一个都没有时直接写出来 —— 否则店长会以为任务照发。 -->
              <div style="color:var(--text-dim);font-size:12px">
                订阅目标：{{ item.target_count ? `${item.target_count} 个群` : '暂无群订阅，不会投递' }}
              </div>
              <div style="color:var(--text-dim);font-size:12px">上次定时发送日期：{{ item.last_sent_date || '未发送' }}</div>
              <div style="display:flex;gap:6px;margin-top:8px" @click.stop>
                <button class="btn btn-sm" @click="editJob(item)">编辑</button>
                <button class="btn btn-sm" @click="selectedJobId = item.id; handlePreview()">预览</button>
                <button class="btn btn-sm btn-danger" @click="handleDeleteJob(item.id)">删除</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:12px">
        <div v-if="activeTab === 'jobs'" class="card">
          <div class="panel-title" style="display:flex;justify-content:space-between;align-items:center">
            <span>消息预览</span>
            <div style="display:flex;gap:6px">
              <button class="btn btn-sm" @click="handlePreview">刷新预览</button>
              <!-- 禁用态（U8）：原来是绿底 + `opacity:.5`，看着仍是最显眼的主按钮，而预览
                   空着时点它没有任何可核对的内容。现在禁用就是灰底（.wp-send-now:disabled），
                   `title` 也说清"差哪一步"—— 进 tab 时任务已经默认选中，再写"先选择任务"
                   与卡片右上角的「当前任务：…」自相矛盾。 -->
              <button
                class="btn btn-sm wp-send-now"
                :disabled="!sendReady"
                :title="sendReady ? '发送前会再确认一次' : '点「刷新预览」后可发送'"
                @click="handleSend"
              >立即发送</button>
              <button class="btn btn-sm" @click="copyPreview">复制</button>
            </div>
          </div>
          <div style="display:flex;justify-content:space-between;color:var(--text-dim);font-size:12px;margin-bottom:8px">
            <span>{{ selectedJob ? `当前任务：${selectedJob.name}` : '请选择任务' }}</span>
            <span :style="previewMeta.chunkCount > 1 ? 'color:var(--yellow)' : ''">
              {{ previewMeta.bytes }} / 2048 字节{{ previewMeta.chunkCount > 1 ? ` · 发送时拆为 ${previewMeta.chunkCount} 条` : '' }}
            </span>
          </div>
          <textarea
            id="wp-job-preview"
            class="input"
            readonly
            aria-label="消息预览内容"
            :value="previewContent"
            placeholder="选择任务后点击刷新预览"
            style="min-height:280px;width:100%;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap"
          ></textarea>
        </div>
      </div>
    </div>

    <!-- ═══ 发送记录（票 07）══════════════════════════════════════════════
         整行铺开：7 列表格塞进两列 grid 的左列（minmax(280px, 420px)）时 1440 下只有
         388px 可视宽、右边 1000px 空白，字节数 / 尝试次数 / 最后一次错误全要横向滚动
         （D2）。它不跟「定时任务」共用那个 grid，直接占满整行。 -->
    <div v-if="activeTab === 'logs'" class="card">
      <div class="panel-title" style="display:flex;justify-content:space-between">
        <span>发送记录</span>
        <span style="display:flex;gap:8px;align-items:center">
          <span style="color:var(--text-dim);font-size:12px">共 {{ deliveries.total }} 条</span>
          <button class="btn btn-sm" @click="handleRefreshDeliveries">刷新</button>
        </span>
      </div>

      <!-- 筛选：内容类型（来自 /meta 注册表）/ 渠道 / 状态 + 分页。三个下拉是同一排的
           筛选控件、没有各自的可见标签，用 aria-label 给程序化名称（U10）。 -->
      <div class="wp-delivery-filters">
        <div class="form-row">
          <label>内容类型</label>
          <select class="select" aria-label="按内容类型筛选" v-model="deliveryFilterForm.topicId">
            <option value="">全部内容类型</option>
            <option v-for="item in deliveryTopicOptions" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
        </div>
        <div class="form-row">
          <label>目标渠道</label>
          <select class="select" aria-label="按目标渠道筛选" v-model="deliveryFilterForm.channelId">
            <option value="">全部渠道</option>
            <option v-for="item in channels" :key="item.id" :value="item.id">
              {{ item.name }}{{ item.enabled ? '' : '（停用）' }}
            </option>
          </select>
        </div>
        <div class="form-row">
          <label>状态</label>
          <select class="select" aria-label="按状态筛选" v-model="deliveryFilterForm.status">
            <option value="">全部状态</option>
            <option v-for="item in deliveryStatusOptions" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
        </div>
        <div style="display:flex;gap:8px;align-items:flex-end">
          <button class="btn btn-primary btn-sm" type="button" @click="handleApplyDeliveryFilters">筛选</button>
          <button
            class="btn btn-sm"
            type="button"
            :disabled="!deliveryFiltered"
            @click="deliveryFilterForm = { topicId: '', channelId: '', status: '' }; handleApplyDeliveryFilters()"
          >清空筛选</button>
        </div>
      </div>

      <!-- ≤700px 这张七列表格会切成一行一卡（U2）：`data-label` 就是卡片里的字段名。 -->
      <div class="data-table-wrap wp-card-table-wrap luyun-scrollbar" style="max-height:420px">
        <table class="data-table wp-card-table wp-delivery-table">
          <thead>
            <tr>
              <th style="cursor:default">时间</th>
              <th style="cursor:default">目标渠道</th>
              <th style="cursor:default">内容类型</th>
              <th style="cursor:default">状态</th>
              <th style="cursor:default">字节数</th>
              <th style="cursor:default">尝试次数</th>
              <th style="cursor:default">最后一次错误</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!deliveries.rows.length">
              <td colspan="7" class="empty-state">
                {{ deliveryFiltered ? '没有符合条件的记录，可换个筛选条件或清空筛选' : '暂无发送记录' }}
              </td>
            </tr>
            <template v-for="item in deliveries.rows" :key="item.id">
              <tr :class="{ 'wp-delivery-failed': item.status === 'failed' }">
                <!-- 时间不换行是全局 `table.data-table td` 就有的（桌面档），这里不再写内联：
                     内联的 nowrap 在 ≤700px 的卡片式里会把这一格顶出横向滚动。 -->
                <td data-label="时间">
                  {{ formatDeliveryTime(item.finished_at || item.created_at) }}
                  <span v-if="!item.finished_at" style="color:var(--text-dim);font-size:11px">（未完成，这是入队时间）</span>
                </td>
                <td data-label="目标渠道">{{ item.channel_name || (item.channel_id ? `#${item.channel_id}` : '渠道已删除') }}</td>
                <td data-label="内容类型">{{ item.topic_name }}</td>
                <td data-label="状态">
                  <span
                    class="badge"
                    :style="item.status === 'sent'
                      ? 'color:var(--green);border-color:var(--green)'
                      : (item.status === 'failed' ? 'color:var(--red);border-color:var(--red)' : '')"
                  >{{ deliveryStatusLabel(item.status) }}</span>
                </td>
                <td data-label="字节数">{{ item.message_bytes }}</td>
                <td data-label="尝试次数">{{ item.attempts }}</td>
                <!-- 第 7 列（U16）：这里原来 `overflow:hidden` 硬裁、没有展开入口，而失败原因
                     正是这一页最需要读的东西。现在显示人话版摘要，「详情」展开完整原文，
                     触屏（没有 hover）也读得到。 -->
                <td data-label="最后一次错误" class="wp-delivery-error">
                  <template v-if="item.last_error">
                    <span class="wp-delivery-error__text">{{ deliveryErrorSummary(item.last_error) }}</span>
                    <button
                      class="btn btn-sm wp-delivery-error__toggle"
                      type="button"
                      :aria-expanded="expandedDeliveryId === item.id"
                      @click.stop="toggleDeliveryDetail(item.id)"
                    >{{ expandedDeliveryId === item.id ? '收起' : '详情' }}</button>
                  </template>
                  <span v-else style="color:var(--text-dim)">—</span>
                </td>
              </tr>
              <tr v-if="expandedDeliveryId === item.id" class="wp-delivery-detail">
                <td colspan="7">
                  <div class="wp-error-full">
                    <div class="wp-error-full__title">最后一次错误（原文）</div>
                    <pre class="wp-error-full__raw">{{ item.last_error }}</pre>
                    <div v-if="item.content_summary" class="wp-error-full__meta">
                      内容摘要：{{ item.content_summary }}
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <div class="wp-delivery-pager">
        <button
          class="btn btn-sm"
          type="button"
          :disabled="deliveries.page <= 1"
          @click="handleDeliveryPage(deliveries.page - 1)"
        >上一页</button>
        <span style="color:var(--text-dim);font-size:12px">
          第 {{ deliveries.page }} / {{ Math.max(deliveries.pages, 1) }} 页
        </span>
        <button
          class="btn btn-sm"
          type="button"
          :disabled="deliveries.page >= deliveries.pages"
          @click="handleDeliveryPage(deliveries.page + 1)"
        >下一页</button>
        <span style="color:var(--text-dim);font-size:11px">
          超过保留天数的记录会被自动清理；待发与发送中的行不会被清理。
        </span>
      </div>
    </div>
    <!-- ═══ 变更历史（票 11）══════════════════════════════════════════════ -->
    <div v-if="activeTab === 'audit'" class="card">
      <div class="panel-title" style="display:flex;justify-content:space-between">
        <span>变更历史</span>
        <span style="display:flex;gap:8px;align-items:center">
          <span style="color:var(--text-dim);font-size:12px">共 {{ audit.total }} 条</span>
          <button class="btn btn-sm" @click="handleRefreshAudit">刷新</button>
        </span>
      </div>
      <p style="color:var(--text-dim);font-size:12px;margin:0 0 8px">
        渠道、群组与成员、订阅、任务的每一次变更都留一条记录：谁、什么时候、改了什么。
        与「发送记录」不同，变更历史**长期保留**，不会被自动清理。
      </p>

      <!-- 筛选：对象类型（选项来自读接口，加一种对象类型只改后端）+ 分页 -->
      <div class="wp-audit-filters">
        <div class="form-row">
          <label>对象类型</label>
          <select class="select" aria-label="按对象类型筛选" v-model="auditFilterForm.objectType">
            <option value="">全部对象类型</option>
            <option v-for="item in audit.objectTypes" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
        </div>
        <div style="display:flex;gap:8px;align-items:flex-end">
          <button class="btn btn-primary btn-sm" type="button" @click="handleApplyAuditFilters">筛选</button>
          <button
            class="btn btn-sm"
            type="button"
            :disabled="!auditFiltered"
            @click="auditFilterForm = { objectType: '' }; handleApplyAuditFilters()"
          >清空筛选</button>
        </div>
      </div>

      <div class="data-table-wrap wp-card-table-wrap luyun-scrollbar" style="max-height:520px">
        <table class="data-table wp-card-table wp-audit-table">
          <thead>
            <tr>
              <th style="cursor:default">时间</th>
              <th style="cursor:default">操作人</th>
              <th style="cursor:default">操作</th>
              <th style="cursor:default">对象</th>
              <th style="cursor:default">变更内容</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!audit.rows.length">
              <td colspan="5" class="empty-state">
                {{ auditFiltered ? '没有符合条件的记录，可换个对象类型或清空筛选' : '暂无变更记录' }}
              </td>
            </tr>
            <tr v-for="item in audit.rows" :key="item.id">
              <td data-label="时间">{{ formatDeliveryTime(item.created_at) }}</td>
              <td data-label="操作人">{{ item.actor || '（未知）' }}</td>
              <td data-label="操作">
                <!-- 动作的颜色是给人扫的：停用 / 删除是"少了一个收件人"的两种，红色。 -->
                <span
                  class="badge"
                  :style="item.action === 'delete'
                    ? 'color:var(--red);border-color:var(--red)'
                    : (item.action === 'disable' ? 'color:var(--yellow);border-color:var(--yellow)' : '')"
                >{{ auditActionLabel(item.action) }}</span>
              </td>
              <td data-label="对象">
                <div>{{ auditObjectLabel(item.object_type) }}</div>
                <div style="color:var(--text-dim);font-size:12px">{{ item.object_name }}</div>
              </td>
              <td data-label="变更内容">
                <div
                  v-for="(line, index) in auditChanges(item)"
                  :key="index"
                  style="font-size:12px"
                >{{ line }}</div>
                <span v-if="!auditChanges(item).length" style="color:var(--text-dim);font-size:12px">
                  未改动任何字段
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="wp-audit-pager">
        <button
          class="btn btn-sm"
          type="button"
          :disabled="audit.page <= 1"
          @click="handleAuditPage(audit.page - 1)"
        >上一页</button>
        <span style="color:var(--text-dim);font-size:12px">
          第 {{ audit.page }} / {{ Math.max(audit.pages, 1) }} 页
        </span>
        <button
          class="btn btn-sm"
          type="button"
          :disabled="audit.page >= audit.pages"
          @click="handleAuditPage(audit.page + 1)"
        >下一页</button>
        <span style="color:var(--text-dim);font-size:11px">
          变更历史长期保留，不随发送记录的保留天数清理。
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 页内 tab 条（D4）：390 下 5 个标签与「接口版本 v2」抢同一行，flex 会把每个按钮压到
   比文字还窄，中文就逐字换行成「渠 道」「定时任 务」那一列。两条一起上：
   - 标签行自己横向滚动，按钮不收缩、不换行（`flex: 0 0 auto` + `white-space: nowrap`）；
   - 版本徽章窄屏让位 —— 它只是"这份 bundle 是哪一版"，真不匹配时页顶有专门的提示条。 */
.view-tabs { flex-wrap: nowrap; }
.wp-tabbar {
  display: flex;
  gap: 4px;
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
}
.wp-tabbar .view-tab { flex: 0 0 auto; white-space: nowrap; }
.wp-api-version {
  margin-left: auto;
  align-self: center;
  flex: 0 0 auto;
  white-space: nowrap;
  color: var(--text-dim);
  font-size: 11px;
}
@media (max-width: 700px) {
  .wp-api-version { display: none; }
}

@media (max-width: 980px) {
  /* 窄屏单列。**必须是 `minmax(0, 1fr)` 而不是 `1fr`**：轨道里的卡片带
     `display:flex` 的标题行与 form，`1fr` 的自动最小尺寸会被内容的固有宽度顶起来
     ——390 下实测轨道被撑到 493px（页面体 scrollWidth 503 > 390，需横向拖动），
     换成 `minmax(0, 1fr)` 才会真正收缩到 370px。这一层是 A11 里「卫生群 / 启用徽章
     被挤出屏幕」的根因，徽章换行只是让内容更窄，挡不住轨道自己被撑宽。 */
  .grid { grid-template-columns: minmax(0, 1fr) !important; }
  /* 卡片本身也要能收缩：块级子元素的 min-width 默认是 0。 */
  .grid > div > * { min-width: 0; }
}

/* 渠道卡片的标题行（名称 + 启用/未订阅徽章）：桌面档是左右各一边，窄屏
   （390 下卡片内容区只有 ~300px）必须允许换行 —— 原来是 `justify-content: space-between`
   且名称不收缩，徽章被顶到 380–431px，视口 390px 直接看不见。
   而"这个群收到了什么"正是这一页最关键的信息。 */
.wp-hook-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.wp-hook-name { font-size: 13px; min-width: 0; overflow-wrap: anywhere; }
.wp-hook-badges { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }

/* 窄屏直接把徽章换到名称下一行，不再和名称抢同一行的宽度。 */
@media (max-width: 560px) {
  .wp-hook-head { justify-content: flex-start; }
  .wp-hook-name { flex: 1 1 100%; }
  .wp-hook-badges { flex: 1 1 100%; }
}

/* 订阅矩阵：零订阅的行整行高亮（页顶还有一条提示条，两处说的是同一件事）。 */
:deep(.data-table tr.wp-matrix-zero) td { background: rgba(245, 158, 11, 0.08); }
:deep(.data-table.wp-matrix th),
:deep(.data-table.wp-matrix td) { white-space: nowrap; }

/* 多选列表里「先选内容类型」的那一列按钮：选中态要看得出来。 */
.wp-pick-row {
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
  width: 100%;
}
.wp-pick-row.active { border-color: var(--accent); color: var(--accent); }
.wp-pick-row.wp-matrix-zero { color: var(--yellow); }
.wp-pick-row.wp-matrix-zero.active { border-color: var(--yellow); }

/* 未选内容类型时参数区的占位：它说明"这块地方是什么"，不是错误提示。 */
.wp-job-hint {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-dim);
}

/* 发送记录（票 07）：筛选行在窄屏要能换行（三个下拉 + 两个按钮在一行里挤不下）。 */
.wp-delivery-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin-bottom: 10px;
}
.wp-delivery-filters .form-row { min-width: 150px; flex: 1 1 150px; }

/* 失败行整行标出来：一屏几十行里，失败的那几条要第一眼看得见。 */
:deep(.data-table tr.wp-delivery-failed) td {
  background: rgba(239, 68, 68, 0.08);
  border-left-color: var(--red);
}
:deep(.data-table tr.wp-delivery-failed) td:first-child { border-left: 2px solid var(--red); }

.wp-delivery-pager {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 10px;
}

/* 变更历史（票 11）：筛选行与分页条与发送记录同一套写法，窄屏要能换行。 */
.wp-audit-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin-bottom: 10px;
}
.wp-audit-filters .form-row { min-width: 150px; flex: 1 1 150px; }

.wp-audit-pager {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 10px;
}

/* 变更内容那一列是「改前 → 改后」的逐行文本：允许换行，别把表格撑出横向滚动。 */
:deep(.data-table.wp-audit-table td) { vertical-align: top; }
:deep(.data-table.wp-audit-table td:nth-child(5)) { white-space: normal; min-width: 200px; }

/* ══ U4 · 加载态 ═══════════════════════════════════════════════════════════
   数据没回来时显示骨架，不显示「0 个 / 暂无渠道」那种空态文案 —— 后者是"确实没有"。
   类名里带 skeleton：真机走查按 `loading|skeleton|spinner|加载中` 统计加载中的可见节点。 */
.wp-skeleton { display: flex; flex-direction: column; gap: 8px; padding: 10px 0; }
.wp-skeleton__line {
  height: 12px;
  border-radius: 6px;
  background: linear-gradient(90deg, var(--card2), var(--border), var(--card2));
  background-size: 200% 100%;
  animation: wp-skeleton-shine 1.4s ease-in-out infinite;
}
.wp-skeleton__line--short { width: 45%; }
@keyframes wp-skeleton-shine {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}
@media (prefers-reduced-motion: reduce) {
  .wp-skeleton__line { animation: none; }
}

/* ══ U5 · 加载失败提示条 ═══════════════════════════════════════════════════
   人话（`error`）在明面上、技术原文折叠在「详情」里，旁边一个「重试」。 */
.wp-load-error { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wp-load-error__text { flex: 1 1 220px; }
.wp-load-error__detail { flex: 1 1 100%; font-size: 11px; }
.wp-load-error__detail summary { cursor: pointer; color: var(--text-dim); }
.wp-load-error__detail pre {
  margin: 6px 0 0;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--card2);
  color: var(--text-dim);
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-all;
}

/* ══ U8 · 「立即发送」的禁用态 ═════════════════════════════════════════════
   原来禁用只靠全局 `.btn:disabled{opacity:.5}`：绿底透出来仍是最显眼的主按钮。禁用
   就该是灰的，可点才上绿。 */
.wp-send-now { background: var(--green); border-color: var(--green); color: #fff; }
.wp-send-now:hover:not(:disabled) { border-color: var(--green); }
.wp-send-now:disabled {
  background: var(--card2);
  border-color: var(--border);
  color: var(--text-dim);
  opacity: 1;
}

/* ══ U1 · 保存后把改过的那张任务卡标出来 ═══════════════════════════════════
   列表按 id 稳定排（后端 ORDER BY id），保存后不会换位；高亮 + scrollIntoView 让店长
   确认"改的是这一张"。2.4 秒后自己退掉，不跟卡片本身的选中边框混成一种状态。 */
.wp-job-card.is-saved { animation: wp-job-flash 2.4s ease-out; }
@keyframes wp-job-flash {
  from { box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.6); }
  to { box-shadow: 0 0 0 3px rgba(99, 102, 241, 0); }
}
@media (prefers-reduced-motion: reduce) {
  .wp-job-card.is-saved { animation: none; border-color: var(--accent); }
}

/* ══ U3 · 订阅矩阵的整格热区 ═══════════════════════════════════════════════
   每一格原来只有一个 16×16 的勾选框可点（64 格 × 3 档实测 192 次点空）。命中的是整格：
   `<td>` 接管点击（见 handleMatrixCellClick），里面这层命中区把热区撑到 ≥44×44。 */
:deep(.data-table.wp-matrix td.wp-matrix-cell) { cursor: pointer; padding: 4px 8px; }
.wp-matrix-hit {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  min-height: 44px;
}
:deep(.data-table.wp-matrix td.wp-matrix-cell:hover) { background: rgba(99, 102, 241, 0.1); }

/* ══ U2 · 窄屏的宽表与矩阵 ═════════════════════════════════════════════════
   两个方向：
   - 发送记录 / 变更历史在 ≤700px 改**卡片式**（一行一卡、字段名 + 值），不再靠横向拖；
   - 订阅矩阵 ≤8 渠道时仍是表格，至少冻结「内容类型」列，右侧加渐隐，提示右边还有渠道。 */
.wp-scroll-hint { display: none; }
.wp-matrix-wrap {
  /* 滚动阴影（纯 CSS）：内容没滚到头时左右边缘各有渐隐，滚到底自己消失，
     不需要 JS 判断能不能滚。四层顺序 = 两个"遮罩" + 两个"阴影"，
     配套的 local / local / scroll / scroll 由简写里的顺序给出。 */
  background:
    linear-gradient(to right, var(--card) 40%, rgba(17, 24, 39, 0)) left center / 28px 100% no-repeat local,
    linear-gradient(to left, var(--card) 40%, rgba(17, 24, 39, 0)) right center / 28px 100% no-repeat local,
    radial-gradient(farthest-side at 0 50%, rgba(0, 0, 0, 0.45), rgba(0, 0, 0, 0)) left center / 12px 100% no-repeat scroll,
    radial-gradient(farthest-side at 100% 50%, rgba(0, 0, 0, 0.45), rgba(0, 0, 0, 0)) right center / 12px 100% no-repeat scroll;
}
/* 冻结「内容类型」列：横向滚动时它留在原地，否则滑到右边就不知道这一行是哪类内容。
   表头那格还要保住全局的 `position: sticky; top: 0`，两层一起生效。 */
:deep(.data-table.wp-matrix th:first-child),
:deep(.data-table.wp-matrix td:first-child) {
  position: sticky;
  left: 0;
  z-index: 1;
  background: var(--card);
}
:deep(.data-table.wp-matrix th:first-child) { background: var(--card2); z-index: 2; }

/* ══ U16 · 发送记录里的「最后一次错误」 ════════════════════════════════════
   原来是 `overflow:hidden` 硬裁 + 只有桌面 hover 才看得到的 `title`。现在：行内最多两行
   摘要（人话），「详情」展开完整原文 —— 触屏也读得到。 */
:deep(.data-table.wp-delivery-table td.wp-delivery-error) {
  white-space: normal;
  color: var(--text-dim);
}
@media (min-width: 701px) {
  /* 只在桌面档限宽：7 列同排时第 7 列不设上限会把「尝试次数」挤没。
     ≤700px 是卡片式（每格整行宽），限宽只会把内容顶出横向滚动。 */
  :deep(.data-table.wp-delivery-table td.wp-delivery-error) { max-width: 320px; }
}
.wp-delivery-error__text {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  /* `min-width:0` + `anywhere` 是必需的：flex 子项默认不肯缩到 min-content 以下，
     而企微的错误原文里常有 `HTTPSConnectionPool(host='qyapi.weixin.qq.com',` 这种
     40+ 字符的不可断词 —— 不压住它，摘要自己就把卡片顶出横向滚动（390 实测 26px）。 */
  min-width: 0;
  overflow-wrap: anywhere;
}
.wp-delivery-error__toggle { margin-top: 4px; }
.wp-error-full__title { color: var(--text-dim); font-size: 11px; margin-bottom: 4px; }
.wp-error-full__raw {
  margin: 0;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--card2);
  color: var(--text);
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-all;
}
.wp-error-full__meta { color: var(--text-dim); font-size: 11px; margin-top: 4px; }
:deep(.data-table tr.wp-delivery-detail td) { background: rgba(17, 24, 39, 0.6); }

/* ══ U11 · 触屏档（≤700px）把控件放大到能点 ═══════════════════════════════
   三档实测控件尺寸完全相同：`.btn-sm` 42×24、页内 tab 52×31、输入框高 34 —— 手机档
   仍是桌面尺寸。桌面档维持现状，只在窄屏这一档放大。 */
@media (max-width: 700px) {
  :deep(.btn) { min-height: 40px; }
  :deep(.btn-sm) { min-height: 40px; padding: 8px 12px; font-size: 12px; }
  .wp-tabbar .view-tab { min-height: 44px; padding: 10px 14px; }
  :deep(.input), :deep(.select) { min-height: 44px; }
  .wp-matrix-hit { min-width: 44px; min-height: 44px; }

  /* 宽表 → 卡片式：一行一卡，`data-label` 当字段名。表头不再需要（每个值自己带名字）。 */
  .wp-card-table-wrap { max-height: none !important; overflow-x: visible; }
  .wp-card-table,
  .wp-card-table tbody,
  .wp-card-table tr,
  .wp-card-table td { display: block; width: 100%; }
  .wp-card-table thead { display: none; }
  .wp-card-table tbody tr {
    border: 1px solid var(--border);
    border-radius: 8px;
    margin: 0 0 8px;
    padding: 6px 8px;
    background: var(--card2);
  }
  .wp-card-table tbody tr:hover { background: var(--card2); }
  .wp-card-table td {
    display: flex;
    gap: 8px;
    padding: 3px 0;
    border-bottom: none;
    white-space: normal;
    text-align: left;
    min-width: 0;
  }
  .wp-card-table td::before {
    content: attr(data-label);
    flex: 0 0 76px;
    color: var(--text-dim);
  }
  /* 没有 data-label 的格子（空态行 / 展开的详情行）不占那 76px 的字段名列。 */
  .wp-card-table td:not([data-label])::before { display: none; }
  .wp-card-table tr td.empty-state { display: block; }
  .wp-card-table tr.wp-delivery-detail { padding: 0 8px 6px; }
  .wp-scroll-hint { display: block; color: var(--text-dim); font-size: 11px; margin: 0 0 6px; }
}
</style>
