<script setup>
/**
 * 员工「今天」的卫生块 —— 从「我的今天」页（`views/today/TodayView.vue`）整块搬来
 * （2026-10-14 用户裁定）：这一块放在工作台首页「今天」（`/workbench`）上，而员工端
 * 的默认首页就是它。搬的是**整块**：今天的日常检查进度、逾期点、仪容仪表那一行
 * 与它的两步拍照链路、以及「仪容仪表 ›」「工作卫生 ›」两颗入口 —— 原页不再重复渲染
 * （同一件事两处报数，员工会拿它当两件事）。
 *
 * **为什么是组件而不是写进首页**：首页（`WorkbenchHomeView`）只做分流与摘要，四个请求
 * 各拉各的；这一块自带业务动作（两步拍照 + 上传队列 + 弹层），摊进首页会把那条
 * 「首页不做业务动作」的规矩搅掉。组件自包含：自己的数据、自己的弹层、自己的 nudge 订阅，
 * 首页员工视角里只是 `<StaffHygieneCard />` 一行；店长视角不渲染它，也就一条请求都不发。
 *
 * 数据三条，都只认员工自己的 cookie（接口上没有 `employee_id` 可填）：
 *  - `GET /api/hygiene/staff/me` —— 当天的班次（`shift`）、当日工作区（`zone_name`）
 *    与日常的钟点（`daily_clocks`）；
 *  - `GET /api/hygiene/staff/daily-work` —— 今天要交的日常项；
 *  - `GET /api/hygiene/staff/attire` —— 仪容仪表今天的状态（按**人**拍：今天排到班次
 *    才有 `required`，休假与没排到的连那一行都不显示）。
 * 三条**同一个 try**：一块读不出来就整块变成「读不出来 + 重试」（弱网下不必等三轮）。
 *
 * 文案、分支与色调逐字沿用原块（那里的注释还在 TodayView 的历史里）：日常交齐 / 还差 N 项 /
 * 过了钟点，仪容仪表的五种状态各自一色。改这里等于改员工每天看的那句话。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import HygieneLiveCamera from '../hygiene/HygieneLiveCamera.vue'
import HygieneStandardOverlay from '../hygiene/HygieneStandardOverlay.vue'
import { useNudgePull } from '../../composables/useNudgePull'
import { useImageUploadQueueStore } from '../../stores/imageUploadQueue'
import { staffRequest } from '../../utils/hygieneStaff'
import { buildWorkQueue, dailyProgress, shiftClock } from '../../utils/hygieneWorkFlow'
import { loginRedirectTarget } from '../../utils/loginNext'
import { dayLabel } from '../../utils/todayShift'

const router = useRouter()

const state = ref('loading') // loading | ready | error
const error = ref('')
const items = ref([])
const clocks = ref(null)
const shift = ref(null)
const zoneName = ref('')
// 逾期是「现在几点」跟本班钟点比出来的：别在 computed 里反复取时间（那样每次渲染结果都
// 可能不一样），但也不能只在加载那一刻取一次 —— 那样过点之后灯和那句「已经过了本班的
// 钟点」要等下一次 nudge 才变红，员工盯着屏幕也看不到它自己变色。30 秒 tick 一次，
// 口径照 `HygieneHomeView` 的 `tickClock`（量化到分钟，秒级抖动不会白重算）。
const now = ref(0)
let clockTimer = null

function tickClock() {
  now.value = Math.floor(Date.now() / 60_000) * 60_000
}

function startClock() {
  if (clockTimer) window.clearInterval(clockTimer)
  tickClock()
  clockTimer = window.setInterval(tickClock, 30_000)
}

function stopClock() {
  if (clockTimer) window.clearInterval(clockTimer)
  clockTimer = null
}

// 仪容仪表（票 12）：按**人**拍，今天排到班次才要拍 —— 休假的与没排到的连这一行都不显示
// （`required` 由排班给，不是「谁有账号」）。它不属于工作区那三类日常，所以是这块里
// 单独的一行，**不进** `status` 的分子分母。
const attire = ref({
  state: 'loading', // loading | ready | error
  error: '',
  businessDate: '',
  required: false,
  status: 'todo', // todo | pending | passed | rejected
  note: '',
  hasStandard: false,
})

// 拍照是两步（ADR 0050：标准图那一屏与取景器**不能同屏**，也不做分屏）。所以这里存的是
// 「现在哪一屏」而不是「弹层开没开」：standard → camera 是换屏，不是叠上去。
const attireSheet = ref('') // '' | 'standard' | 'camera'
const imageUploads = useImageUploadQueueStore()
// 「这张正在传」从上传队列派生，不用本组件的 ref：刷新页面 / 回收 webview 之后草稿会恢复
// 继续传（跟卫生那一屏同一条口径），局部状态会让员工对着正在传的那张再拍一遍。
const attireKey = computed(() => `attire:${attire.value.businessDate}`)
const attirePending = computed(() => imageUploads.activeTasks.some(
  (task) => task.pendingKey === attireKey.value,
))

// 这一行的色调：没标准图（做不了，得找店长）= 琥珀，被驳回（要重拍）= 珊瑚，
// 待验收 = 中性，其余（没拍 / 已通过）= 玉色。分支顺序与模板里那串 v-if 一致 ——
// 「被驳回」先于「没标准图」，两边判据不一致的话颜色和文案会对不上。
const attireTone = computed(() => {
  if (attire.value.status === 'rejected') return 'rejected'
  if (!attire.value.hasStandard) return 'nostandard'
  if (attire.value.status === 'pending') return 'pending'
  return ''
})

const status = computed(() => dailyProgress(items.value))
const due = computed(() => shiftClock(shift.value, clocks.value))
const overdue = computed(() => buildWorkQueue({
  inbox: items.value,
  shiftDue: due.value,
  now: now.value,
}).filter((task) => task.bucket === 'overdue').length)

// 三行进度（B 案 v3）：日常 / 专项 / 整改。日常本来就在这一块里；专项与整改各拉一条
// 员工自己的门（跟「卫生待办」页同一口径）。**这两条各自失败只让那一行显示「—」**，
// 不把整卡打回错误态 —— 员工看得到日常，就不该被一条读不到的专项挡住。
const deepItems = ref(null)
const fixItems = ref(null)
const deepProgress = computed(() => (deepItems.value ? dailyProgress(deepItems.value) : null))
const fixProgress = computed(() => (fixItems.value ? dailyProgress(fixItems.value) : null))
/** 三块没通过的合计（「还差 N 项」只报这一处，不再各块各报一遍）。 */
const remainingTotal = computed(() => (
  status.value.remaining
  + (deepProgress.value ? deepProgress.value.remaining : 0)
  + (fixProgress.value ? fixProgress.value.remaining : 0)
))
/** 行尾读数：「2/5」；那一块没读到时写「—」（与首页「—」同一条口径）。 */
function progressText(progress) {
  return progress ? `${progress.passed}/${progress.total}` : '—'
}
/** 三块加起来的应做项数：0 就是「今天没有要交的」，面板头不摆一个「已交齐」自欺。 */
const totalWork = computed(() => (
  status.value.total
  + (deepProgress.value ? deepProgress.value.total : 0)
  + (fixProgress.value ? fixProgress.value.total : 0)
))
/** 进度行末尾那句合计（面板头只报截止）：还差 N 项 / 已交齐。
 *  放在进度行里，是因为首页另有一格「我的待办 N」—— 同一件事在面板头再说一遍是重复。 */
const remainingText = computed(() => (
  remainingTotal.value ? `还差 ${remainingTotal.value} 项` : '已交齐'
))
async function loadExtras() {
  const settle = (promise) => promise.then((data) => data.items || [], () => null)
  const [deep, fix] = await Promise.all([
    settle(staffRequest('/api/hygiene/staff/deep-clean')),
    settle(staffRequest('/api/hygiene/staff/fix')),
  ])
  deepItems.value = deep
  fixItems.value = fix
}

// B 案 v3 的 LED（只由上面这几份已有数据派生，不新增判据）：
// 面板头的灯 = 这块整体「要不要动手」（逾期坏 / 还差提醒 / 空或全齐中性/好）；
// 仪容仪表那一行的灯跟它自己的三种语气一一对应（ADR 0050 的分支不动）。
const shcLed = computed(() => {
  if (state.value === 'error') return 'bad'
  if (state.value !== 'ready') return 'idle'
  if (overdue.value > 0) return 'bad'
  if (status.value.remaining > 0) return 'warn'
  return status.value.total > 0 ? 'ok' : 'idle'
})
const attireLed = computed(() => {
  if (attire.value.status === 'rejected') return 'bad'
  if (attire.value.status === 'pending' || !attire.value.hasStandard) return 'warn'
  return 'ok'
})

/** 会话没了（换了手机、店里停了账号、cookie 过期）：回员工登录页，回来还是这一页。
 *  与「我的今天」页同一个走法（`loginRedirectTarget` 一处算落点）：几个并发请求会各拿一个
 *  401 各自调到这里，**已经在登录页上时它返回 null** —— 不返回的话第二个 401 会把
 *  「已经是登录页的当前地址」再包一层，`?next=` 里的目标当场作废。 */
function leaveForStaffLogin() {
  const target = loginRedirectTarget(router.currentRoute.value)
  if (target) router.replace(target)
}

/** 读今天这三条。`quiet`（实时 nudge 触发的重读）不打回 loading —— 会闪一下「正在读…」。 */
async function load(quiet = false) {
  if (!quiet) {
    state.value = 'loading'
    error.value = ''
  }
  try {
    const [me, work, attireShot] = await Promise.all([
      staffRequest('/api/hygiene/staff/me'),
      staffRequest('/api/hygiene/staff/daily-work'),
      staffRequest('/api/hygiene/staff/attire'),
    ])
    const employee = me.employee || {}
    const shot = attireShot.attire || {}
    attire.value = {
      state: 'ready',
      error: '',
      businessDate: shot.business_date || '',
      required: Boolean(shot.required),
      status: shot.status || 'todo',
      note: shot.note || '',
      hasStandard: Boolean(shot.has_standard),
    }
    items.value = work.items || []
    clocks.value = me.daily_clocks || null
    shift.value = employee.shift || null
    zoneName.value = employee.zone_name || ''
    tickClock()
    state.value = 'ready'
    void loadExtras()
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    error.value = err.message || '卫生待办读不出来'
    attire.value = { ...attire.value, state: 'error', error: error.value }
    state.value = 'error'
  }
}

function openAttireStandard() {
  attireSheet.value = 'standard'
}

function openAttireCamera() {
  // 相机只能在有标准图时开：员工要照着它拍（没传标准图时服务端也会拒）。
  if (!attire.value.hasStandard) return
  attireSheet.value = 'camera'
}

/** 拍完就进上传队列（不是直接 fetch）：断网/地铁里拍的那张不丢，恢复后自己传。 */
function onAttireCaptured(blob) {
  attireSheet.value = ''
  try {
    const form = new FormData()
    form.append('file', blob, 'attire.jpg')
    form.append('live', 'true')
    imageUploads.enqueue({
      transport: 'staff',
      path: '/api/hygiene/staff/attire/submit',
      formData: form,
      label: '仪容仪表 · 现场自拍',
      detail: attire.value.businessDate ? dayLabel(attire.value.businessDate) : '',
      pendingKey: attireKey.value,
      onSuccess: () => load(true),
      onError: () => { attire.value = { ...attire.value, error: '刚才那张没传上去，可在上传列表里重试' } },
    })
  } catch (err) {
    attire.value = { ...attire.value, error: err.message || '无法加入上传队列' }
  }
}

onMounted(() => {
  startClock()
  void load()
})

// 30 秒 tick 的定时器必须跟着组件走：首页切走 / 换身份时这块会被卸载，留着就是一条
// 没人用的 interval。
onBeforeUnmount(stopClock)

// 实时（nudge + pull）：店长改了排班、自己交了一项 —— 这块跟着重读。nudge 不带数据，
// 收到就重取这三条轻量 GET。断线兜底由 `useConnectionFallback` 挂载时 pull 一次。
useNudgePull({
  id: 'staff-hygiene-card',
  topics: ['hygiene', 'scheduling'],
  pull: () => { load(true); void loadExtras() },
})
</script>

<template>
  <section class="shc">
    <!-- 面板头（B 案 v3）：6px LED + 标题 + 右栏当天工作区。 -->
    <div class="shc-hd">
      <i class="led" :class="shcLed" aria-hidden="true"></i>
      <h2>今天要交</h2>
      <!-- 面板头只报截止：合计已经在三行进度里报过，首页另有一格「我的待办 N」——
           同一件事不再各写一遍。 -->
      <em v-if="state === 'ready' && due && totalWork" class="shc-due">{{ due }} 前交</em>
    </div>

    <p v-if="state === 'loading'" class="shc-sub">正在读今天的日常…</p>

    <template v-else-if="state === 'error'">
      <p class="shc-sub">{{ error }}</p>
      <div class="acts">
        <button class="btn" type="button" @click="load()">重试</button>
      </div>
    </template>

    <div v-else class="shc-body">
      <!-- 三行进度（B 案 v3）：日常 / 专项 / 整改，一行一件事；
           合计（还差 N 项 / 已交齐）并进首行末尾，首行是主 CTA「拍摄」，其余行尾「去」
           （与卫生页队列上那颗「拍摄」同名，同一件事不换三种叫法）。 -->
      <ul class="shc-progress">
        <li v-if="status.total" class="shc-prog">
          <i class="led" :class="status.remaining ? 'warn' : 'ok'" aria-hidden="true"></i>
          <span class="shc-prog-label">日常 </span>
          <b class="shc-prog-num">{{ progressText(status) }}</b>
          <span v-if="totalWork" class="shc-prog-remain">{{ remainingText }}</span>
          <button
            class="shc-go is-primary"
            type="button"
            @click="router.push({ path: '/workbench/staff/clean', query: { shoot: 'next' } })"
          >
            拍摄
          </button>
        </li>
        <li v-if="!deepProgress || deepProgress.total" class="shc-prog">
          <i class="led" :class="deepProgress && deepProgress.remaining ? 'warn' : 'ok'" aria-hidden="true"></i>
          <span class="shc-prog-label">专项 </span>
          <b class="shc-prog-num">{{ progressText(deepProgress) }}</b>
          <button
            class="shc-go"
            type="button"
            @click="router.push({ path: '/workbench/staff/clean', query: { group: 'deep' } })"
          >去 ›</button>
        </li>
        <li v-if="!fixProgress || fixProgress.total" class="shc-prog">
          <i class="led" :class="fixProgress && fixProgress.remaining ? 'warn' : 'ok'" aria-hidden="true"></i>
          <span class="shc-prog-label">整改 </span>
          <b class="shc-prog-num">{{ progressText(fixProgress) }}</b>
          <button
            class="shc-go"
            type="button"
            @click="router.push({ path: '/workbench/staff/clean', query: { group: 'fix' } })"
          >去 ›</button>
        </li>
      </ul>

      <p v-if="!status.total" class="shc-sub">
        <template v-if="shift">今天没有要交的日常检查。</template>
        <!-- 没班次就没有可交的那一份（班次由排班决定）——说清该找谁，别让人在这儿等。 -->
        <template v-else>排班还没给你班次，今天没有可交的日常检查；找超级管理员配一下规则。</template>
      </p>
      <p v-if="overdue" class="shc-err" role="status">
        已经过了本班的钟点，还有 {{ overdue }} 项没交。
      </p>

      <!-- 仪容仪表那一行（按人拍，排到班次才出现）：状态 LED + 一行字。 -->
      <div v-if="attire.required" class="shc-rows">
        <div class="shc-row">
          <i class="led" :class="attireLed" aria-hidden="true"></i>
          <p class="shc-attire" :class="attireTone">
            仪容仪表：<template v-if="attirePending">正在传…</template><template
              v-else-if="attire.status === 'passed'"
            >已通过</template><template
              v-else-if="attire.status === 'pending'"
            >已交，等店长验收</template><template
              v-else-if="attire.status === 'rejected'"
            >被驳回，要重拍</template><template
              v-else-if="!attire.hasStandard"
            >超级管理员还没传标准图</template><template v-else>今天还没拍</template>
          </p>
        </div>
        <p v-if="attire.note" class="shc-err" role="status">店长说：{{ attire.note }}</p>
        <p v-if="attire.error" class="shc-err" role="status">{{ attire.error }}</p>
      </div>

      <!-- 两颗动作并排在一行（省一屏高度）：左边是「仪容仪表」（两步拍照入口），
           右边是「工作卫生」。已经通过的不给重拍入口（服务端也会拒 `already_accepted`）：
           验收过的是记录，员工端就不该摆一个按了必然失败的按钮。 -->
      <div class="acts">
        <button
          v-if="attire.required && attire.hasStandard && attire.status !== 'passed'"
          class="btn"
          type="button"
          :disabled="attirePending"
          @click="openAttireStandard"
        >
          {{ attire.status === 'todo' ? '仪容仪表 ›' : '重拍一张 ›' }}
        </button>
        <button class="btn" type="button" @click="router.push('/workbench/staff/clean')">
          工作卫生 ›
        </button>
      </div>
    </div>
  </section>

  <!-- 仪容仪表的拍照弹层（票 12）：两步 —— 先看标准图、再开相机。**不同屏**是 ADR 0050
       的硬规则：标准图与取景器并排，人会照着「上一张」摆姿势而不是看镜头。 -->
  <div
    v-if="attireSheet"
    class="modal-overlay"
    role="dialog"
    aria-modal="true"
    aria-labelledby="attire-sheet-title"
    @click.self="attireSheet = ''"
  >
    <div class="modal-box">
      <div class="modal-header">
        <h3 id="attire-sheet-title">仪容仪表</h3>
        <button class="btn" type="button" @click="attireSheet = ''">关闭</button>
      </div>
      <template v-if="attireSheet === 'standard'">
        <p class="shc-sub">对着标准图看清再拍。这一屏故意不放取景框。</p>
        <HygieneStandardOverlay
          src="/api/hygiene/staff/attire/standard"
          alt="仪容仪表标准图"
        />
        <button class="btn" type="button" @click="openAttireCamera">打开相机</button>
      </template>
      <template v-else-if="attireSheet === 'camera'">
        <p class="shc-sub">拍现在的样子。相册里的旧照片不算，必须现场拍。</p>
        <HygieneLiveCamera @captured="onAttireCaptured" />
      </template>
    </div>
  </div>
</template>

<style scoped>
/* B 案 v3「三行进度」（2026-10-17 版式审查后）：这一块挂在首页/今天页的员工视角上。
   骨架 = 面板头（6px LED + 标题 + 本班截止）→ 三行进度（日常 / 专项 / 整改；
   首行主 CTA「拍摄」，其余「去」；合计「还差 N 项」并进首行末尾）→ 仪容仪表行 → 动作行。
   全部走语义令牌；卡片 = 1px 线 + --hy-shadow-sm，没有渐变光晕。 */
.shc {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  overflow: hidden;
  color: var(--ink);
  font-family: var(--font-sans);
  padding: 0 0 14px;
}

.shc-hd {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 11px 14px;
  border-bottom: 1px solid var(--line);
}
.shc-hd h2 {
  margin: 0;
  font: 600 12.5px/1 var(--font-sans);
  letter-spacing: .06em;
  color: var(--ink);
}
.shc-hd em {
  margin-left: auto;
  font-style: normal;
  font-family: var(--font-mono), var(--font-sans);
  font-size: 9.5px;
  color: var(--ink-faint);
}

/* 状态灯：6px（ok / warn / bad / sig / idle）。 */
.led {
  width: 6px;
  height: 6px;
  flex: none;
  display: inline-block;
  border-radius: 50%;
  background: var(--ink-faint);
}
.led.ok { background: var(--ok); }
.led.warn { background: var(--warn); }
.led.bad { background: var(--danger); }
.led.sig { background: var(--action); }
.led.idle { background: var(--ink-faint); }

.shc-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 8px 14px 0;
}

/* 三行进度：一行一件事，行高 56px、动作贴行尾。 */
.shc-progress {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.shc-prog {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 56px;
  border-top: 1px solid var(--wb-ink-a08);
}
.shc-prog:first-child { border-top: 0; }
.shc-prog-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.shc-prog-num {
  margin-left: auto;
  font-family: var(--font-mono), var(--font-sans);
  font-size: 16px;
  font-weight: 500;
  color: var(--ink);
}
/* 合计并进首行末尾（面板头只报截止）：与 16px 读数同一行，用 mono 小字。 */
.shc-prog-remain {
  margin-left: 6px;
  font-family: var(--font-mono), var(--font-sans);
  font-size: 9.5px;
  color: var(--ink-faint);
  white-space: nowrap;
}

/* 行尾动作：主 CTA ≥48px 方角；其余行是次按钮。 */
.shc-go {
  flex: none;
  min-height: 48px;
  min-width: 64px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: transparent;
  color: var(--ink);
  font-family: var(--font-sans);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.shc-go.is-primary {
  background: var(--action);
  border-color: var(--action);
  color: var(--action-ink);
}

/* 读不出来 / 重试两态直接挂在面板里（没有 .shc-body 包一层）。 */
.shc > .shc-sub,
.shc > .acts {
  margin: 12px 14px 0;
}

.shc-sub {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--ink-dim);
}

/* 行列表：一行 = LED + 一行字（仪容仪表这条按人拍）。 */
.shc-rows {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--line);
  padding-top: 4px;
}
.shc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 8px 0;
}
.shc-attire {
  margin: 0;
  font-family: var(--font-sans);
  font-size: 13px;
  line-height: 1.45;
  color: var(--ok);
}
.shc-attire.pending { color: var(--warn); }
.shc-attire.rejected { color: var(--danger-bright); }
.shc-attire.nostandard { color: var(--warn); }

.shc-err {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--danger-bright);
}

.acts {
  display: flex;
  gap: 8px;
}
.acts .btn {
  flex: 1;
}

/* 卡内按钮：≥48px、方角、描边式。全局 `.btn` 有一条 40px 的 min-height 基线
   （!important），这里同权重抬回 48。弹层里那几颗保持默认（撑大反而挤）。 */
.shc .btn {
  min-height: 48px !important;
  font-size: .95rem;
  border-radius: var(--r-1);
  background: transparent;
  border: 1px solid var(--line-strong);
  color: var(--ink);
}
</style>
