<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  useWecomPush, WECOM_PUSH_API_VERSION, MATRIX_CHANNEL_LIMIT, pushTypeLabel,
  channelDeleteConfirmText, channelGroupNames, channelTopicNames, formatSentAt,
  canSendNow, sendNowConfirmText,
} from '../composables/useWecomPush'
import { useStationsStore } from '../stores/stations'
import SvgIcon from '../components/SvgIcon.vue'
import LuyunCheckbox from '../components/ui/LuyunCheckbox.vue'
import LuyunTimePicker from '../components/ui/LuyunTimePicker.vue'

// 页内 tab：不改路由（页面清单契约保持绿）。五个 tab 里的「变更历史」是第二批，
// 本票先摆四个；定时任务与发送记录两个 tab 的内容本票**原样搬入**，票 07/08 各自重做。
const TABS = [
  { id: 'channels', name: '渠道' },
  { id: 'subscriptions', name: '订阅' },
  { id: 'jobs', name: '定时任务' },
  { id: 'logs', name: '发送记录' },
]

const {
  meta, channels, topics, groups, jobs, logs, matrix, selectedJobId, previewContent,
  previewMeta, error, activeTab, channelForm, channelGroupForm, multiSelect,
  contractWarning, zeroSubscriptionTip, viewMode, zeroTopicIds,
  resetChannelForm, resetChannelGroupForm, resetJobForm, jobForm,
  loadAll, loadSubscriptions, loadJobs, loadLogs, loadChannels,
  editChannel, saveChannel, deleteChannel, toggleChannelEnabled, testChannel,
  editChannelGroup, saveChannelGroup, deleteChannelGroup,
  addGroupMember, removeGroupMember,
  toggleSubscription, pickMultiSelectTopic, saveMultiSelectTopic,
  editJob, applyJobTemplate, saveJob, deleteJob, previewSelectedJob, sendSelectedJob,
} = useWecomPush()

const stationsStore = useStationsStore()
const toastMsg = ref('')
const toastType = ref('info')

function flash(msg, type = 'info') {
  toastMsg.value = msg
  toastType.value = type
  setTimeout(() => { if (toastMsg.value === msg) toastMsg.value = '' }, 3000)
}

const isEditingChannel = computed(() => !!channelForm.id)
const isEditingGroup = computed(() => !!channelGroupForm.id)
const selectedJob = computed(() => jobs.value.find((j) => j.id === selectedJobId.value))
const jobStations = computed(() => stationsStore.list.filter((s) => s.id && s.id !== 'loumian'))
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
// 某个内容类型当前勾了哪些渠道（矩阵与多选列表共用同一份判据）
const subscriptionIds = computed(() => {
  const map = {}
  for (const row of matrixRows.value) {
    map[row.id] = (row.channels || []).filter((item) => item.enabled).map((item) => Number(item.id))
  }
  return map
})

function topicSubscribed(topicId, channelId) {
  return (subscriptionIds.value[topicId] || []).includes(Number(channelId))
}
function topicHasPhotos(row) {
  return !!row.contains_employee_photos
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
async function handleTestChannel(id) {
  try {
    const result = await testChannel(id)
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
    await saveMultiSelectTopic()
    flash('订阅已保存，下一次触发即按新订阅投递', 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleSaveJob() {
  try {
    await saveJob()
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
    flash(result.success ? '消息已发送' : `发送失败：${result.error || result.response_text}`, result.success ? 'success' : 'error')
  } catch (e) { flash(e.message, 'error') }
}
async function copyPreview() {
  if (!previewContent.value) return
  await navigator.clipboard.writeText(previewContent.value)
  flash('预览内容已复制', 'success')
}
function handleApplyTemplate(id) {
  try {
    applyJobTemplate(id)
    flash('已填入模板，请选择渠道后保存', 'success')
  } catch (e) { flash(e.message, 'error') }
}

function fmtSentAt(s) {
  return (s || '').replace('T', ' ').slice(0, 19)
}

onMounted(async () => {
  let stationError = ''
  try {
    await stationsStore.load()
  } catch (e) {
    stationError = '档口数据加载失败：' + (e.message || '未知错误')
    flash(stationError, 'error')
  }
  await loadAll()
  // loadAll() 成功时会把 error 清空，这里补上档口加载失败的持久提示，避免被吞掉
  if (stationError && !error.value) error.value = stationError
})
</script>

<template>
  <div style="display:flex;flex-direction:column;gap:12px">
    <div v-if="error" class="dash-error-banner"><SvgIcon name="alert-triangle" :size="14" /> 企微推送数据加载失败：{{ error }}</div>
    <!-- 接口版本不匹配：只提示，不阻断操作（旧页面照样能读，写接口由后端把关） -->
    <div v-if="contractWarning" class="dash-error-banner"><SvgIcon name="alert-triangle" :size="14" /> {{ contractWarning }}</div>
    <!-- 零订阅：这类内容当前一条都不发，页面上不写出来店长只会以为"今天没有" -->
    <div v-if="zeroSubscriptionTip" class="dash-error-banner"><SvgIcon name="alert-triangle" :size="14" /> {{ zeroSubscriptionTip }}</div>
    <div v-if="toastMsg" class="badge" :style="toastType === 'error' ? 'color:var(--red);border-color:var(--red)' : 'color:var(--green);border-color:var(--green)'">
      {{ toastMsg }}
    </div>

    <div class="view-tabs">
      <button
        v-for="tab in TABS"
        :key="tab.id"
        class="view-tab"
        :class="{ active: activeTab === tab.id }"
        type="button"
        @click="activeTab = tab.id"
      >{{ tab.name }}</button>
      <span style="margin-left:auto;color:var(--text-dim);font-size:11px;align-self:center">
        接口版本 {{ WECOM_PUSH_API_VERSION }}
      </span>
    </div>

    <!-- ═══ 渠道 ══════════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'channels'" class="grid" style="grid-template-columns: minmax(280px, 420px) minmax(0, 1fr)">
      <div style="display:flex;flex-direction:column;gap:12px">
        <!-- 渠道地址 -->
        <div class="card">
          <div class="panel-title" style="display:flex;justify-content:space-between">
            <span>渠道</span>
            <span style="color:var(--text-dim);font-size:12px">{{ channels.length }} 个</span>
          </div>
          <form @submit.prevent="handleSaveChannel" style="display:flex;flex-direction:column;gap:10px">
            <div class="badge" :style="isEditingChannel ? 'color:var(--yellow);border-color:var(--yellow)' : ''">
              {{ isEditingChannel ? `正在编辑：${channelForm.name}` : '新增渠道' }}
              <button v-if="isEditingChannel" type="button" class="btn btn-sm" style="margin-left:8px" @click="resetChannelForm">取消编辑</button>
            </div>
            <div class="form-row">
              <label>名称</label>
              <input class="input" v-model="channelForm.name" placeholder="例如：管理群日报" maxlength="60" required />
            </div>
            <div class="form-row">
              <label>Webhook 地址</label>
              <input class="input" v-model="channelForm.webhook_url" :placeholder="isEditingChannel ? '留空表示不更换地址' : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...'" maxlength="500" />
            </div>
            <div class="form-row">
              <label>备注</label>
              <textarea class="input" v-model="channelForm.notes" maxlength="200" placeholder="可选" style="min-height:74px;resize:vertical"></textarea>
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
            <div v-if="!channels.length" class="empty-state">暂无渠道</div>
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
                <button class="btn btn-sm" @click="handleTestChannel(item.id)">测试</button>
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
            <span style="color:var(--text-dim);font-size:12px">{{ groups.length }} 个</span>
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
              <label>群组名称</label>
              <input class="input" v-model="channelGroupForm.name" maxlength="60" placeholder="例如：日报群组" required />
            </div>
            <div class="form-row">
              <label>备注</label>
              <input class="input" v-model="channelGroupForm.notes" maxlength="200" placeholder="可选" />
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
            <div v-if="!groups.length" class="empty-state">暂无群组</div>
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
            {{ matrixColumns.length }} 个渠道 · {{ matrixRows.length }} 类内容
          </span>
        </div>

        <!-- 渠道不超过阈值：内容 × 渠道的勾选矩阵 -->
        <template v-if="!isMultiSelectView">
          <p v-if="!matrixColumns.length" class="empty-state">还没有渠道。先到「渠道」tab 新增一个。</p>
          <div v-else class="data-table-wrap luyun-scrollbar" style="max-height:520px">
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
                  <td v-for="channel in matrixColumns" :key="channel.id" style="text-align:center">
                    <LuyunCheckbox
                      :model-value="topicSubscribed(row.id, channel.id)"
                      @update:model-value="() => handleToggleSubscription(row, channel)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
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

    <!-- ═══ 定时任务 / 发送记录（票 07/08 各自重做，本票原样保留）══════════ -->
    <div v-if="activeTab === 'jobs' || activeTab === 'logs'" class="grid" style="grid-template-columns: minmax(280px, 420px) minmax(0, 1fr)">
      <div v-if="activeTab === 'jobs'" style="display:flex;flex-direction:column;gap:12px">
        <!-- 推送任务 -->
        <div class="card">
          <div class="panel-title" style="display:flex;justify-content:space-between">
            <span>推送任务</span>
            <span style="color:var(--text-dim);font-size:12px">{{ jobs.length }} 个</span>
          </div>
          <form @submit.prevent="handleSaveJob" style="display:flex;flex-direction:column;gap:10px">
            <div class="form-row">
              <label>任务名称</label>
              <input class="input" v-model="jobForm.name" maxlength="60" required />
            </div>
            <div class="form-row">
              <label>推送类型</label>
              <select class="select" v-model="jobForm.push_type">
                <option value="sales_report_text">销售报表文字版</option>
                <option value="data_quality_alert">数据质量告警</option>
              </select>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
              <div class="form-row">
                <label>目标渠道</label>
                <select class="select" v-model="jobForm.webhook_id" required>
                  <option v-for="w in channels" :key="w.id" :value="w.id">{{ w.name }}{{ w.enabled ? '' : '（停用）' }}</option>
                </select>
              </div>
              <div class="form-row">
                <label>每天推送时间</label>
                <LuyunTimePicker v-model="jobForm.schedule_time" />
              </div>
            </div>
            <div v-if="jobForm.push_type !== 'data_quality_alert'" style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
              <div class="form-row">
                <label>报表日期</label>
                <select class="select" v-model="jobForm.date_range_mode">
                  <option value="today">当天</option>
                  <option value="yesterday">昨天</option>
                </select>
              </div>
              <div class="form-row">
                <label>档口筛选</label>
                <select class="select" v-model="jobForm.station">
                  <option value="">全部（排除楼面）</option>
                  <option v-for="s in jobStations" :key="s.id" :value="s.id">{{ s.name }}</option>
                </select>
              </div>
            </div>
            <div v-else style="font-size:11px;line-height:1.5;color:var(--text-dim)">
              数据质量任务会读取采集健康状态与日终对账报告，并统计未映射菜品。建议推送时间设在日终对账（默认 22:05）之后。
            </div>
            <div class="form-row">
              <label>备注</label>
              <textarea class="input" v-model="jobForm.notes" maxlength="200" placeholder="可选" style="min-height:74px;resize:vertical"></textarea>
            </div>
            <label class="luyun-check-row">
              <LuyunCheckbox v-model="jobForm.enabled" /> 启用定时推送
            </label>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <button class="btn btn-primary" type="submit">保存任务</button>
              <button class="btn" type="button" @click="resetJobForm">清空</button>
              <button class="btn" type="button" @click="handleApplyTemplate('data_quality_daily')">+ 数据质量模板</button>
            </div>
          </form>

          <div style="display:flex;flex-direction:column;gap:8px;margin-top:12px">
            <div v-if="!jobs.length" class="empty-state">暂无推送任务</div>
            <div
              v-for="item in jobs"
              :key="item.id"
              class="card"
              style="padding:10px;cursor:pointer"
              :style="selectedJobId === item.id ? 'border-color:var(--accent)' : ''"
              @click="selectedJobId = item.id"
            >
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                <strong style="font-size:13px">{{ item.name }}</strong>
                <span class="badge" :style="item.enabled ? 'color:var(--green);border-color:var(--green)' : ''">{{ item.enabled ? item.schedule_time : '停用' }}</span>
              </div>
              <div style="color:var(--text-dim);font-size:12px">目标：{{ item.webhook_name || '未配置' }}</div>
              <div style="color:var(--text-dim);font-size:12px">
                类型：{{ pushTypeLabel(item.push_type) }} · 日期：{{ item.date_range_mode === 'yesterday' ? '昨天' : '当天' }}
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
              <button
                class="btn btn-sm"
                style="background:var(--green);border-color:var(--green);color:#fff"
                :disabled="!sendReady"
                :title="sendReady ? '发送前会再确认一次' : '先选择任务并刷新预览'"
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
            class="input"
            readonly
            :value="previewContent"
            placeholder="选择任务后点击刷新预览"
            style="min-height:280px;width:100%;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap"
          ></textarea>
        </div>

        <div v-if="activeTab === 'logs'" class="card">
          <div class="panel-title" style="display:flex;justify-content:space-between">
            <span>发送记录</span>
            <button class="btn btn-sm" @click="loadLogs">刷新</button>
          </div>
          <div class="data-table-wrap luyun-scrollbar" style="max-height:340px">
            <table class="data-table">
              <thead>
                <tr><th>时间</th><th>目标</th><th>类型</th><th>状态</th><th>字节</th><th>结果</th></tr>
              </thead>
              <tbody>
                <tr v-if="!logs.length"><td colspan="6" class="empty-state">暂无发送记录</td></tr>
                <tr v-for="item in logs" :key="item.id">
                  <td>{{ fmtSentAt(item.sent_at) }}</td>
                  <td>{{ item.webhook_name }}</td>
                  <td>{{ pushTypeLabel(item.push_type) }}</td>
                  <td>
                    <span class="badge" :style="item.status === 'success' ? 'color:var(--green);border-color:var(--green)' : 'color:var(--red);border-color:var(--red)'">
                      {{ item.status === 'success' ? '成功' : '失败' }}
                    </span>
                  </td>
                  <td>{{ item.message_bytes || 0 }}</td>
                  <td style="color:var(--text-dim);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" :title="item.error || item.response_text">
                    {{ item.error || item.response_text || '' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
</style>
