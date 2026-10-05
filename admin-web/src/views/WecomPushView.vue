<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  useWecomPush, PUSH_TYPE_NAMES, webhookDeleteConfirmText, hygieneFeedWarning,
  canSendNow, sendNowConfirmText,
} from '../composables/useWecomPush'
import { useStationsStore } from '../stores/stations'
import SvgIcon from '../components/SvgIcon.vue'
import LuyunCheckbox from '../components/ui/LuyunCheckbox.vue'
import LuyunTimePicker from '../components/ui/LuyunTimePicker.vue'

const {
  webhooks, jobs, logs, meta, selectedJobId, previewContent, previewMeta, loading, error,
  webhookForm, jobForm, resetWebhookForm, resetJobForm,
  loadAll, loadJobs, loadLogs, editWebhook, saveWebhook, deleteWebhook, toggleWebhookEnabled, testWebhook,
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

const isEditingWebhook = computed(() => !!webhookForm.id)
// 没有可用的卫生群时，卫生消息一条都不发 —— 这件事必须在页面上看得见
const hygieneWarning = computed(() => hygieneFeedWarning(webhooks.value))
const selectedJob = computed(() => jobs.value.find((j) => j.id === selectedJobId.value))
const jobStations = computed(() => stationsStore.list.filter((s) => s.id && s.id !== 'loumian'))
// 预览为空时「立即发送」不可点：这时页面没有任何可核对的内容，一点却会真的外发
// （后端 send-now 自己现算正文），是全页最容易误触的一条。
const sendReady = computed(() => canSendNow({
  job: selectedJob.value,
  content: previewContent.value,
}))

async function handleSaveWebhook() {
  try {
    await saveWebhook()
    flash('Webhook 已保存', 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleDeleteWebhook(id) {
  const target = webhooks.value.find((w) => w.id === id)
  if (!window.confirm(webhookDeleteConfirmText(target))) return
  try {
    await deleteWebhook(id)
    flash('Webhook 已删除', 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleToggleWebhook(item) {
  try {
    await toggleWebhookEnabled(item)
    flash(item.enabled ? `已停用「${item.name}」` : `已启用「${item.name}」`, 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleTestWebhook(id) {
  try {
    const result = await testWebhook(id)
    flash(result.success ? '测试消息已发送' : `测试失败：${result.error || result.response_text}`, result.success ? 'success' : 'error')
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
    flash('已填入模板，请选择 Webhook 后保存', 'success')
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
    <div v-if="hygieneWarning" class="dash-error-banner"><SvgIcon name="alert-triangle" :size="14" /> {{ hygieneWarning }}</div>
    <div v-if="toastMsg" class="badge" :style="toastType === 'error' ? 'color:var(--red);border-color:var(--red)' : 'color:var(--green);border-color:var(--green)'">
      {{ toastMsg }}
    </div>

    <div class="grid" style="grid-template-columns: minmax(280px, 420px) minmax(0, 1fr)">
      <div style="display:flex;flex-direction:column;gap:12px">
        <!-- Webhook 管理 -->
        <div class="card">
          <div class="panel-title" style="display:flex;justify-content:space-between">
            <span>Webhook 管理</span>
            <span style="color:var(--text-dim);font-size:12px">{{ webhooks.length }} 个</span>
          </div>
          <form @submit.prevent="handleSaveWebhook" style="display:flex;flex-direction:column;gap:10px">
            <div class="badge" :style="isEditingWebhook ? 'color:var(--yellow);border-color:var(--yellow)' : ''">
              {{ isEditingWebhook ? `正在编辑：${webhookForm.name}` : '新增地址' }}
              <button v-if="isEditingWebhook" type="button" class="btn btn-sm" style="margin-left:8px" @click="resetWebhookForm">取消编辑</button>
            </div>
            <div class="form-row">
              <label>名称</label>
              <input class="input" v-model="webhookForm.name" placeholder="例如：管理群日报" maxlength="60" required />
            </div>
            <div class="form-row">
              <label>Webhook 地址</label>
              <input class="input" v-model="webhookForm.webhook_url" :placeholder="isEditingWebhook ? '留空表示不更换地址' : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...'" maxlength="500" />
            </div>
            <div class="form-row">
              <label>备注</label>
              <textarea class="input" v-model="webhookForm.notes" maxlength="200" placeholder="可选" style="min-height:74px;resize:vertical"></textarea>
            </div>
            <label class="luyun-check-row">
              <LuyunCheckbox v-model="webhookForm.enabled" /> 启用
            </label>
            <label class="luyun-check-row">
              <LuyunCheckbox v-model="webhookForm.hygiene_feed" /> 这是卫生群
            </label>
            <p style="color:var(--text-dim);font-size:12px;margin:0">
              卫生提醒与验收照片只发勾选的群；一个都不勾就一条都不发。
            </p>
            <div style="display:flex;gap:8px">
              <button class="btn btn-primary" type="submit">{{ isEditingWebhook ? '保存修改' : '新增地址' }}</button>
              <button class="btn" type="button" @click="resetWebhookForm">清空 / 新增</button>
            </div>
          </form>

          <div style="display:flex;flex-direction:column;gap:8px;margin-top:12px">
            <div v-if="!webhooks.length" class="empty-state">暂无 webhook</div>
            <div v-for="item in webhooks" :key="item.id" class="card wp-hook-card" style="padding:10px">
              <div class="wp-hook-head">
                <strong class="wp-hook-name">{{ item.name }}</strong>
                <span class="wp-hook-badges">
                  <span v-if="item.hygiene_feed" class="badge" style="color:var(--cyan);border-color:var(--cyan)">卫生群</span>
                  <span class="badge" :style="item.enabled ? 'color:var(--green);border-color:var(--green)' : ''">{{ item.enabled ? '启用' : '停用' }}</span>
                </span>
              </div>
              <div style="color:var(--text-dim);font-size:12px;word-break:break-all">{{ item.webhook_url_masked }}</div>
              <div v-if="item.notes" style="color:var(--text-dim);font-size:12px">{{ item.notes }}</div>
              <div style="display:flex;gap:6px;margin-top:8px">
                <button class="btn btn-sm" @click="editWebhook(item)">编辑</button>
                <button class="btn btn-sm" @click="handleToggleWebhook(item)">{{ item.enabled ? '停用' : '启用' }}</button>
                <button class="btn btn-sm" @click="handleTestWebhook(item.id)">测试</button>
                <button class="btn btn-sm btn-danger" @click="handleDeleteWebhook(item.id)">删除</button>
              </div>
            </div>
          </div>
        </div>

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
                <label>目标 Webhook</label>
                <select class="select" v-model="jobForm.webhook_id" required>
                  <option v-for="w in webhooks" :key="w.id" :value="w.id">{{ w.name }}{{ w.enabled ? '' : '（停用）' }}</option>
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
              <button class="btn" type="button" @click="handleApplyTemplate('data_quality_daily')">＋ 数据质量模板</button>
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
                类型：{{ PUSH_TYPE_NAMES[item.push_type] || item.push_type }} · 日期：{{ item.date_range_mode === 'yesterday' ? '昨天' : '当天' }}
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
        <div class="card">
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

        <div class="card">
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
                  <td>{{ PUSH_TYPE_NAMES[item.push_type] || item.push_type }}</td>
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
  /* 卡片本身也要能收缩：块级子元素的 min-width 默认是 auto。 */
  .grid > div > * { min-width: 0; }
}

/* Webhook 卡片的标题行（名称 + 卫生群/启用徽章）：桌面档是左右各一边，窄屏
   （390 下卡片内容区只有 ~300px）必须允许换行 —— 原来是 `justify-content: space-between`
   且名称不收缩，徽章被顶到 380–431px，视口 390px 直接看不见「卫生群」。
   而"这个群是不是卫生群"正是这一页最关键的信息（卫生提醒只发勾选的群）。 */
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
</style>
