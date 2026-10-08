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
 * 401 的处理交给 `staffRequest` 之外的公共路径 —— 这里用的是 `api`（管理端 cookie），
 * 员工那几条走 `staffRequest`，各自与页面自己那次请求同一条链。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import { useNudgePull } from '../../composables/useNudgePull'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { pageTitle } from '../../router/pageRoutes.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { IDENTITY_ADMIN } from '../../utils/workbenchIdentity'
import { staffRequest } from '../../utils/hygieneStaff'
import { dayLabel } from '../../utils/todayShift'
import { WORKBENCH_TITLE, workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { HOME_LINKS, workbenchHomeSummary } from '../../utils/workbenchHome'

useScopedStylesheet('/hygiene-admin.css')

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
  ])
  businessDate.value = date
    || (day.value && day.value.business_date)
    || (inbox.value && inbox.value.today)
    || ''
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
  <div class="hygiene-admin wbh">
    <header class="wbh-head">
      <p class="wbh-brand">{{ WORKBENCH_TITLE }}</p>
      <h1 class="wbh-title">今天<span v-if="dateText" class="wbh-date">{{ dateText }}</span></h1>
    </header>

    <!-- 探针还没回来：一个数字都不渲染（两个视角都可能，先说清在等什么）。 -->
    <p v-if="!summary.view" class="wbh-wait">{{ state === 'loading' ? '正在确认身份…' : '身份还没定，稍后再看' }}</p>

    <!-- ── 店长：今天谁上班 + 三个数字 ─────────────────────────────────── -->
    <template v-else-if="summary.view === IDENTITY_ADMIN">
      <router-link class="wbh-card wbh-duty" :to="HOME_LINKS.duty">
        <div class="wbh-card-hd">
          <span class="wbh-card-title">今天谁上班</span>
          <span class="wbh-card-more">排班月历 ›</span>
        </div>
        <p v-if="!day" class="wbh-sub">今天的排班读不出来，点进月历看</p>
        <template v-else-if="!summary.duty.groups.length">
          <p class="wbh-sub">今天还没排班</p>
        </template>
        <template v-else>
          <p class="wbh-duty-total">{{ summary.duty.total }} 人上班</p>
          <ul class="wbh-shifts">
            <li v-for="group in summary.duty.groups" :key="group.shift">
              <b class="wbh-shift-name">{{ group.shift }}</b>
              <span class="wbh-shift-count">{{ group.count }} 人</span>
              <span class="wbh-shift-people">{{ group.names.join('、') }}</span>
            </li>
          </ul>
        </template>
      </router-link>

      <div class="wbh-cells">
        <router-link class="wbh-cell leaves" :to="HOME_LINKS.leaves">
          <span class="wbh-num">{{ numberText(summary.leaves) }}</span>
          <span class="wbh-label">待批请假</span>
          <span class="wbh-go">排班待办 ›</span>
        </router-link>
        <router-link class="wbh-cell reviews" :to="HOME_LINKS.reviews">
          <span class="wbh-num">{{ numberText(summary.reviews) }}</span>
          <span class="wbh-label">待验收</span>
          <span class="wbh-go">日常验收 ›</span>
        </router-link>
        <router-link class="wbh-cell fixes" :to="HOME_LINKS.fixes">
          <span class="wbh-num">{{ numberText(summary.fixes) }}</span>
          <span class="wbh-label">逾期整改</span>
          <span class="wbh-go">整改单 ›</span>
        </router-link>
        <!-- 健康证到期（2026-10 花名册改版）：数字与花名册行标签**同源**（服务端那份
             `daily-queue` 的 `health_cert_due`），点进去是花名册（补档案、看谁到期）。 -->
        <router-link class="wbh-cell certs" :to="HOME_LINKS.certs" title="临期或已过期的人数">
          <span class="wbh-num">{{ numberText(summary.certs) }}</span>
          <span class="wbh-label">健康证到期</span>
          <span class="wbh-go">花名册 ›</span>
        </router-link>
        <!-- 人事提醒（第五格）：本月**该调工龄奖**的人数 + 本月**生日**人数，两块都在
             人事提醒页上（那里还有第三块「档案待补」，但它不进这个数字）。 -->
        <router-link
          class="wbh-cell reminders"
          :to="HOME_LINKS.reminders"
          title="本月该调工龄奖 + 本月生日的人数"
        >
          <span class="wbh-num">{{ numberText(summary.reminders) }}</span>
          <span class="wbh-label">人事提醒</span>
          <span class="wbh-go">工龄奖 · 生日 ›</span>
        </router-link>
      </div>
    </template>

    <!-- ── 员工：我的班次 / 工作区 / 待办 ───────────────────────────────── -->
    <template v-else>
      <router-link class="wbh-card wbh-me" :to="HOME_LINKS.myRequests">
        <div class="wbh-card-hd">
          <span class="wbh-card-title">我的班</span>
          <span class="wbh-card-more">今天 ›</span>
        </div>
        <p class="wbh-shift" :class="summary.me.shift.tone">{{ summary.me.shift.headline }}</p>
        <p class="wbh-sub">{{ summary.me.shift.subline }}</p>
        <p v-if="summary.me.next.length" class="wbh-next">
          <span v-for="day in summary.me.next" :key="day.business_date">
            {{ dayLabel(day.business_date) }} {{ day.text }}
          </span>
        </p>
      </router-link>

      <div class="wbh-cells">
        <router-link class="wbh-cell myItems" :to="HOME_LINKS.myItems">
          <span class="wbh-num">{{ numberText(summary.work.pending) }}</span>
          <span class="wbh-label">我的待办</span>
          <span class="wbh-go">卫生待办 ›</span>
        </router-link>
        <router-link class="wbh-cell myRequests" :to="HOME_LINKS.myRequests">
          <span class="wbh-num">{{ numberText(summary.incoming) }}</span>
          <span class="wbh-label">等我回应</span>
          <span class="wbh-go">我的申请 ›</span>
        </router-link>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 首页沿用工作台那套深青墨令牌（`/hygiene-admin.css`，由本页加载一次）。 */
.wbh {
  padding: var(--hy-page, 18px);
  padding-bottom: 40px;
  gap: 14px;
}
.wbh-head { display: flex; flex-direction: column; gap: 4px; }
.wbh-brand {
  margin: 0;
  font-family: var(--font-song); font-size: 12px;
  letter-spacing: .3em; color: var(--hy-faint);
}
.wbh-title {
  margin: 0;
  display: flex; align-items: baseline; gap: 10px;
  font-family: var(--font-song); font-size: 22px;
  letter-spacing: .08em; color: var(--hy-ink);
}
.wbh-date { font-size: 12.5px; letter-spacing: .04em; color: var(--hy-muted); }
.wbh-wait { margin: 18px 0 0; font-size: 13px; color: var(--hy-muted); }

.wbh-card {
  display: block; text-decoration: none;
  background: var(--hy-surface);
  border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-lg);
  padding: 14px 16px;
}
.wbh-card:hover { border-color: var(--hy-line-strong); }
.wbh-card-hd { display: flex; align-items: baseline; gap: 10px; }
.wbh-card-title { font-size: 13px; color: var(--hy-ink); letter-spacing: .06em; }
.wbh-card-more { margin-left: auto; font-size: 11.5px; color: var(--hy-mint); }
.wbh-sub { margin: 6px 0 0; font-size: 12.5px; color: var(--hy-muted); }

/* 今天谁上班：人数一行、各班一行。 */
.wbh-duty-total {
  margin: 8px 0 0;
  font-size: 22px; color: var(--hy-ink);
}
.wbh-shifts { margin: 10px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.wbh-shifts li { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; }
.wbh-shift-name { color: var(--hy-mint); font-weight: 600; }
.wbh-shift-count { color: var(--hy-muted); }
.wbh-shift-people { color: var(--hy-ink); min-width: 0; overflow-wrap: anywhere; }

/* 那几格数字：一格一扇门。 */
.wbh-cells { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.wbh-cell {
  display: flex; flex-direction: column; gap: 2px;
  text-decoration: none;
  background: var(--hy-surface);
  border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-lg);
  padding: 14px 16px;
}
.wbh-cell:hover { border-color: var(--hy-mint-line); background: var(--hy-mint-soft); }
.wbh-num {
  font-family: var(--font-song); font-size: 26px; line-height: 1.1;
  color: var(--hy-ink);
}
.wbh-label { font-size: 12.5px; color: var(--hy-ink); }
.wbh-go { margin-top: 4px; font-size: 11px; color: var(--hy-muted); }

/* 我的班：跟「今天」页同一套语气（上班玉色 / 休灰 / 请假青 / 已调整琥珀）。 */
.wbh-shift { margin: 10px 0 0; font-family: var(--font-song); font-size: 24px; color: var(--hy-mint); }
.wbh-shift.rest { color: var(--hy-muted); }
.wbh-shift.none { color: var(--hy-faint); }
.wbh-shift.leave { color: var(--hy-aqua, var(--hy-mint)); }
.wbh-shift.moved { color: var(--hy-amber, var(--hy-seal-bright)); }
.wbh-next { margin: 10px 0 0; display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: var(--hy-muted); }
</style>
