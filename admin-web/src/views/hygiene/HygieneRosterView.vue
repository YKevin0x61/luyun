<script setup>
import { nextTick, onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import {
  HYGIENE_PERMISSIONS,
  HYGIENE_SHIFTS,
  hygienePermissionLabel,
  hygieneShiftLabel,
  rosterStatusLabel,
} from '../../utils/hygieneCopy'
import { STAFF_ENTRY_PATH } from '../../utils/staffPaths'

// 排班那条班次挂在卫生的哪一档日常检查（`staff_shifts.duty_slot`）→ 卫生认的班次名。
// 只认这一列、不认名字：排班的班次是**数据**（店长能改名、能加第三个），把「夜班」改成
// 「晚班」，这一档仍然是夜班档；责任区正是按这两个开关建的（`hygiene_zones.day_shift` /
// `night_shift`），所以责任区候选要按它筛。
const DUTY_SLOT_SHIFTS = { day: '白班', night: '夜班' }

const employees = ref([])
const zones = ref([])
// 排班的班次表：只出还在用的（停用的班次服务端会拒，不摆一个点了必然报错的选项）。
const scheduleShifts = ref([])
// 今天的**营业日**（06:00 切，北京时）。取服务端算好的那一份，页面不自己 `new Date()` 拼 ——
// 管理机的时区可能不在东八区，而「过去的日子改不了」是服务端按营业日判的。
const today = ref('')
// 排班那份读不出来时的一句话，跟 `errorText` 分开：花名册读得好好的，不该因为排班读挂了
// 就把整张员工表换成「加载失败」。
const scheduleError = ref('')
const loading = ref(true)
const errorText = ref('')
const drafts = ref({})
const busyId = ref(null)
const disableTarget = ref(null)

// 员工入口：全仓只有导航栏指向 /hygiene/roster，没人知道店员该扫哪个地址。这里把
// 绝对 URL 和二维码一起摆出来，新店员不用管理员口述。
// 路径取 `staffPaths.js` 的 `STAFF_ENTRY_PATH`（员工端入口 = 今天页），不在这里写死 ——
// 票 04 把员工首页搬到 `/staff/*` 时，这里硬编码的 `/hygiene` 漏改，二维码扫出来是死路径。
const staffEntryUrl = ref('')
const entryCopied = ref(false)
const qrCanvas = ref(null)

async function renderStaffEntry() {
  // 用当前 origin：门店可能是内网 IP、也可能是域名，写死哪个都会有一半人打不开。
  staffEntryUrl.value = `${window.location.origin}${STAFF_ENTRY_PATH}`
  entryCopied.value = false
  await nextTick()
  if (!qrCanvas.value) return
  try {
    await QRCode.toCanvas(qrCanvas.value, staffEntryUrl.value, { width: 148, margin: 1 })
  } catch {
    // 画不出二维码不影响复制链接，静默降级（下面那行 URL 仍然可读）。
  }
}

async function copyStaffEntry() {
  try {
    await navigator.clipboard.writeText(staffEntryUrl.value)
    entryCopied.value = true
  } catch {
    // 内网明文 http 不是安全上下文，没有 clipboard API：URL 就在旁边，手动抄。
    entryCopied.value = false
  }
}

function draftFor(row) {
  return drafts.value[row.id]
}

async function loadRoster() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/hygiene/admin/roster')
    employees.value = data.employees || []
    zones.value = data.zones || []
    const next = {}
    for (const row of employees.value) {
      next[row.id] = {
        name: row.name || '',
        job_title: row.job_title || '',
        permission: row.permission,
        // 「改今天」写的是排班的**单日覆盖**，草稿里放的是排班班次 id（或 'rest' = 那天休），
        // 不是写死的「白班/夜班」。
        shift: defaultShiftIdFor(row),
        // 责任区：'' = 「跟这个班次的固定区」（提交时发 `zone_id: null`）—— 不再替管理员
        // 挑第一个区，那种默认会把「他今天本来在哪个区」悄悄改掉。
        zone_id: row.zone_id === null || row.zone_id === undefined ? '' : row.zone_id,
      }
    }
    drafts.value = next
  } catch (err) {
    errorText.value = err.message || '无法加载花名册'
  } finally {
    loading.value = false
  }
}

// `/calendar` 的 `today` 就是营业日（`SchedulingCalendarView` 同一口径）。这里只看它那一个
// 字段：月份是这条接口的必填参数，`today` 跟要哪个月无关，所以月按本机时间取就够了
// （**日期**不这么取，那个必须用服务端的 `today`）。
function currentMonthValue() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

// 今天每个人排到哪个班（`{employee_id: 班次 id 字符串 | 'rest'}`）。
//
// 花名册那一行只给**卫生的档位**（白班/夜班），而排班同一档可能有好几条班次
//（白班档既有「早班」也有「白班」）—— 按档位配对会配错人。当天名单是权威，照它预填。
const dutyToday = ref({})

function dutyMapFromDay(day) {
  const map = {}
  for (const group of (day && day.groups) || []) {
    for (const person of group.people || []) map[person.id] = String(group.shift.id)
  }
  for (const person of (day && day.off_people) || []) map[person.id] = 'rest'
  return map
}

// 排班那份：今天的营业日 + 还在用的班次表 + 今天的当天名单。跟花名册各读各的 —— 它读挂了
// 只收掉「改今天」这条路（预填退回按档位配对），批准/停用/改名这些活照旧。
async function loadSchedule() {
  try {
    const [calendar, shiftData] = await Promise.all([
      api.get('/api/scheduling/calendar', { month: currentMonthValue() }),
      api.get('/api/scheduling/shifts'),
    ])
    today.value = (calendar && calendar.today) || ''
    scheduleShifts.value = (shiftData && shiftData.shifts) || []
    scheduleError.value = ''
    // 当天名单要拿 `today` 当参数，所以只能等上面那一步回来（换一个 RTT 换一次准确预填）。
    dutyToday.value = today.value
      ? dutyMapFromDay(await api.get('/api/scheduling/day', { date: today.value }))
      : {}
  } catch (err) {
    today.value = ''
    scheduleShifts.value = []
    dutyToday.value = {}
    scheduleError.value = err.message || '排班读不出来，今天暂时改不了'
  }
}

// 「先拿排班那份，再读花名册」：预填要用班次表，反过来的话第一次渲染的预填必然是空的。
async function loadAll() {
  await loadSchedule()
  await loadRoster()
}

function zonesForShift(shift) {
  return zones.value.filter((zone) =>
    (zone.shifts || HYGIENE_SHIFTS).includes(shift),
  )
}

// 这条班次算卫生的哪一档；'' = 没标档位（或选了「休」）：跟卫生的日常检查没有对应关系。
function dutyShiftOf(shiftId) {
  const shift = scheduleShifts.value.find((item) => String(item.id) === String(shiftId))
  return (shift && DUTY_SLOT_SHIFTS[shift.duty_slot]) || ''
}

// 选中的班次能去哪些区：标了档位就把它翻成「白班/夜班」再喂给 `zonesForShift`（那套
// `day_shift` / `night_shift` 开关）。没标档位的班次不筛 —— 筛只会把区全滤掉，而那种
// 班次本来就「跟卫生日常无关」，全列出来让管理员自己定。
function zoneChoicesForShift(shiftId) {
  const slot = dutyShiftOf(shiftId)
  return slot ? zonesForShift(slot) : zones.value
}

// 预填：**先问今天那份当天名单**（`dutyToday`），他今天在哪个班就填哪个班 ——
// 花名册那一行只给卫生的档位，同一档有多条班次时按档位配对会配错人。
//
// 名单里没有他（今天还没铺到、或者名单读挂了）才退回按档位配对：同类班次里挑第一条。
// 那样挑不到就留空，逼管理员自己选一条 —— 免得「什么都没动就点了提交」把班次悄悄换掉。
function defaultShiftIdFor(row) {
  const exact = dutyToday.value[row.id]
  if (exact) return exact
  const slot = Object.keys(DUTY_SLOT_SHIFTS).find((key) => DUTY_SLOT_SHIFTS[key] === row.shift)
  const match = slot && scheduleShifts.value.find((item) => item.duty_slot === slot)
  return match ? String(match.id) : ''
}

// 换班次 → 能去的区跟着换：原来那个区不在新班次的名单里就落回「跟固定区」，由排班那边
// 按「这个人 × 这个班次」的固定区填。
function onShiftChange(row) {
  const draft = drafts.value[row.id]
  if (!draft) return
  const allowed = zoneChoicesForShift(draft.shift)
  if (!allowed.some((zone) => String(zone.id) === String(draft.zone_id))) {
    draft.zone_id = ''
  }
}

onMounted(loadAll)
onMounted(renderStaffEntry)

useHygieneRealtime({
  id: 'hygiene-admin-roster',
  resources: ['roster', 'assignment'],
  pull: loadRoster,
})

async function approve(row) {
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/roster/${row.id}/approve`)
    await loadRoster()
  } catch (err) {
    errorText.value = err.message || '批准失败'
  } finally {
    busyId.value = null
  }
}

function askDisable(row) {
  disableTarget.value = row
}

async function confirmDisable() {
  const row = disableTarget.value
  disableTarget.value = null
  if (!row) return
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/roster/${row.id}/disable`)
    await loadRoster()
  } catch (err) {
    errorText.value = err.message || '停用失败'
  } finally {
    busyId.value = null
  }
}

async function enable(row) {
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/roster/${row.id}/enable`)
    await loadRoster()
  } catch (err) {
    errorText.value = err.message || '启用失败'
  } finally {
    busyId.value = null
  }
}

async function saveRow(row) {
  const draft = draftFor(row)
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.patch(`/api/hygiene/admin/roster/${row.id}`, {
      name: draft.name,
      job_title: draft.job_title,
      permission: draft.permission,
    })
    await loadRoster()
  } catch (err) {
    errorText.value = err.message || '保存失败'
  } finally {
    busyId.value = null
  }
}

// 「改今天」：写一条排班的**单日覆盖**。卫生原来那条改派接口（
// `POST /api/hygiene/admin/roster/{id}/assignment`）票 10 起固定 403 ——
// 今天上哪个班、在哪个区由排班决定，而卫生不 import 排班，所以改派直接写排班那边。
// 覆盖是**整天的快照**：班次和责任区一起定下来，`zone_id: null` = 跟这个班次的固定区。
async function changeAssignment(row) {
  const draft = draftFor(row)
  if (!draft || !draft.shift || !today.value) return
  busyId.value = row.id
  errorText.value = ''
  try {
    const payload = draft.shift === 'rest'
      // 「那天休」：班次与责任区都得留空（带细节的休会被服务端拦下来）。
      ? { is_rest: true, shift_id: null, zone_id: null }
      : {
          is_rest: false,
          shift_id: Number(draft.shift),
          zone_id: draft.zone_id === '' ? null : Number(draft.zone_id),
        }
    await api.put(`/api/scheduling/overrides/${row.id}/${today.value}`, payload)
    await loadRoster()
  } catch (err) {
    // 服务端把「为什么改不了」说全了（已经过去的日子、这天还没排到、班次停用…），原话转给
    // 管理员，别拿一句「改派失败」盖掉。
    errorText.value = err.message || '改今天失败'
  } finally {
    busyId.value = null
  }
}

// 「撤销回规则」：撤掉今天的覆盖，今天就回到排班规则铺出来的样子（服务端按**现在的规则**
// 重算）。今天没有覆盖时它是个空操作（服务端只删记录再重算），所以不必先判断有没有 ——
// 花名册这一份看不到「今天是不是覆盖写的」（只有排班当天卡里的 `overridden` 分得开）。
async function undoAssignment(row) {
  if (!today.value) return
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.delete(`/api/scheduling/overrides/${row.id}/${today.value}`)
    await loadRoster()
  } catch (err) {
    errorText.value = err.message || '撤销失败'
  } finally {
    busyId.value = null
  }
}
</script>

<template>
  <div class="roster-page">
    <div class="card roster-head">
      <div>
        <p class="hy-eyebrow">Roster · 人员名册</p>
        <h1>卫生花名册</h1>
        <p>批准注册、维护姓名职位，并设置卫生权限。</p>
        <details class="rule-help">
          <summary>规则说明</summary>
          <p>今天上哪个班、在哪个区由排班决定。这里的「改今天」写的是排班的单日覆盖：只改今天这一天，不动排班规则（以后怎么排去「排班」页改规则）。停用后不能登录，但花名册记录保留，可重新启用。超级管理员是后台共享账号，不能从花名册提升。</p>
        </details>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="loadAll">刷新</button>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>

    <div class="table-card">
      <div class="table-card-header">
        <h3>员工入口</h3>
      </div>
      <p class="editor-lead">店员用手机扫这个码进卫生系统，登录用手机号 + 密码。链接也可以直接在手机浏览器里打开。</p>
      <div class="staff-entry">
        <canvas ref="qrCanvas" class="staff-entry-qr" role="img" aria-label="员工入口二维码"></canvas>
        <div class="staff-entry-copy">
          <code>{{ staffEntryUrl }}</code>
          <button type="button" class="btn" @click="copyStaffEntry">
            {{ entryCopied ? '已复制' : '复制链接' }}
          </button>
        </div>
      </div>
    </div>

    <div class="table-card">
      <div class="table-card-header">
        <h3>员工 <span>{{ employees.length }}</span></h3>
      </div>
      <div v-if="loading" class="roster-empty">正在加载…</div>
      <div v-else-if="errorText" class="roster-empty">加载失败，点上方「刷新」重试。</div>
      <div v-else-if="!employees.length" class="roster-empty">还没有人注册。</div>
      <div v-else class="hy-person-list">
        <article v-for="row in employees" :key="row.id" class="hy-person">
          <div class="hy-person-top">
            <div class="roster-person-copy">
              <strong>{{ row.name || '未设置姓名' }}</strong>
              <span>{{ row.phone }}</span>
            </div>
            <span class="roster-status" :data-status="rosterStatusLabel(row)">
              {{ rosterStatusLabel(row) }}
            </span>
          </div>
          <div v-if="drafts[row.id]" class="hy-person-fields">
            <label>
              姓名
              <input
                v-model="drafts[row.id].name"
                class="input"
                type="text"
                maxlength="40"
                :disabled="busyId === row.id"
                placeholder="请输入真实姓名"
              >
            </label>
            <label>
              职位
              <input
                v-model="drafts[row.id].job_title"
                class="input"
                type="text"
                maxlength="40"
                :disabled="busyId === row.id"
                placeholder="头衔，比如领班"
              >
            </label>
            <label>
              卫生权限
              <select
                v-model="drafts[row.id].permission"
                class="select"
                :disabled="busyId === row.id"
              >
                <option v-for="perm in HYGIENE_PERMISSIONS" :key="perm" :value="perm">
                  {{ hygienePermissionLabel(perm) }}
                </option>
              </select>
            </label>
            <label>
              当天班次 · 现在 {{ hygieneShiftLabel(row.shift) }}
              <select
                v-model="drafts[row.id].shift"
                class="select"
                :disabled="busyId === row.id || !today"
                @change="onShiftChange(row)"
              >
                <option value="" disabled>选排班的班次</option>
                <option value="rest">休（这天不上班）</option>
                <option v-for="shift in scheduleShifts" :key="shift.id" :value="String(shift.id)">
                  {{ shift.name }}
                </option>
              </select>
            </label>
            <label>
              当天区域 · 现在 {{ row.zone_name || '未选' }}
              <select
                v-model="drafts[row.id].zone_id"
                class="select"
                :disabled="busyId === row.id || drafts[row.id].shift === 'rest'"
              >
                <option value="">跟这个班的固定区</option>
                <option v-for="zone in zoneChoicesForShift(drafts[row.id].shift)" :key="zone.id" :value="zone.id">
                  {{ zone.name }}
                </option>
              </select>
            </label>
            <div class="roster-assign-actions">
              <button
                type="button"
                class="btn"
                :disabled="busyId === row.id || !today || !drafts[row.id].shift"
                @click="changeAssignment(row)"
              >改今天</button>
              <button
                type="button"
                class="btn"
                :disabled="busyId === row.id || !today"
                @click="undoAssignment(row)"
              >撤销回规则</button>
            </div>
            <p v-if="scheduleError" class="roster-assign-hint roster-assign-error">{{ scheduleError }}</p>
            <p v-else class="roster-assign-hint">
              「改今天」写的是排班今天的单日覆盖：只改这一天，以后改排班规则也不动它。这天没有覆盖时，「撤销回规则」什么都不改。
            </p>
          </div>
          <div class="hy-person-actions">
            <button
              v-if="!row.approved && !row.disabled"
              type="button"
              class="btn btn-primary"
              :disabled="busyId === row.id"
              @click="approve(row)"
            >批准</button>
            <button
              v-if="row.disabled"
              type="button"
              class="btn btn-primary"
              :disabled="busyId === row.id"
              @click="enable(row)"
            >启用</button>
            <button
              type="button"
              class="btn"
              :disabled="busyId === row.id"
              @click="saveRow(row)"
            >保存</button>
            <button
              v-if="!row.disabled"
              type="button"
              class="btn btn-danger"
              :disabled="busyId === row.id"
              @click="askDisable(row)"
            >停用</button>
          </div>
        </article>
      </div>
    </div>

    <ConfirmDialog
      v-if="disableTarget"
      title="停用员工"
      :message="`停用 ${disableTarget.name || disableTarget.phone} 后不能登录，花名册里仍能看到这个人。`"
      confirm-label="停用"
      danger
      @confirm="confirmDisable"
      @cancel="disableTarget = null"
    />
  </div>
</template>

<style scoped>
.roster-person-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

/* 「改今天 / 撤销回规则」是同一件事的两面：并排等宽，跟上面那些字段一个节奏。 */
.roster-assign-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: .5rem;
}
.roster-assign-actions .btn {
  width: 100%;
  min-height: 38px;
}
/* 这一行是「改的是哪一天」和读不出排班时的实话，跟着字段的说明字号走。 */
.roster-assign-hint {
  margin: 0;
  font-size: .74rem;
  line-height: 1.6;
  color: var(--hy-faint);
}
.roster-assign-error { color: var(--hy-seal-bright); }
</style>
