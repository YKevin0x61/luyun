<script setup>
/**
 * 工作台首页（票 06）：子应用根 `/workbench` 那一页 —— 打开就知道今天该干嘛。
 *
 * 它只做**分流与摘要**：按此刻的身份（票 04 的 `stores/workbenchIdentity`）渲染两套视角，
 * 数字全部由**已有接口**在客户端聚合（口径在 `utils/workbenchHome.js`，那儿有逐条出处）。
 * 页面上没有任何业务动作：不批假、不验收、不开整改单 —— 那些仍然在各自专页里，
 * 这里每个数字只是一扇门（spec 故事 14 与「关键交互」的最后一条）。
 *
 * 两套视角互斥，且**探针回来之前一个数字都不渲染**（`view === null`）：身份没定之前
 * 谁也不知道这是谁，先渲染店长那三个数字再被换成员工视角，等于让人看一眼假的。
 *
 * 店长那一档打四个接口（今天谁上班 / 待批请假 / 待验收 / 逾期整改），四个请求并行、
 * **各拉各的**：一块读不出来只让那一格显示「—」，其余照常（跟「今天」页两块卡同一个
 * 口径）。任何一块失败都不改这一页的结构，也不把整页打回错误态。
 *
 * 员工那一档打五个（我的班 / 我的申请 / 日常 / 专项 / 整改）：班次与工作区复用「今天」页
 * 那套翻译（`utils/todayShift.js`），待办条数与「卫生待办」页的三个角标同一口径。
 * 那一档里还有一块**今天的活**（`components/staff/StaffHygieneCard.vue`）：它自带三条请求
 * （`/staff/me`、`/staff/daily-work`、`/staff/attire`）与自己的实时订阅 —— 首页只摆它一行，
 * 不把它的口径并进这套摘要里（那一块有业务动作，摘要没有）。
 * 401 的处理交给 `staffRequest` 之外的公共路径 —— 这里用的是 `api`（管理端 cookie），
 * 员工那几条走 `staffRequest`，各自与页面自己那次请求同一条链。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import StaffHygieneCard from '../../components/staff/StaffHygieneCard.vue'
import { useNudgePull } from '../../composables/useNudgePull'
import { pageTitle } from '../../router/pageRoutes.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { IDENTITY_ADMIN } from '../../utils/workbenchIdentity'
import { staffRequest } from '../../utils/hygieneStaff'
import { dayLabel } from '../../utils/todayShift'
import { WORKBENCH_TITLE, workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { HOME_LINKS, MANAGER_TODO_LIMIT, workbenchHomeSummary } from '../../utils/workbenchHome'
import { PREP_PLAN_PATH } from '../../utils/prepPlanPaths.js'
import { useStationsStore } from '../../stores/stations'

const identityStore = useWorkbenchIdentityStore()
const identity = computed(() => identityStore.identity)

// 四块数据的原始响应（`null` = 还没回来 / 读失败）。页面只把它们交给
// `workbenchHomeSummary`，自己不解释形状 —— 口径在 util 里，能拿真单测压。
const day = ref(null)
const inbox = ref(null)
const queue = ref(null)
const fix = ref(null)
// 人事提醒（第五格）：本月该调工龄奖 + 本月生日，两块都在这条响应里。
const reminders = ref(null)
// 备货计划：今天各档口还差几项（ADR 0117 把入口从「后勤」组提到首页这张卡片上）
const prep = ref(null)
const stationsStore = useStationsStore()
const deep = ref(null)
const daily = ref(null)
const me = ref(null)
// 业务日（服务端那个 06:00 切日的今天）：月历、员工端、卫生三处读的是同一天，首页
// 显示的日期也得是它 —— 手机上「今天」是几号不作数（票 05 已踩过一次时区）。
const businessDate = ref('')
const state = ref('loading') // loading | ready

/** 今天要摊的东西（店长 / 员工两套互斥的视角）。 */
const summary = computed(() => workbenchHomeSummary({
  identity: identity.value,
  day: day.value,
  inbox: inbox.value,
  queue: queue.value,
  fix: fix.value,
  reminders: reminders.value,
  deep: deep.value,
  daily: daily.value,
  me: me.value,
}))

/** 员工首页「我的班」右上角那颗状态（`staffShiftCell().tone` 的六种取值）。 */
const SHIFT_STATES = {
  shift: { text: '在岗', led: 'ok' },
  half: { text: '半天', led: 'warn' },
  leave: { text: '请假', led: 'sig' },
  rest: { text: '休', led: 'off' },
  moved: { text: '已调整', led: 'warn' },
  none: { text: '未排', led: 'off' },
}
// 「上班」那一态在 `todayShift.todayTone` 里用**空串**表示（页面默认色），不是 `shift`；
// 这里把它归一成 `shift`，否则上班的日子右上角会写「未排」、灯也是灰的。
function shiftState(tone) {
  return SHIFT_STATES[tone || 'shift'] || SHIFT_STATES.none
}

const QUICK_LINKS = [
  { label: '排班月历', to: '/workbench/hr/calendar' },
  { label: '备货计划', to: '/workbench/kitchen/prep-plan' },
  { label: '配方管理', to: '/workbench/kitchen/recipe/manage' },
  { label: '损耗分析', to: '/workbench/kitchen/loss' },
  { label: '花名册', to: '/workbench/hr/roster' },
  { label: '班次表', to: '/workbench/hr/shifts' },
]

const dateText = computed(() => dayLabel(businessDate.value))

/** 数字为空的三种表现，别混：
 *  `—` 那一块**读不出来**（不是 0，别把「没读到」说成「没有」）；`0` 是真的没有；
 *  其余照原样。 */
function numberText(value) {
  if (value == null) return '—'
  return String(value)
}

/** 几块并行、各拉各的：失败把那块留成 `null`，不抛出去影响别的块。 */
async function safe(promise, sink, pick = (data) => data) {
  try {
    sink.value = pick(await promise) ?? null
  } catch {
    sink.value = null
  }
}

async function loadManager() {
  // 备货卡片上的档口名要中文：档口表没拉过就先拉一次（读不到时退回 id，不影响其他块）。
  // 只挂在店长这一档 —— 员工会话调 `/api/stations` 是 401，没必要白打一次。
  void stationsStore.load().catch(() => {})
  // 先拿营业日：`daily-queue` 不带日期时回的就是服务端那个营业日（`date`），三处读的
  // 是同一天。**不能拿手机上的今天去问排班** —— 手机时区可能不在东八区，问出隔壁那天
  // 只会得到一句「这天没铺」，店长看到「今天没人上班」。
  await safe(api.get('/api/hygiene/admin/daily-queue'), queue)
  const date = (queue.value && queue.value.date) || ''
  await Promise.all([
    safe(api.get('/api/scheduling/day', date ? { date } : {}), day),
    safe(api.get('/api/scheduling/inbox'), inbox),
    safe(api.get('/api/hygiene/admin/fix'), fix),
    safe(api.get('/api/hygiene/admin/hr-reminders'), reminders),
    safe(api.get('/api/prep-plan/summary'), prep),
  ])
  businessDate.value = date
    || (day.value && day.value.business_date)
    || (inbox.value && inbox.value.today)
    || ''
}

/**
 * 员工「我的班」副行下面那两个小入口的落点：`/workbench/staff/today?act=leave|swap&date=<营业日>`。
 *
 * 为什么落「今天」页而不是直接写月历：员工登录后的默认落点就是「今天」，从首页点过来
 * 先经过它、再转进月历那一天的操作单（`TodayView` 里只做转发，不在这两页摆第二套表单）。
 * 日期取服务端营业日 `businessDate`（`/me` 的 `today`），不拿手机上的今天。
 */
function staffDayLink(act) {
  return {
    path: '/workbench/staff/today',
    query: { act, date: businessDate.value || '' },
  }
}

async function loadStaff() {
  // 先拿自己的班：营业日与「我的班次」都从它来，其余四块并行。
  await safe(staffRequest('/api/scheduling/me'), me)
  businessDate.value = (me.value && me.value.today) || ''
  await Promise.all([
    safe(staffRequest('/api/scheduling/me/requests'), inbox),
    safe(staffRequest('/api/hygiene/staff/daily-work'), daily),
    safe(staffRequest('/api/hygiene/staff/deep-clean'), deep),
    safe(staffRequest('/api/hygiene/staff/fix'), fix),
  ])
}

async function load() {
  if (!identity.value) return
  state.value = 'loading'
  if (identity.value === IDENTITY_ADMIN) await loadManager()
  else await loadStaff()
  state.value = 'ready'
}

onMounted(() => {
  document.title = workbenchDocumentTitle(pageTitle('/workbench'))
  void load()
})

// 切档时重取（顶栏那颗切换器改了身份，这一页的两个视角跟着换）。探针还没回来时
// `load()` 自己会跳过 —— 切档与探针都走这条，重复取一次也无妨（都是轻量 GET）。
watch(identity, () => { void load() })

// 实时（nudge + pull，票 10 那套）：店长改了排班、员工交了卫生、有人提了申请 ——
// 首页上那些数字跟着变。nudge 不带数据，收到就整页重取一次（四个轻量 GET）。
// `when` 那一层：身份还没探出来时不重取 —— 那会儿 `load()` 本来就什么都不做，
// 断线兜底（`useConnectionFallback`）在挂载时就会 pull 一次，不挡的话每次进页面都白跑。
useNudgePull({
  id: 'workbench-home',
  topics: ['scheduling', 'hygiene'],
  pull: () => { void load() },
  fallback: { when: () => Boolean(identity.value) },
})
</script>

<template>
  <div class="wbh">
    <header class="wbh-head">
      <div>
        <p class="wbh-kicker">{{ dateText }} · 营业日</p>
        <h1 class="wbh-title">今天</h1>
      </div>
      <div v-if="summary.view === IDENTITY_ADMIN && summary.duty" class="wbh-pills">
        <span class="wbh-pill"><i class="wbh-led ok"></i>上班 <b>{{ summary.duty.total }}</b></span>
        <span v-if="summary.leaves" class="wbh-pill is-warn"><i class="wbh-led warn"></i>待批 <b>{{ summary.leaves }}</b></span>
      </div>
    </header>

    <p v-if="!summary.view" class="wbh-wait">{{ state === 'loading' ? '正在确认身份…' : '身份还没定，稍后再看' }}</p>

    <!-- ── 店长：四个读数 + 今天的班 ─────────────────────────────────── -->
    <template v-else-if="summary.view === IDENTITY_ADMIN">
      <div class="wbh-kpis">
        <router-link class="wbh-kpi wbh-cell leaves is-warn" :to="HOME_LINKS.leaves">
          <div class="wbh-kh"><span>待批请假</span><i class="wbh-led warn"></i></div>
          <b class="wbh-num is-warn">{{ numberText(summary.leaves) }}</b>
          <p class="wbh-kf">排班待办 · 员工已提</p>
        </router-link>
        <router-link class="wbh-kpi wbh-cell reviews is-warn" :to="HOME_LINKS.reviews">
          <div class="wbh-kh"><span>待验收</span><i class="wbh-led warn"></i></div>
          <b class="wbh-num is-warn">{{ numberText(summary.reviews) }}</b>
          <p class="wbh-kf">日常检查 · 现在能验收</p>
        </router-link>
        <router-link class="wbh-kpi wbh-cell fixes is-bad" :to="HOME_LINKS.fixes">
          <div class="wbh-kh"><span>逾期整改</span><i class="wbh-led bad"></i></div>
          <b class="wbh-num is-bad">{{ numberText(summary.fixes) }}</b>
          <p class="wbh-kf">超过期限 · 未回拍</p>
        </router-link>
        <router-link class="wbh-kpi wbh-cell reminders" :to="HOME_LINKS.reminders">
          <div class="wbh-kh"><span>人事提醒</span><i class="wbh-led sig"></i></div>
          <b class="wbh-num">{{ numberText(summary.reminders) }}</b>
          <p class="wbh-kf">工龄奖 · 生日 · 健康证</p>
        </router-link>
      </div>

      <div class="wbh-cols">
        <router-link class="wbh-panel wbh-duty" :to="HOME_LINKS.duty">
          <header class="wbh-ph">
            <i class="wbh-led ok"></i><h2>今天谁上班</h2>
            <span class="wbh-hint">{{ summary.duty ? summary.duty.total : '—' }} 人上班</span>
            <router-link class="wbh-more" :to="HOME_LINKS.duty">月历 ›</router-link>
          </header>
          <p v-if="!day" class="wbh-empty">今天的排班读不出来，点进月历看</p>
          <template v-else-if="!summary.duty.groups.length && !summary.duty.off.length">
            <p class="wbh-empty">今天还没排班</p>
          </template>
          <template v-else>
            <div v-for="group in summary.duty.groups" :key="group.shift" class="wbh-shift">
              <div class="wbh-sh"><i class="wbh-led sig"></i><b>{{ group.shift }}</b><em>{{ group.count }} 人</em></div>
              <p>{{ group.names.join('、') }}</p>
            </div>
            <ul v-if="summary.duty.off.length" class="wbh-offs">
              <li v-for="group in summary.duty.off" :key="group.label" :class="group.tone">
                <b>{{ group.label }}</b><span>{{ group.count }} 人</span><i>{{ group.names.join('、') }}</i>
              </li>
            </ul>
          </template>
        </router-link>

        <div class="wbh-side">
          <!-- 备货计划（ADR 0117）：首页第一眼就该看到「今天还差什么」，不用记着去后勤组里翻。 -->
          <section class="wbh-panel">
            <header class="wbh-ph">
              <i class="wbh-led sig"></i><h2>备货计划</h2>
              <span v-if="prep" class="wbh-hint">还差 {{ prep.remaining }} 项</span>
              <router-link class="wbh-more" :to="PREP_PLAN_PATH">去备货 ›</router-link>
            </header>
            <ul v-if="prep && prep.stations && prep.stations.length" class="wbh-prep">
              <li v-for="row in prep.stations" :key="row.station">
                <b>{{ row.label || row.station }}</b>
                <span>{{ row.remaining }}</span>
              </li>
            </ul>
            <p v-else class="wbh-empty">今天还没算（进备货计划页就开始算）</p>
          </section>

          <!-- 等我处理：把四个读数展开成「具体哪几件事」（2026-10-18 实用性改进）。
               一条一行、点行直达那个专页；数据全部来自本页已经在拉的三份响应。 -->
          <section class="wbh-panel">
            <header class="wbh-ph">
              <i class="wbh-led warn"></i><h2>等我处理</h2>
              <span v-if="summary.todos && summary.todos.length" class="wbh-hint">共 {{ summary.todos.length }} 件</span>
            </header>
            <ul v-if="summary.todos && summary.todos.length" class="wbh-todos">
              <li v-for="row in summary.todos.slice(0, MANAGER_TODO_LIMIT)" :key="row.key">
                <router-link class="wbh-todo" :to="row.to">
                  <span class="wbh-kind" :class="row.tone">{{ row.kind }}</span>
                  <span class="wbh-todo-main"><b>{{ row.title }}</b><i>{{ row.meta }}</i></span>
                  <span class="wbh-chev">›</span>
                </router-link>
              </li>
            </ul>
            <p v-if="summary.todos && summary.todos.length > MANAGER_TODO_LIMIT" class="wbh-more-line">
              还有 {{ summary.todos.length - MANAGER_TODO_LIMIT }} 件：去对应页面看全部
            </p>
            <p v-if="!summary.todos || !summary.todos.length" class="wbh-empty wbh-todo-empty">没有等你处理的事。</p>
          </section>
          <section class="wbh-panel">
            <header class="wbh-ph"><i class="wbh-led sig"></i><h2>常用</h2></header>
            <div class="wbh-quick">
              <router-link v-for="link in QUICK_LINKS" :key="link.to" :to="link.to">{{ link.label }}</router-link>
            </div>
          </section>
        </div>
      </div>
    </template>

    <!-- ── 员工：我的班次 / 工作区 / 待办（B 案：我的班 + 今天要交 + 两个数字）── -->
    <template v-else>
      <!-- 读班次的那几秒不要先报「今天没有你的班」：`staffShiftCell(null)` 的默认文案就是
           这句。身份探针先回、`/me` 后回，从别的页点回「今天」时每次都会闪一下。 -->
      <p v-if="state !== 'ready'" class="wbh-wait">正在读你的班…</p>
      <template v-else>
      <!-- 我的班：卡片本体整块点进「整月」页（2026-10-30 用户裁定，原来进的是「今天」页；
           测试挂点 `.wbh-me` 保持是那个链接）；
           副行下面多一行四个入口（2026-10-29 用户裁定加上「加班」与「损耗」）：请假 / 换班
           一次点击直达「今天」页并带上动作与营业日 —— 那一页把 `?act=&date=` 转进月历当天的
           操作单（发起只有那一处表单）；加班登记、损耗登记本来就在自己那一页
           （`/workbench/staff/overtime`、`/workbench/staff/loss`），直接过去，不在这儿再
           摆第二张表单。 -->
      <section class="wbh-me-card">
        <router-link class="wbh-me" :to="HOME_LINKS.myMonth">
          <div class="wbh-kh">
            <span>我的班</span>
            <!-- 状态不能再写死「在岗」：休 / 请假 / 未排的那天大字写「休」、右上角却亮绿灯，
                 是员工每天第一眼就能看到的错话。tone 与「今天」页那五态同一份口径。 -->
            <em class="wbh-onsite" :class="'is-' + (summary.me.shift.tone || 'shift')">
              <i class="wbh-led" :class="shiftState(summary.me.shift.tone).led"></i>{{ shiftState(summary.me.shift.tone).text }}
            </em>
          </div>
          <b class="wbh-shift-big">{{ summary.me.shift.headline }}</b>
          <p class="wbh-sub">{{ summary.me.shift.subline }}</p>
        </router-link>
        <div class="wbh-me-acts">
          <router-link class="wbh-me-act" :to="staffDayLink('leave')">请假</router-link>
          <router-link class="wbh-me-act" :to="staffDayLink('swap')">换班</router-link>
          <router-link class="wbh-me-act" :to="HOME_LINKS.myOvertime">加班</router-link>
          <router-link class="wbh-me-act" :to="HOME_LINKS.myWastage">损耗</router-link>
        </div>
      </section>

      <StaffHygieneCard />

      <div class="wbh-cells">
        <router-link class="wbh-cell myItems" :to="HOME_LINKS.myItems">
          <div class="wbh-kh"><span>我的待办</span><i class="wbh-led warn"></i></div>
          <b class="wbh-num is-warn">{{ numberText(summary.work.pending) }}</b>
          <p class="wbh-kf">卫生待办 ›</p>
        </router-link>
        <router-link class="wbh-cell myRequests" :to="HOME_LINKS.myRequests">
          <div class="wbh-kh"><span>等我回应</span><i class="wbh-led sig"></i></div>
          <b class="wbh-num">{{ numberText(summary.incoming) }}</b>
          <p class="wbh-kf">我的申请 ›</p>
        </router-link>
      </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
/* B 案「钢面 · 仪」（2026-10-17 重构）：读数 + 面板 + LED。全部走语义令牌，
   浅 / 深两套主题各自生效；这里不写裸色值。 */
.wbh {
  padding-bottom: calc(40px + var(--safe-b));
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.wbh-head { display: flex; align-items: flex-end; gap: 16px; }
.wbh-kicker {
  margin: 0 0 7px;
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .26em;
  color: var(--ink-faint);
}
.wbh-title {
  margin: 0;
  font: 600 26px/1 var(--font-sans);
  letter-spacing: .01em;
  color: var(--ink);
}
.wbh-wait { margin: 18px 0 0; font-size: 12px; color: var(--ink-dim); }

.wbh-pills { margin-left: auto; display: flex; flex-wrap: wrap; gap: 8px; }
.wbh-pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 10px; border: 1px solid var(--line-strong); border-radius: var(--r-1);
  background: var(--surface-1); color: var(--ink-dim);
  font: 500 10px/1 var(--font-mono);
}
.wbh-pill b { color: var(--ink); font-weight: 600; }
.wbh-pill.is-warn b { color: var(--warn); }

.wbh-led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; }
.wbh-led.ok { background: var(--ok); box-shadow: 0 0 0 3px var(--wb-ok-a16); }
.wbh-led.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.wbh-led.bad { background: var(--danger); box-shadow: 0 0 0 3px var(--wb-danger-a16); }
.wbh-led.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }
.wbh-led.off { background: var(--line-strong); }
.wbh-onsite.is-rest, .wbh-onsite.is-none { color: var(--ink-faint); }
.wbh-onsite.is-leave { color: var(--info); }
.wbh-onsite.is-half, .wbh-onsite.is-moved { color: var(--warn); }

.wbh-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.wbh-kpi {
  position: relative; overflow: hidden;
  display: flex; flex-direction: column; gap: 9px;
  padding: 13px 15px 12px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  text-decoration: none;
}
.wbh-kpi::after { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--action); }
.wbh-kpi.is-warn::after { background: var(--warn); }
.wbh-kpi.is-bad::after { background: var(--danger); }
.wbh-kh {
  display: flex; align-items: center; gap: 7px;
  font: 500 9.5px/1 var(--font-sans); letter-spacing: .18em; color: var(--ink-faint);
}
.wbh-kh .wbh-led { margin-left: auto; }
.wbh-num { font: 500 30px/1 var(--font-mono); letter-spacing: -.03em; color: var(--ink); }
.wbh-num.is-warn { color: var(--warn); }
.wbh-num.is-bad { color: var(--danger-bright); }
.wbh-gauge { display: flex; gap: 2px; }
.wbh-gauge i { flex: 1; height: 3px; border-radius: 1px; background: var(--line); }
.wbh-gauge i.on { background: var(--action); }
.wbh-kpi.is-warn .wbh-gauge i.on { background: var(--warn); }
.wbh-kpi.is-bad .wbh-gauge i.on { background: var(--danger); }
.wbh-kf { margin: 0; font-size: 9.5px; color: var(--ink-faint); }

.wbh-cols { display: grid; grid-template-columns: 1.6fr 1fr; gap: 14px; align-items: start; }
.wbh-side { display: flex; flex-direction: column; gap: 14px; }
.wbh-panel {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  overflow: hidden;
}
.wbh-ph { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.wbh-ph h2 { margin: 0; font: 600 12.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.wbh-hint { font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.wbh-more { margin-left: auto; font-size: 10.5px; color: var(--ink-dim); text-decoration: none; }
.wbh-empty { margin: 0; padding: 14px; font-size: 11px; color: var(--ink-dim); }
.wbh-more-line { margin: 0; padding: 10px 14px; border-top: 1px solid var(--wb-ink-a08); font-size: 10.5px; color: var(--ink-faint); }

.wbh-shift { padding: 12px 14px 13px; border-bottom: 1px solid var(--wb-ink-a08); }
.wbh-shift:last-child { border-bottom: 0; }
.wbh-sh { display: flex; align-items: center; gap: 8px; }
.wbh-sh b { font-size: 11.5px; font-weight: 600; letter-spacing: .04em; color: var(--ink); }
.wbh-sh em { margin-left: auto; font-style: normal; font: 500 11px/1 var(--font-mono); color: var(--ink); }
.wbh-shift p { margin: 7px 0 0; font-size: 10.5px; line-height: 1.6; color: var(--ink-dim); }

.wbh-offs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.wbh-duty { text-decoration: none; display: block; }
.wbh-offs li {
  display: flex; align-items: baseline; gap: 8px;
  padding: 9px 14px; border-top: 1px solid var(--wb-ink-a08);
  font-size: 10.5px; color: var(--ink-dim);
}
.wbh-offs li b { min-width: 26px; font-weight: 600; color: var(--ink); }
.wbh-offs li i { font-style: normal; margin-left: auto; color: var(--ink-dim); }

/* 等我处理：类型 / 主副两行 / 箭头，行高 56（触控），一行一条。 */
.wbh-todos { list-style: none; margin: 0; padding: 0; }
.wbh-todos li { border-bottom: 1px solid var(--wb-ink-a08); }
.wbh-todos li:last-child { border-bottom: 0; }
.wbh-todo {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 56px;
  padding: 9px 14px;
  text-decoration: none;
}
.wbh-todo:hover { background: var(--surface-2); }
.wbh-kind {
  font: 500 9px/1 var(--font-sans);
  letter-spacing: .16em;
  color: var(--ink-dim);
  border-left: 2px solid currentColor;
  padding-left: 6px;
}
.wbh-kind.warn { color: var(--warn); }
.wbh-kind.bad { color: var(--danger-bright); }
.wbh-todo-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.wbh-todo-main b { font-size: 12px; font-weight: 600; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wbh-todo-main i { font-style: normal; font: 400 9.5px/1.2 var(--font-mono); color: var(--ink-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wbh-chev { color: var(--ink-faint); font-size: 14px; }
.wbh-empty { margin: 0; padding: 14px; font-size: 11px; color: var(--ink-dim); }
.wbh-more-line { margin: 0; padding: 10px 14px; border-top: 1px solid var(--wb-ink-a08); font-size: 10.5px; color: var(--ink-faint); }

.wbh-rem { display: flex; flex-direction: column; }
.wbh-rem-row { display: flex; align-items: baseline; gap: 10px; padding: 12px 14px; }
.wbh-rem-row b { font: 500 22px/1 var(--font-mono); color: var(--ink); }
.wbh-rem-row span { font-size: 10px; color: var(--ink-dim); }
.wbh-rem-go {
  display: block; padding: 9px 14px; border-top: 1px solid var(--wb-ink-a08);
  font-size: 10.5px; color: var(--ink-dim); text-decoration: none;
}
.wbh-quick { display: grid; grid-template-columns: 1fr 1fr; }
.wbh-quick a {
  padding: 10px 14px; font-size: 11px; color: var(--ink-dim); text-decoration: none;
  border-bottom: 1px solid var(--wb-ink-a08);
}
.wbh-quick a:nth-child(odd) { border-right: 1px solid var(--wb-ink-a08); }

/* 备货计划（ADR 0117）：一行一个档口，右边是「还差几项」。 */
.wbh-prep { list-style: none; margin: 0; padding: 0; }
.wbh-prep li {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  min-height: 44px; padding: 0 14px;
  border-bottom: 1px solid var(--wb-ink-a08);
}
.wbh-prep li:last-child { border-bottom: 0; }
.wbh-prep b { font-weight: 600; font-size: 12px; color: var(--ink); }
.wbh-prep span {
  font-size: 13px; font-weight: 600; color: var(--action);
  font-variant-numeric: tabular-nums;
}

/* 员工档：卡片外壳（底 / 边 / 阴影 / 左侧语义条）+ 里面的班次链接 + 一行直达入口。
   外壳不是链接：请假 / 换班 是两张碰得到的入口，链接不能套链接。 */
.wbh-me-card {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  overflow: hidden;
  position: relative;
}
.wbh-me-card::after { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--action); }
.wbh-me {
  display: flex; flex-direction: column; gap: 10px;
  padding: 14px 16px 12px;
  text-decoration: none;
}
.wbh-me-acts {
  display: flex;
  border-top: 1px solid var(--wb-ink-a08);
}
.wbh-me-act {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
  font-size: 13px;
  font-weight: 600;
  color: var(--action);
  text-decoration: none;
}
.wbh-me-act + .wbh-me-act { border-left: 1px solid var(--wb-ink-a08); }
.wbh-onsite { margin-left: auto; display: inline-flex; align-items: center; gap: 5px; font-style: normal; font-size: 9.5px; color: var(--ok); letter-spacing: .1em; }
.wbh-shift-big { font: 600 30px/1.1 var(--font-sans); letter-spacing: .02em; color: var(--ink); }
.wbh-sub { margin: 0; font-size: 11.5px; color: var(--ink-dim); }
.wbh-next { margin: 0; padding-top: 10px; border-top: 1px solid var(--wb-ink-a08); display: flex; flex-wrap: wrap; gap: 12px; font-size: 10.5px; color: var(--ink-dim); }

.wbh-cells { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.wbh-cell {
  display: flex; flex-direction: column; gap: 8px;
  padding: 12px 14px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  text-decoration: none;
}

@media (max-width: 900px) {
  .wbh-kpis { grid-template-columns: repeat(2, 1fr); }
  .wbh-cols { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .wbh-head { align-items: flex-start; flex-direction: column; gap: 10px; }
  .wbh-pills { margin-left: 0; }
  .wbh-title { font-size: 22px; }
  .wbh-kpi { padding: 11px 13px 10px; }
  .wbh-num { font-size: 26px; }
}
</style>
