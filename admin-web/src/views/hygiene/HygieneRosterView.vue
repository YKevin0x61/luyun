<script setup>
/**
 * 花名册（工作台 · 人事组的第四页）。
 *
 * 改版口径见 `.scratch/roster-redesign/design.md`：这一页只做三件事 —— 名单（待批准置顶 +
 * 在职 / 已停用两档）、编辑抽屉（11 项字段 + 底部四个动作）、邀请店员弹层。原来那些
 * 当天排班/工作区控件、只读七项、规则折叠块全部退出本页（排班那件事的唯一真相源是排班页）。
 *
 * 三条不能动的约束：
 *   1. **抽屉与弹层不 Teleport**：`--hy-*` 全部定义在 `.hygiene-admin` 上，Teleport 到 body
 *      等于丢光样式。它们写成 `.roster-page` 的直接子元素。
 *   2. **它们都要 `animation: none`**：共享表给 `.roster-page > *` 挂了 `hy-rise`，那个
 *      `transform`（`both` 保留到最终帧）会让内部 `position: fixed` 相对卡片而不是视口定位。
 *   3. **`admin_caps` 是整组替换**：保存时必须用 `keepSupervisorOnlyCaps` 把只读七项在库里
 *      已有的值原样带回 —— 页面不显示 ≠ 可以抹掉（少带一个就是静默删权限）。
 *
 * 敏感字段口径（design §12.1）：身份证号与底薪只在这里的管理端出现（抽屉 / 导出），
 * 员工端两条接口都不下发它们。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import {
  ADMIN_CAP_STAFF_DEFS,
  keepSupervisorOnlyCaps,
  normalizeCaps,
} from '../../utils/adminCaps'
import { hygienePermissionLabel, rosterStatusLabel } from '../../utils/hygieneCopy'
import { chinaTodayDate, formatHygieneStamp } from '../../utils/hygieneTime'
import { STAFF_ENTRY_PATH } from '../../utils/staffPaths'

const employees = ref([])
const loading = ref(true)
const loadedOnce = ref(false)
const loadError = ref('')
const errorText = ref('')
const savedHint = ref('')
let savedHintTimer = null

// 工具栏两个筛选位：搜索是**本地**过滤（不请求），分段筛选是这一页唯一的一组开关。
const keyword = ref('')
const segment = ref('all')
const SEGMENTS = [
  { id: 'all', label: '全部', title: '待批准和在职的人' },
  { id: 'missing', label: '待补', title: '档案没补全的员工' },
  { id: 'disabled', label: '已停用', title: '停用后不能登录的员工' },
]

// 抽屉：`drawerRow` 是打开那一刻的那一行，`draft` 是它的草稿。实时到只更新列表，
// **不覆盖正在编辑的草稿**（design §3.8）——否则管理员刚输入的值会被冲掉。
const drawerRow = ref(null)
const draft = ref(null)
const drawerError = ref('')
const busy = ref(false)
const discardOpen = ref(false)
const disableTarget = ref(null)

const exporting = ref(false)

// 邀请店员弹层（二维码 + 链接 + 复制）。
const inviteOpen = ref(false)
const inviteUrl = ref('')
const inviteCopied = ref(false)
const inviteCopyTitle = ref('')
const qrCanvas = ref(null)

const todayDate = chinaTodayDate()

function flash(text) {
  savedHint.value = text
  if (savedHintTimer) clearTimeout(savedHintTimer)
  savedHintTimer = setTimeout(() => { savedHint.value = '' }, 4000)
}

// ── 分组与排序 ────────────────────────────────────────────────────────────
// 全部 = 待批准 + 在职（**不含已停用**，已停用默认隐藏）；待补 = 未停用且档案缺项；
// 已停用 = disabled。计数看**整份名单**，不受搜索影响（搜索是"找人"的临时动作）。
function employeeCount(id) {
  if (id === 'missing') {
    return employees.value.filter((row) => !row.disabled && row.profile_incomplete === true).length
  }
  if (id === 'disabled') {
    return employees.value.filter((row) => row.disabled === true).length
  }
  return 0
}

const searched = computed(() => {
  const needle = keyword.value.trim().toLowerCase()
  if (!needle) return employees.value
  return employees.value.filter((row) => (
    String(row.name || '').toLowerCase().includes(needle)
    || String(row.phone || '').toLowerCase().includes(needle)
  ))
})

const segmentRows = computed(() => {
  if (segment.value === 'missing') {
    return searched.value.filter((row) => !row.disabled && row.profile_incomplete === true)
  }
  if (segment.value === 'disabled') {
    return searched.value.filter((row) => row.disabled === true)
  }
  return searched.value.filter((row) => !row.disabled)
})

function phoneOrder(a, b) {
  return String(a.phone || '').localeCompare(String(b.phone || ''))
}

/** 在职 / 已停用：姓名中文升序，同名按手机号；没有姓名的一律排最后。 */
function byName(a, b) {
  const nameA = String(a.name || '').trim()
  const nameB = String(b.name || '').trim()
  if (!nameA && !nameB) return phoneOrder(a, b)
  if (!nameA) return 1
  if (!nameB) return -1
  const compared = nameA.localeCompare(nameB, 'zh-Hans-CN')
  return compared === 0 ? phoneOrder(a, b) : compared
}

/** 待批准：注册时间升序（等得最久的在最上面）；没有注册时间的按 id 升序。 */
function byCreatedAt(a, b) {
  const stampA = Date.parse(a.created_at || '')
  const stampB = Date.parse(b.created_at || '')
  if (Number.isFinite(stampA) && Number.isFinite(stampB) && stampA !== stampB) return stampA - stampB
  if (Number.isFinite(stampA) !== Number.isFinite(stampB)) return Number.isFinite(stampA) ? -1 : 1
  return Number(a.id) - Number(b.id)
}

const pendingRows = computed(() => (
  segment.value === 'disabled'
    ? []
    : segmentRows.value.filter((row) => !row.approved && !row.disabled).sort(byCreatedAt)
))
const activeRows = computed(() => (
  segment.value === 'disabled'
    ? []
    : segmentRows.value.filter((row) => row.approved && !row.disabled).sort(byName)
))
const disabledRows = computed(() => (
  segment.value === 'disabled'
    ? segmentRows.value.filter((row) => row.disabled).sort(byName)
    : []
))

/** 页面上实际渲染的分区（顺序固定：待批准最上面）。空分区整块不渲染。 */
const rosterGroups = computed(() => {
  const groups = []
  if (pendingRows.value.length) groups.push({ key: 'pending', title: '待批准', rows: pendingRows.value })
  if (activeRows.value.length) groups.push({ key: 'active', title: '在职', rows: activeRows.value })
  if (disabledRows.value.length) groups.push({ key: 'disabled', title: '已停用', rows: disabledRows.value })
  return groups
})

const searchMissed = computed(() => Boolean(keyword.value.trim()) && searched.value.length === 0)
// 「还没有人注册」只在真的一个人都没有时出现（搜索命不中走另一句）。
const nobodyYet = computed(() => !employees.value.length && segment.value !== 'disabled')

/** 分段格上的文字：`全部` 不带数字，`待补 3` / `已停用 1` 带（数字是**整份名单**里的计数）。 */
function segmentText(item) {
  return item.id === 'all' ? item.label : `${item.label} ${employeeCount(item.id)}`
}

/** 「管理员 · N 项 / 普通员工 · 无」：标签用服务端派生的值，**项数只数员工端真正生效的
 *  三项**（`admin_caps` 里可能存着超管专属七项，按全长度报会写成「管理员 · 8 项」而实际
 *  能用 0 项）。边缘情况照实显示「管理员 · 0 项」。 */
function capChip(row) {
  const caps = normalizeCaps(row.admin_caps)
  const usable = ADMIN_CAP_STAFF_DEFS.filter((item) => caps.includes(item.key)).length
  return hygienePermissionLabel(row.permission) === '管理员' ? `管理员 · ${usable} 项` : '普通员工 · 无'
}

function healthChip(row) {
  if (row.health_cert_state === 'soon') {
    return { text: `健康证 ${row.health_cert_expires_on} 到期`, title: '30 天内到期', kind: 'is-warn' }
  }
  if (row.health_cert_state === 'expired') {
    return { text: `健康证 ${row.health_cert_expires_on} 已过期`, title: '已过期', kind: 'is-danger' }
  }
  return null
}

// ── 读取 ──────────────────────────────────────────────────────────────────
async function loadRoster(options = {}) {
  const quiet = options.quiet === true
  if (!quiet) loading.value = true
  loadError.value = ''
  try {
    const data = await api.get('/api/hygiene/admin/roster')
    employees.value = data.employees || []
    loadedOnce.value = true
  } catch (err) {
    const message = err.message || '无法加载花名册'
    // 名单已经读到过一次：这次读挂了只报一行（列表留在原地），不把整张表换成错误态。
    if (loadedOnce.value) errorText.value = message
    else loadError.value = message
  } finally {
    if (!quiet) loading.value = false
  }
}

function onKeydown(event) {
  // 确认框自己处理 Esc（捕获阶段就吞掉了事件）；这里再兜一道：聚焦在确认框上时
  // 抽屉不该被同一次 Esc 关掉（那两个动作是两回事）。
  if (discardOpen.value || disableTarget.value) return
  if (event.key !== 'Escape' || !drawerRow.value) return
  requestClose()
}

onMounted(() => {
  loadRoster()
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  if (savedHintTimer) clearTimeout(savedHintTimer)
})

// 实时只订 roster：这一页不再有当天的编辑动作，收 assignment 只换来无意义的重取。
useHygieneRealtime({
  id: 'hygiene-admin-roster',
  resources: ['roster'],
  pull: () => loadRoster({ quiet: true }),
})

// ── 抽屉 ──────────────────────────────────────────────────────────────────
function draftFrom(row) {
  return {
    name: row.name || '',
    job_title: row.job_title || '',
    id_card_no: row.id_card_no || '',
    health_cert_date: row.health_cert_date || '',
    base_salary: row.base_salary === null || row.base_salary === undefined ? '' : String(row.base_salary),
    hire_date: row.hire_date || '',
    seniority_bonus:
      row.seniority_bonus === null || row.seniority_bonus === undefined
        ? ''
        : String(row.seniority_bonus),
    admin_caps: normalizeCaps(row.admin_caps),
  }
}

function salaryValue(raw) {
  const text = String(raw ?? '').trim()
  if (!text) return null
  const num = Number(text)
  return Number.isFinite(num) ? num : null
}

/** PATCH 的 body：**只有这 8 个键**（不再发 `permission`，服务端按开关派生）。
 *
 *  工龄奖在列，但它**不是**「待补」四项之一 —— 空值的意思是「还没调过」，
 *  既不参与 `profile_incomplete`，也不挡批准（`docs/adr/0101`）。
 */
function patchBody(source, row) {
  return {
    name: String(source.name || '').trim(),
    job_title: String(source.job_title || '').trim(),
    id_card_no: String(source.id_card_no || '').trim().toUpperCase(),
    health_cert_date: source.health_cert_date || null,
    base_salary: salaryValue(source.base_salary),
    hire_date: source.hire_date || null,
    seniority_bonus: salaryValue(source.seniority_bonus),
    admin_caps: normalizeCaps([...source.admin_caps, ...keepSupervisorOnlyCaps(row.admin_caps)]),
  }
}

const dirty = computed(() => {
  if (!drawerRow.value || !draft.value) return false
  return JSON.stringify(patchBody(draft.value, drawerRow.value))
    !== JSON.stringify(patchBody(draftFrom(drawerRow.value), drawerRow.value))
})

const missingSalary = computed(() => Boolean(draft.value) && salaryValue(draft.value.base_salary) === null)
const missingHireDate = computed(() => Boolean(draft.value) && !String(draft.value.hire_date || '').trim())

/** 工龄奖那一栏旁边的对照：按入职日期算出的「应为」值（服务端派生，前端不复算）。
 *
 *  只提示、**不替人改** —— 高于应为值时同样只标出来（`docs/adr/0101` 说绝不自动下调），
 *  所以这句话是「应为 N 元」，不是「将改为 N 元」。
 */
const seniorityNote = computed(() => {
  const row = drawerRow.value
  if (!row || !draft.value) return ''
  if (!String(row.hire_date || '').trim()) return '待补入职日期'
  const shouldBe = row.seniority_should_be
  if (shouldBe === undefined || shouldBe === null) return ''
  if (salaryValue(draft.value.seniority_bonus) === shouldBe) return ''
  return `应为 ${shouldBe} 元`
})
const canApprove = computed(() => !missingSalary.value && !missingHireDate.value)
/** 批准门槛那一行（常显，不藏在悬浮里 —— 手机没有 hover）。只有「待批准」的人才有这一行：
 *  已批准 / 已停用的人下面根本没有「批准」按钮，摆一句"还缺…"是在说一件不存在的事。 */
const gateNote = computed(() => {
  if (!drawerRow.value || drawerRow.value.approved || drawerRow.value.disabled) return ''
  if (missingSalary.value && missingHireDate.value) return '还缺底薪和入职日期'
  if (missingSalary.value) return '还缺底薪'
  if (missingHireDate.value) return '还缺入职日期'
  return ''
})

const primaryAction = computed(() => {
  if (!drawerRow.value) return 'save'
  if (drawerRow.value.disabled) return 'enable'
  if (!drawerRow.value.approved) return 'approve'
  return 'save'
})

const createdText = computed(() => {
  const stamp = drawerRow.value && drawerRow.value.created_at
  return stamp ? (formatHygieneStamp(stamp) || '—') : '—'
})

const expiresText = computed(() => (drawerRow.value && drawerRow.value.health_cert_expires_on) || '未设置')

function openDrawer(row) {
  drawerRow.value = row
  draft.value = draftFrom(row)
  drawerError.value = ''
  savedHint.value = ''
}

function closeDrawer() {
  drawerRow.value = null
  draft.value = null
  drawerError.value = ''
  discardOpen.value = false
}

function requestClose() {
  if (busy.value) return
  if (dirty.value) {
    discardOpen.value = true
    return
  }
  closeDrawer()
}

function confirmDiscard() {
  discardOpen.value = false
  closeDrawer()
}

/** 保存成功后抽屉留在原地、草稿换成服务端返回值（方便接着改）。 */
async function reloadDrawer(id) {
  await loadRoster({ quiet: true })
  const fresh = employees.value.find((row) => row.id === id)
  if (!fresh) {
    closeDrawer()
    return
  }
  drawerRow.value = fresh
  draft.value = draftFrom(fresh)
}

async function saveDraft() {
  const row = drawerRow.value
  if (!row || busy.value || !dirty.value) return
  const body = patchBody(draft.value, row)
  if (!body.name) {
    drawerError.value = '请填写员工姓名'
    return
  }
  busy.value = true
  drawerError.value = ''
  savedHint.value = ''
  try {
    await api.patch(`/api/hygiene/admin/roster/${row.id}`, body)
    await reloadDrawer(row.id)
    flash('已保存')
  } catch (err) {
    // 服务端的中文原话直接摆出来（空 PATCH / 底薪格式 / 身份证重复…），别吞。
    drawerError.value = err.message || '保存失败'
  } finally {
    busy.value = false
  }
}

async function approveRow() {
  const row = drawerRow.value
  if (!row || busy.value || !canApprove.value) return
  const label = row.name || row.phone
  busy.value = true
  drawerError.value = ''
  savedHint.value = ''
  try {
    // 门槛按**库里的值**判（服务端同一口径）：草稿里刚补上的两项先落地，再批准。
    if (dirty.value) {
      const body = patchBody(draft.value, row)
      if (!body.name) {
        drawerError.value = '请填写员工姓名'
        return
      }
      await api.patch(`/api/hygiene/admin/roster/${row.id}`, body)
    }
    await api.post(`/api/hygiene/admin/roster/${row.id}/approve`)
    closeDrawer()
    await loadRoster({ quiet: true })
    flash(`已批准 ${label}`)
  } catch (err) {
    drawerError.value = err.message || '批准失败'
  } finally {
    busy.value = false
  }
}

function askDisable() {
  if (busy.value || !drawerRow.value) return
  disableTarget.value = drawerRow.value
}

async function confirmDisable() {
  const row = disableTarget.value
  disableTarget.value = null
  if (!row) return
  const label = row.name || row.phone
  busy.value = true
  drawerError.value = ''
  savedHint.value = ''
  try {
    await api.post(`/api/hygiene/admin/roster/${row.id}/disable`)
    closeDrawer()
    await loadRoster({ quiet: true })
    flash(`已停用 ${label}`)
  } catch (err) {
    drawerError.value = err.message || '停用失败'
  } finally {
    busy.value = false
  }
}

async function enableRow() {
  const row = drawerRow.value
  if (!row || busy.value) return
  const label = row.name || row.phone
  busy.value = true
  drawerError.value = ''
  savedHint.value = ''
  try {
    // 启用**不走**批准门槛（恢复不是新入职）。
    await api.post(`/api/hygiene/admin/roster/${row.id}/enable`)
    closeDrawer()
    await loadRoster({ quiet: true })
    flash(`已启用 ${label}`)
  } catch (err) {
    drawerError.value = err.message || '启用失败'
  } finally {
    busy.value = false
  }
}

// ── 导出 ──────────────────────────────────────────────────────────────────
// 行集合与当前分段 + 搜索框**完全一致**（所见即所得）：分段给 `filter`、搜索词给 `q`。
const exportable = computed(() => (
  !loading.value && !loadError.value && !exporting.value && segmentRows.value.length > 0
))

async function exportRoster() {
  if (!exportable.value) return
  exporting.value = true
  errorText.value = ''
  try {
    const params = new URLSearchParams({ filter: segment.value })
    const needle = keyword.value.trim()
    if (needle) params.set('q', needle)
    await api.download(`/api/hygiene/admin/roster-export.csv?${params.toString()}`, 'roster.csv')
  } catch (err) {
    errorText.value = err.message || '导出失败'
  } finally {
    exporting.value = false
  }
}

// ── 邀请店员 ──────────────────────────────────────────────────────────────
async function openInvite() {
  inviteOpen.value = true
  inviteCopied.value = false
  inviteCopyTitle.value = ''
  // 用当前 origin：门店可能是内网 IP、也可能是域名，写死哪个都会有一半人打不开。
  // 路径取 `STAFF_ENTRY_PATH`（员工端入口 = 今天页），页面里不出现字面量路径。
  inviteUrl.value = `${window.location.origin}${STAFF_ENTRY_PATH}`
  await nextTick()
  if (!qrCanvas.value) return
  try {
    await QRCode.toCanvas(qrCanvas.value, inviteUrl.value, { width: 148, margin: 1 })
  } catch {
    // 画不出二维码不影响复制链接，静默降级（链接就在旁边）。
  }
}

function closeInvite() {
  inviteOpen.value = false
}

async function copyInvite() {
  try {
    await navigator.clipboard.writeText(inviteUrl.value)
    inviteCopied.value = true
    setTimeout(() => { inviteCopied.value = false }, 2000)
  } catch {
    // 内网明文 http 不是安全上下文，没有 clipboard API：链接在旁边，手动抄。
    inviteCopied.value = false
    inviteCopyTitle.value = '手动复制上面的链接'
  }
}
</script>

<template>
  <div class="roster-page">
    <div class="card roster-head">
      <h1>花名册</h1>
      <div class="roster-tools">
        <div class="roster-search">
          <input
            v-model="keyword"
            class="input"
            type="text"
            autocomplete="off"
            enterkeyhint="search"
            aria-label="按姓名或手机号搜索"
            placeholder="搜索姓名或手机号"
          >
          <button
            v-if="keyword"
            type="button"
            class="roster-search-clear"
            aria-label="清空搜索"
            @click="keyword = ''"
          >✕</button>
        </div>
        <div class="roster-seg" role="group" aria-label="花名册筛选">
          <button
            v-for="item in SEGMENTS"
            :key="item.id"
            type="button"
            class="roster-seg-item"
            :class="{ 'is-on': segment === item.id }"
            :title="item.title"
            :aria-pressed="segment === item.id ? 'true' : 'false'"
            @click="segment = item.id"
          >
            <span>{{ segmentText(item) }}</span>
          </button>
        </div>
        <div class="roster-tools-actions">
          <button type="button" class="btn" @click="openInvite">邀请店员</button>
          <button
            type="button"
            class="btn"
            :disabled="!exportable"
            :title="exportable ? '导出当前分组' : '这个分组没有可导出的人'"
            @click="exportRoster"
          >导出</button>
        </div>
      </div>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>
    <p v-if="savedHint" class="roster-saved" role="status">{{ savedHint }}</p>

    <div v-if="loading" class="table-card">
      <div class="roster-empty">正在加载花名册…</div>
    </div>

    <div v-else-if="loadError" class="table-card">
      <div class="roster-empty">
        <span>花名册没读出来</span>
        <button type="button" class="btn btn-sm" @click="loadRoster">重试</button>
      </div>
    </div>

    <div v-else-if="nobodyYet" class="table-card">
      <div class="roster-empty">还没有人注册，点「邀请店员」</div>
    </div>

    <div v-else-if="searchMissed" class="table-card">
      <div class="roster-empty">没有找到匹配的人</div>
    </div>

    <div v-else-if="!segmentRows.length" class="table-card">
      <div class="roster-empty">这个分组现在没有人</div>
    </div>

    <template v-else>
      <!-- 三个分区，各自一张卡：待批准**永远在最上面**，其下才是在职；已停用只在
           「已停用」分段里出现（默认隐藏）。分区的行集合与标题在 `rosterGroups` 里算。 -->
      <section v-for="group in rosterGroups" :key="group.key" class="table-card">
        <div class="table-card-header">
          <h3>{{ group.title }} <span>{{ group.rows.length }}</span></h3>
        </div>
        <div class="hy-person-list">
          <button
            v-for="row in group.rows"
            :key="row.id"
            type="button"
            class="hy-person roster-row"
            :aria-label="`编辑 ${row.name || row.phone} 的资料`"
            @click="openDrawer(row)"
          >
            <span class="roster-person-copy">
              <strong>{{ row.name || '未设置姓名' }}</strong>
              <span>{{ row.phone }}</span>
            </span>
            <span class="roster-row-meta">
              <span class="roster-chip" title="管理权限的实际项数">{{ capChip(row) }}</span>
              <span v-if="row.profile_incomplete" class="roster-chip is-warn" title="四项档案有一项为空">待补</span>
              <span
                v-if="healthChip(row)"
                class="roster-chip"
                :class="healthChip(row).kind"
                :title="healthChip(row).title"
              >{{ healthChip(row).text }}</span>
            </span>
            <span class="roster-row-tail">
              <span class="roster-status" :data-status="rosterStatusLabel(row)">{{ rosterStatusLabel(row) }}</span>
              <span class="roster-row-go" aria-hidden="true">›</span>
            </span>
          </button>
        </div>
      </section>
    </template>

    <!-- 抽屉（桌面右侧滑出 / 手机全屏）。**不 Teleport**、**animation: none**：
         两条都是样式上的硬约束，见文件头。 -->
    <div
      v-if="drawerRow"
      class="modal-overlay roster-drawer-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="roster-drawer-title"
      @click.self="requestClose"
    >
      <div class="roster-drawer">
        <div class="roster-drawer-head">
          <h3 id="roster-drawer-title" class="roster-drawer-title">{{ drawerRow.name || drawerRow.phone }}</h3>
          <button type="button" class="btn btn-sm" aria-label="关闭" @click="requestClose">关闭</button>
        </div>

        <div class="roster-drawer-body">
          <p v-if="drawerError" class="roster-error" role="alert">{{ drawerError }}</p>

          <div v-if="draft" class="hy-person-fields">
            <label>
              <span class="roster-field-head"><span>姓名</span></span>
              <input
                v-model="draft.name"
                class="input"
                type="text"
                maxlength="40"
                placeholder="请输入真实姓名"
                :disabled="busy"
              >
            </label>

            <label>
              <span class="roster-field-head"><span>职位</span></span>
              <input
                v-model="draft.job_title"
                class="input"
                type="text"
                maxlength="40"
                placeholder="头衔，比如领班"
                :disabled="busy"
              >
            </label>

            <label title="登录账号，员工本人能在「我的」里改">
              <span class="roster-field-head"><span>手机号</span></span>
              <span class="roster-readonly is-mono">{{ drawerRow.phone }}</span>
            </label>

            <label title="员工自己注册的时间">
              <span class="roster-field-head"><span>注册时间</span></span>
              <span class="roster-readonly">{{ createdText }}</span>
            </label>

            <label title="明文保存，用于核对证件">
              <span class="roster-field-head">
                <span>身份证号</span>
                <span v-if="!String(draft.id_card_no || '').trim()" class="roster-field-note">待补</span>
              </span>
              <input
                v-model="draft.id_card_no"
                class="input"
                type="text"
                maxlength="18"
                autocomplete="off"
                placeholder="18 位身份证号"
                :disabled="busy"
              >
            </label>

            <label title="按证件上的日期填，有效期一年">
              <span class="roster-field-head">
                <span>健康证办理日期</span>
                <span v-if="!draft.health_cert_date" class="roster-field-note">待补</span>
              </span>
              <input
                v-model="draft.health_cert_date"
                class="input"
                type="date"
                :max="todayDate"
                :disabled="busy"
              >
              <span class="roster-expiry">有效期至 {{ expiresText }}</span>
            </label>

            <label title="不发给员工端，只在管理端显示">
              <span class="roster-field-head">
                <span>底薪</span>
                <span v-if="missingSalary" class="roster-field-note">待补</span>
              </span>
              <span class="roster-input-suffix">
                <input
                  v-model="draft.base_salary"
                  class="input"
                  type="number"
                  inputmode="numeric"
                  min="0"
                  max="999999"
                  step="1"
                  :disabled="busy"
                >
                <span class="roster-suffix">元/月</span>
              </span>
            </label>

            <label title="由超级管理员补录">
              <span class="roster-field-head">
                <span>入职日期</span>
                <span v-if="missingHireDate" class="roster-field-note">待补</span>
              </span>
              <input
                v-model="draft.hire_date"
                class="input"
                type="date"
                :disabled="busy"
              >
            </label>

            <label title="满一年 100、第 10 年 1000 封顶；「应为」是照入职日期算出来的对照，改不改由你定">
              <span class="roster-field-head">
                <span>工龄奖</span>
                <span v-if="seniorityNote" class="roster-field-note">{{ seniorityNote }}</span>
              </span>
              <span class="roster-input-suffix">
                <input
                  v-model="draft.seniority_bonus"
                  class="input"
                  type="number"
                  inputmode="numeric"
                  min="0"
                  max="999999"
                  step="1"
                  :disabled="busy"
                >
                <span class="roster-suffix">元/月</span>
              </span>
            </label>

            <label title="由管理权限派生，不能直接改">
              <span class="roster-field-head"><span>卫生权限</span></span>
              <span class="roster-readonly">{{ hygienePermissionLabel(drawerRow.permission) }}</span>
            </label>

            <fieldset class="roster-caps">
              <legend class="roster-caps-title">管理权限</legend>
              <div class="roster-caps-grid">
                <label
                  v-for="cap in ADMIN_CAP_STAFF_DEFS"
                  :key="cap.key"
                  class="roster-cap"
                  :title="cap.note"
                >
                  <input
                    v-model="draft.admin_caps"
                    type="checkbox"
                    :value="cap.key"
                    :disabled="busy"
                  >
                  <span>{{ cap.label }}</span>
                </label>
              </div>
            </fieldset>
          </div>
        </div>

        <div class="roster-drawer-foot">
          <p v-if="gateNote" class="roster-field-note">{{ gateNote }}</p>
          <div class="roster-foot-actions">
            <button
              v-if="!drawerRow.disabled"
              type="button"
              class="btn btn-danger roster-danger"
              :disabled="busy"
              @click="askDisable"
            >停用</button>
            <button
              type="button"
              class="btn roster-save"
              :class="{ 'btn-primary': primaryAction === 'save' }"
              :disabled="busy || !dirty"
              :title="dirty ? undefined : '没有改动'"
              @click="saveDraft"
            >{{ busy ? '保存中…' : '保存' }}</button>
            <button
              v-if="!drawerRow.approved && !drawerRow.disabled"
              type="button"
              class="btn roster-primary"
              :class="{ 'btn-primary': primaryAction === 'approve' }"
              :disabled="busy || !canApprove"
              :title="canApprove ? undefined : gateNote"
              @click="approveRow"
            >批准</button>
            <button
              v-if="drawerRow.disabled"
              type="button"
              class="btn roster-primary"
              :class="{ 'btn-primary': primaryAction === 'enable' }"
              :disabled="busy"
              @click="enableRow"
            >启用</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 邀请店员弹层：二维码搬进这里（页面上不再有独立的入口卡片）。 -->
    <div v-if="inviteOpen" class="modal-overlay roster-invite-overlay" @click.self="closeInvite">
      <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="roster-invite-title">
        <div class="modal-header">
          <h3 id="roster-invite-title">邀请店员</h3>
          <button type="button" class="btn btn-sm" aria-label="关闭" @click="closeInvite">关闭</button>
        </div>
        <div class="staff-entry">
          <canvas ref="qrCanvas" class="staff-entry-qr" role="img" aria-label="员工入口二维码"></canvas>
          <div class="staff-entry-copy">
            <code>{{ inviteUrl }}</code>
            <button
              type="button"
              class="btn"
              :title="inviteCopyTitle || undefined"
              @click="copyInvite"
            >{{ inviteCopied ? '已复制' : '复制链接' }}</button>
          </div>
        </div>
        <p class="editor-lead">扫这个码自助注册，批准后登录</p>
      </div>
    </div>

    <ConfirmDialog
      v-if="disableTarget"
      class="roster-dialog"
      title="停用员工"
      :message="`停用 ${disableTarget.name || disableTarget.phone} 后不能登录，记录还在。`"
      confirm-label="停用"
      danger
      @confirm="confirmDisable"
      @cancel="disableTarget = null"
    />

    <ConfirmDialog
      v-if="discardOpen"
      class="roster-dialog"
      title="放弃改动"
      message="这次没保存的改动会丢掉。"
      confirm-label="放弃"
      @cancel="discardOpen = false"
      @confirm="confirmDiscard"
    />
  </div>
</template>

<style scoped>
/* 三个浮层都是 `.roster-page` 的直接子元素，共享表给直接子元素挂了 `hy-rise`：
   那个动画的 `transform`（`both` 保留到最终帧）会让内部 `position: fixed` 相对卡片
   定位而不是视口 —— 抽屉会滑进内容流里、确认框会掉到视口外。这里统一摘掉。 */
.roster-page > .modal-overlay {
  animation: none;
}
/* 确认框（停用 / 放弃改动）是**叠在抽屉之上**的一层：抽屉自己的遮罩 `z-index: 120`，
   确认框必须更高 —— 手机上抽屉是全屏的，低一层就等于整个被盖住、点不到。 */
.roster-page > .roster-dialog {
  z-index: 130;
}

/* ── 页头工具组 ─────────────────────────────────────────────────────── */
.roster-page .roster-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: .5rem;
}
.roster-page .roster-search {
  position: relative;
  display: flex;
  align-items: center;
  min-width: 0;
}
.roster-page .roster-search .input {
  width: 100%;
  padding-right: 2.4rem;
}
.roster-page .roster-search-clear {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  display: grid;
  place-items: center;
  width: 2.4rem;
  min-height: 40px;
  border: 0;
  background: transparent;
  color: var(--hy-muted);
  font: inherit;
  font-size: .9rem;
  cursor: pointer;
}
.roster-page .roster-search-clear:hover { color: var(--hy-ink); }

.roster-page .roster-seg {
  display: flex;
  align-items: center;
  gap: .2rem;
  padding: .18rem;
  border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-sm);
  background: var(--hy-surface-2);
}
.roster-page .roster-seg-item {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: .3rem;
  min-height: 40px;
  padding: 0 .7rem;
  border: 1px solid transparent;
  border-radius: var(--hy-radius-sm);
  background: transparent;
  color: var(--hy-muted);
  font: inherit;
  font-size: .8rem;
  white-space: nowrap;
  cursor: pointer;
  transition: color .18s var(--hy-ease), background .18s var(--hy-ease), border-color .18s var(--hy-ease);
}
.roster-page .roster-seg-item:hover { color: var(--hy-ink); }
.roster-page .roster-seg-item.is-on {
  background: var(--hy-mint-soft);
  border-color: var(--hy-mint-line);
  color: var(--hy-mint);
}

.roster-page .roster-tools-actions {
  display: flex;
  align-items: center;
  gap: .5rem;
}
.roster-page .roster-tools-actions .btn { min-height: 40px; }

/* ── 列表行 ─────────────────────────────────────────────────────────── */
/* 姓名 + 手机号那一格：竖排两行（共享表里那两套样式挂在 `.hy-person-top` 下，
   改版后行是 grid、不再是 `.hy-person-top` 的两段结构，所以在这里补一份）。 */
.roster-page .roster-person-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.roster-page .roster-person-copy strong {
  font-family: var(--font-song);
  font-size: 1.02rem;
  font-weight: 700;
}
.roster-page .roster-person-copy span { font-family: var(--font-mono); }

.roster-page .roster-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: .8rem;
  width: 100%;
  min-height: 56px;
  font: inherit;
  color: var(--hy-ink);
  text-align: left;
  cursor: pointer;
}
.roster-page .roster-row-meta {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: .3rem;
  min-width: 0;
}
.roster-page .roster-row-tail {
  display: inline-flex;
  align-items: center;
  gap: .5rem;
}
.roster-page .roster-row-go {
  color: var(--hy-faint);
  font-size: 1.05rem;
  line-height: 1;
}
.roster-page .roster-chip {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: .15rem .5rem;
  border: 1px solid var(--hy-line);
  border-radius: 999px;
  background: var(--hy-surface-3);
  color: var(--hy-muted);
  font-size: .7rem;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.roster-page .roster-chip.is-warn {
  background: var(--hy-amber-soft);
  border-color: var(--hy-amber-line);
  color: var(--hy-amber);
}
.roster-page .roster-chip.is-danger {
  background: var(--hy-seal-soft);
  border-color: var(--hy-seal-line);
  color: var(--hy-seal-bright);
}

/* ── 抽屉 ───────────────────────────────────────────────────────────── */
/* 遮罩要盖掉 theme.css 的居中：拉伸 + 靠右。父级限定抬特异度，不用 `!important`。 */
.roster-page .roster-drawer-overlay {
  align-items: stretch;
  justify-content: flex-end;
  padding: 0;
  z-index: 120;
  touch-action: none;
}
.roster-page .roster-drawer {
  display: flex;
  flex-direction: column;
  width: min(480px, 100vw);
  height: 100dvh;
  max-height: 100dvh;
  background: var(--hy-surface);
  border-left: 1px solid var(--hy-line-strong);
  border-radius: var(--hy-radius-lg) 0 0 var(--hy-radius-lg);
  box-shadow: var(--hy-shadow-md);
  touch-action: auto;
  overscroll-behavior: contain;
  animation: roster-slide .22s var(--hy-ease) both;
}
.roster-page .roster-drawer-head {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: .6rem;
  padding: .85rem 1rem;
  border-bottom: 1px solid var(--hy-line);
  background: var(--hy-surface);
}
.roster-page .roster-drawer-title {
  margin: 0;
  min-width: 0;
  font-family: var(--font-song);
  font-size: 1rem;
  font-weight: 700;
  color: var(--hy-ink);
  overflow-wrap: anywhere;
}
.roster-page .roster-drawer-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 1rem;
}
.roster-page .roster-drawer-foot {
  position: sticky;
  bottom: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: .5rem;
  padding: .7rem 1rem calc(.7rem + env(safe-area-inset-bottom));
  border-top: 1px solid var(--hy-line);
  background: var(--hy-surface);
}
.roster-page .roster-drawer-foot .roster-field-note { margin-right: auto; }
.roster-page .roster-foot-actions {
  display: flex;
  flex-wrap: wrap;
  gap: .5rem;
}
.roster-page .roster-readonly {
  display: inline-flex;
  align-items: center;
  min-height: 38px;
  padding: 0 .7rem;
  border: 1px dashed var(--hy-line);
  border-radius: var(--hy-radius-sm);
  background: var(--hy-surface-2);
  color: var(--hy-ink);
  font-weight: 400;
}
.roster-page .roster-readonly.is-mono { font-family: var(--font-mono); }
.roster-page .roster-field-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: .35rem;
}
.roster-page .roster-field-note {
  color: var(--hy-amber);
  font-size: .72rem;
  font-weight: 600;
}
.roster-page .roster-expiry {
  font-size: .72rem;
  font-weight: 400;
  color: var(--hy-faint);
}
.roster-page .roster-input-suffix {
  display: flex;
  align-items: center;
  gap: .4rem;
}
.roster-page .roster-input-suffix .input { flex: 1 1 auto; min-width: 0; }
.roster-page .roster-suffix {
  flex: 0 0 auto;
  font-size: .76rem;
  font-weight: 400;
  color: var(--hy-muted);
}

/* 管理权限：三项可勾（员工端真有执行点的那些）。 */
.roster-page .roster-caps {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
  display: flex;
  flex-direction: column;
  gap: .35rem;
}
.roster-page .roster-caps-title {
  padding: 0;
  font-size: .76rem;
  font-weight: 600;
  color: var(--hy-muted);
}
.roster-page .roster-caps-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(8rem, 1fr));
  gap: .15rem .7rem;
  max-width: 44rem;
}
.roster-page .hy-person-fields .roster-cap {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: .4rem;
  min-height: 34px;
  font-weight: 500;
  color: var(--hy-ink);
  cursor: pointer;
}
.roster-page .roster-cap input {
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--hy-mint);
}
.roster-page .roster-cap span { min-width: 0; }
.roster-page .roster-cap input:disabled { cursor: not-allowed; }
.roster-page .roster-cap:has(input:disabled) { cursor: not-allowed; color: var(--hy-faint); }

/* 保存成功的一次性反馈（`role="status"`）：与 `roster-error` 同一个位置与尺寸，颜色相反。 */
.roster-page .roster-saved {
  margin: 0 0 .6rem;
  font-size: .8rem;
  color: var(--hy-jade);
}
.roster-page .roster-empty .btn { margin-left: .6rem; }

@keyframes roster-slide {
  from { transform: translateX(16px); opacity: .6; }
  to { transform: translateX(0); opacity: 1; }
}

/* ── 桌面（>720px） ─────────────────────────────────────────────────── */
@media (min-width: 721px) {
  .roster-page .roster-search { width: 15rem; }
}

/* ── 手机（≤720px）：工具栏竖排、行两行、抽屉全屏、触控 ≥44 ─────────── */
@media (max-width: 720px) {
  .roster-page .roster-tools {
    flex-direction: column;
    align-items: stretch;
    width: 100%;
  }
  .roster-page .roster-search { width: 100%; }
  .roster-page .roster-search-clear { width: 44px; min-height: 44px; }
  .roster-page .roster-seg {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    padding: .2rem;
  }
  .roster-page .roster-seg-item { min-height: 44px; padding: 0 .4rem; }
  .roster-page .roster-tools-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .roster-page .roster-tools-actions .btn { min-height: 44px; }

  .roster-page .roster-row {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: start;
    gap: .45rem .6rem;
    min-height: 64px;
    padding: .85rem .9rem;
  }
  .roster-page .roster-row .roster-person-copy { grid-column: 1; }
  .roster-page .roster-row .roster-row-meta {
    grid-column: 1 / -1;
    justify-content: flex-start;
  }
  .roster-page .roster-row .roster-row-tail { grid-column: 2; grid-row: 1; }

  /* 抽屉全屏：头部吸顶、动作条吸底。 */
  .roster-page .roster-drawer {
    width: 100vw;
    border-left: 0;
    border-radius: 0;
  }
  .roster-page .roster-drawer-body,
  .roster-page .roster-drawer-foot {
    padding-left: max(1rem, env(safe-area-inset-left));
    padding-right: max(1rem, env(safe-area-inset-right));
  }
  .roster-page .roster-readonly { min-height: 44px; }
  .roster-page .hy-person-fields .roster-cap { min-height: 44px; }
  /* 危险动作单独一行、跨两列：手机上最不容易误触的排法。 */
  .roster-page .roster-foot-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: .5rem;
    width: 100%;
  }
  .roster-page .roster-foot-actions .btn { width: 100%; min-height: 44px; }
  .roster-page .roster-foot-actions .roster-danger {
    grid-column: 1 / -1;
    grid-row: 2;
  }
  .roster-page .roster-foot-actions .roster-save { grid-column: 1; grid-row: 1; }
  .roster-page .roster-foot-actions .roster-primary { grid-column: 2; grid-row: 1; }
}
</style>
