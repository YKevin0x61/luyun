<script setup>
import { computed, onMounted, ref } from 'vue'
import { useWecomPush, PUSH_TYPE_NAMES } from '../composables/useWecomPush'
import { useStationsStore } from '../stores/stations'
import SvgIcon from '../components/SvgIcon.vue'
import LuyunCheckbox from '../components/ui/LuyunCheckbox.vue'
import LuyunTimePicker from '../components/ui/LuyunTimePicker.vue'

const {
  webhooks, jobs, logs, meta, selectedJobId, previewContent, previewMeta, loading, error,
  webhookForm, jobForm, resetWebhookForm, resetJobForm,
  loadAll, loadJobs, loadLogs, editWebhook, saveWebhook, deleteWebhook, testWebhook,
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
const selectedJob = computed(() => jobs.value.find((j) => j.id === selectedJobId.value))
const jobStations = computed(() => stationsStore.list.filter((s) => s.id && s.id !== 'loumian'))

async function handleSaveWebhook() {
  try {
    await saveWebhook()
    flash('Webhook 已保存', 'success')
  } catch (e) { flash(e.message, 'error') }
}
async function handleDeleteWebhook(id) {
  if (!window.confirm('确定删除该 webhook？')) return
  try {
    await deleteWebhook(id)
    flash('Webhook 已删除', 'success')
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
  const pushType = selectedJob.value?.push_type || 'sales_report_text'
  const label = pushType === 'data_quality_alert' ? '数据质量摘要' : '销售报表'
  if (!window.confirm(`确定立即发送当前预览对应的${label}？`)) return
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
            <div style="display:flex;gap:8px">
              <button class="btn btn-primary" type="submit">{{ isEditingWebhook ? '保存修改' : '新增地址' }}</button>
              <button class="btn" type="button" @click="resetWebhookForm">清空 / 新增</button>
            </div>
          </form>

          <div style="display:flex;flex-direction:column;gap:8px;margin-top:12px">
            <div v-if="!webhooks.length" class="empty-state">暂无 webhook</div>
            <div v-for="item in webhooks" :key="item.id" class="card" style="padding:10px">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                <strong style="font-size:13px">{{ item.name }}</strong>
                <span class="badge" :style="item.enabled ? 'color:var(--green);border-color:var(--green)' : ''">{{ item.enabled ? '启用' : '停用' }}</span>
              </div>
              <div style="color:var(--text-dim);font-size:12px;word-break:break-all">{{ item.webhook_url_masked }}</div>
              <div v-if="item.notes" style="color:var(--text-dim);font-size:12px">{{ item.notes }}</div>
              <div style="display:flex;gap:6px;margin-top:8px">
                <button class="btn btn-sm" @click="editWebhook(item)">编辑</button>
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
              <button class="btn btn-sm" style="background:var(--green);border-color:var(--green);color:#fff" @click="handleSend">立即发送</button>
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
  .grid { grid-template-columns: 1fr !important; }
}
</style>
