<script setup>
import { nextTick, onMounted, provide, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import LuyunCheckbox from '../components/ui/LuyunCheckbox.vue'
import SvgIcon from '../components/SvgIcon.vue'
import { api } from '../api/client'
import { useEscapeClose } from '../composables/useEscapeClose'
import { createScrollHints } from '../utils/scrollHints'
import { formatTs } from '../utils/backupProgress'
import { createSectionModalHost, SETTINGS_MODAL_HOST } from './settings/sectionContract'
import CollectSection from './settings/CollectSection.vue'
import StatusSection from './settings/StatusSection.vue'
import DataSection from './settings/DataSection.vue'
import SystemSection from './settings/SystemSection.vue'
import AccountSection from './settings/AccountSection.vue'

// ============================================================================
// 系统配置页的壳（ADR 0099）。
//
// 这一层只剩四件事：返回主页、页头的一行状态摘要、5 项分节导航（含 `?section=` 落地与
// 当前项滚进视野）、四个弹窗的「框」与 Esc。7 节并成 5 节的正文都在
// `views/settings/` 下，每个分节自取数据、自己渲染反馈，契约见
// `views/settings/sectionContract.js`（唯一一份接口定义）。
//
// 写接口一律走 api/client.js（自带 credentials:'include' 与 401 处理），
// client.js 已针对 /login、/settings 关闭 401 自动跳转，避免在本页造成重定向死循环。
// ============================================================================

const route = useRoute()
const router = useRouter()

// 配置页是独立全屏页（无主导航壳），返回按钮固定回到主页（仪表盘）。
function goBack() {
  router.push('/')
}

// 分节导航在窄屏（≤700px，见文件末尾的媒体查询）变成一条横向滚动条：溢出提示与
// 「当前项滚进视野」都由
// createScrollHints 统一负责（见它自己的注释：这两条看不见的坑正是 A5 / A6）。
const setupNav = createScrollHints()
const setupNavEl = setupNav.scrollerRef

// 节名一律 ≤ 4 字：390px 下横滑 tab 要能露出后面几节，长名字（原「账号与 API Token」
// 7 个字）会把第 4、5 节整个顶出屏幕。
const SECTIONS = [
  { id: 'collect', label: '采集', icon: 'store' },
  { id: 'status', label: '状态', icon: 'check-circle' },
  { id: 'data', label: '数据', icon: 'database' },
  { id: 'system', label: '系统', icon: 'refresh-cw' },
  { id: 'account', label: '账号', icon: 'key' },
]

// 旧 7 节的 `?section=` 深链继续落地：`components/admin/DataTable.vue` 的「备份 / 迁移」
// 按钮就在用 `?section=backup`。合并分节不该把既有跳转变成"点进去落到别的节"。
const LEGACY_SECTION_ALIASES = {
  pos: 'collect',
  runtime: 'collect',
  health: 'status',
  backup: 'data',
  database: 'data',
  update: 'system',
  account: 'account',
}

function resolveSectionId(raw) {
  if (typeof raw !== 'string') return ''
  if (SECTIONS.some((s) => s.id === raw)) return raw
  return LEGACY_SECTION_ALIASES[raw] || ''
}

const activeSection = ref(resolveSectionId(route.query.section) || 'collect')

function switchSection(id) {
  if (!SECTIONS.some((s) => s.id === id)) return
  activeSection.value = id
  // `flush: 'post'` 式的时机问题在这里用 nextTick 处理：`:class` 的 active 要等这次
  // 渲染落地才换格，滚早了滚的是上一个分节。
  nextTick(() => setupNav.scrollActiveIntoView())
}

// ===== 页头状态摘要：采集状态 · 版本 · 上次备份 =====
// 三个**各自独立**的只读请求：任一端点失败（含 401、断网）或没有数据，这一项就不显示，
// 既不报错也不阻塞渲染 —— 首屏不该因为一个探针 500 而空着或弹错。取数一律走既有端点，
// 不为页头新开接口。
const summaryItems = ref([])

/** 采集状态（GET /api/scraper/status）：凭据、登录、暂停、任务状态四个信号给一句话。 */
function collectSummaryItem(payload) {
  if (!payload) return null
  if (payload.has_credentials === false || payload.login_state === 'no_credentials') {
    return { key: 'collect', text: '采集未配置凭据', tone: 'warn' }
  }
  if (payload.login_state === 'login_failed_cooldown') {
    return { key: 'collect', text: '采集登录失败', tone: 'warn' }
  }
  if (payload.paused) return { key: 'collect', text: '采集已暂停', tone: 'warn' }
  if (payload.status === 'running') return { key: 'collect', text: '采集运行中' }
  return { key: 'collect', text: '采集未运行', tone: 'warn' }
}

/** 摘要里的时间只到分钟：一行三格要放得下，秒没有信息量。 */
function shortTs(value) {
  return formatTs(value).slice(0, 16)
}

async function loadSummary() {
  const [scraper, health, backup] = await Promise.allSettled([
    api.get('/api/scraper/status', null, null, 'no-store'),
    api.get('/api/system/health', null, null, 'no-store'),
    api.get('/api/backup/health', null, null, 'no-store'),
  ])

  const items = []
  if (scraper.status === 'fulfilled') {
    const item = collectSummaryItem(scraper.value)
    if (item) items.push(item)
  }
  if (health.status === 'fulfilled' && health.value?.version) {
    items.push({ key: 'version', text: `版本 ${health.value.version}` })
  }
  if (backup.status === 'fulfilled') {
    const last = backup.value?.health?.last_success_at
    items.push(last
      ? { key: 'backup', text: `上次备份 ${shortTs(last)}` }
      : { key: 'backup', text: '尚无备份', tone: 'warn' })
  }
  summaryItems.value = items
}

// ===== 四个弹窗：框在壳里，状态与文案由分节注册（见 settings/sectionContract.js）=====
const modals = createSectionModalHost()
provide(SETTINGS_MODAL_HOST, modals)

// 四个弹窗的 Esc：**只注册一条**入口，按"谁开着"分派（渲染顺序的最后一个 = 视觉上
// 最上面那个）。为什么不是四个弹窗各注册一条：共享实现 `useEscapeClose` 的栈只让
// **最后注册**的那一格响应（它的注释：关闭态的弹窗不占用栈顶），而本页四个弹窗都是
// 常驻 `v-if` 的固定入口、不是"打开时才挂载"的组件——四条各注册一条的话，栈顶永远
// 是最后那条（Token 弹窗）。这一点在拆壳时实测过：四条各注册一条时，确认框开着按 Esc
// 毫无反应（实测 `confirm_after_esc = 1`，框还挡着导航）；改成下面这一条后归零。
// 一条入口 + 一个 `isOpen` 判据，正是那个共享实现设计的用法。
// 每个弹窗仍各走自己的关闭路径（取消 = 什么都没发生）；唯一例外是「恢复完成」：
// 它没有"取消"语义，Esc 等同于点主按钮（会话已失效时登出）。
const MODAL_ORDER = ['confirm', 'importSuccess', 'dbReset', 'token']

/** 当前开着的那一个（从渲染顺序末端往前找），没有则返回空串。 */
function openModalKind() {
  for (let i = MODAL_ORDER.length - 1; i >= 0; i -= 1) {
    if (modals[MODAL_ORDER[i]]?.isOpen()) return MODAL_ORDER[i]
  }
  return ''
}

useEscapeClose(
  () => Boolean(openModalKind()),
  () => {
    const kind = openModalKind()
    if (kind) modals[kind].close()
  },
)

// 整库恢复之后：采集节的凭据与运行配置已被覆盖，由数据节抛上来、这里翻成刷新计数。
const collectRefresh = ref(0)
function onDataRestored() {
  collectRefresh.value += 1
}

onMounted(() => {
  // `?section=` 落地：初值已经在 setup 里定好（分节挂载时就按它自取数据，不白跑一次
  // 默认节的请求），这里只把当前项滚进视野——`?section=system` 直接进来也得看得见
  // 高亮在哪一格。
  if (route.query.section) {
    nextTick(() => setupNav.scrollActiveIntoView('auto'))
  }
  loadSummary()
})
</script>

<template>
  <div class="setup-page">
    <div class="container">
      <button type="button" class="back-btn" @click="goBack">← 返回主页</button>
      <h1>系统配置</h1>
      <!-- 一行状态摘要：三项各自独立，取不到的项根本不出现（不报错、不占位）。 -->
      <p v-if="summaryItems.length" class="status-summary">
        <template v-for="(item, i) in summaryItems" :key="item.key">
          <span v-if="i" class="status-summary__sep" aria-hidden="true">·</span>
          <span class="status-summary__item" :class="item.tone ? `is-${item.tone}` : ''">{{ item.text }}</span>
        </template>
      </p>

      <div class="setup-body">
        <nav
          ref="setupNavEl"
          class="setup-nav"
          :class="{ 'is-scroll-start': setupNav.atStart.value, 'is-scroll-end': setupNav.atEnd.value }"
        >
          <button
            v-for="s in SECTIONS"
            :key="s.id"
            type="button"
            class="nav-item"
            :class="{ active: activeSection === s.id }"
            @click="switchSection(s.id)"
          >
            <SvgIcon :name="s.icon" :size="14" />
            <span>{{ s.label }}</span>
          </button>
        </nav>

        <div class="setup-content">
          <CollectSection
            v-show="activeSection === 'collect'"
            :active="activeSection === 'collect'"
            :refresh-key="collectRefresh"
          />
          <StatusSection v-show="activeSection === 'status'" :active="activeSection === 'status'" />
          <DataSection
            v-show="activeSection === 'data'"
            :active="activeSection === 'data'"
            @data-restored="onDataRestored"
          />
          <SystemSection v-show="activeSection === 'system'" :active="activeSection === 'system'" />
          <AccountSection v-show="activeSection === 'account'" :active="activeSection === 'account'" />
        </div>
      </div>
    </div>

    <!-- 弹窗宿主：只有壳渲染「框」。四个弹窗的状态与文案在分节里注册进来，
         没有注册的弹窗连框都不存在，所以这里的 `?.` 不是防御性写法而是必要条件。 -->

    <!-- 两步确认：覆盖导入 / 数据回滚 / 清理 / 保存保留配置 / 清空凭据 / 退出 / 撤销 Token -->
    <div v-if="modals.confirm?.isOpen()" class="modal-overlay show" role="dialog" aria-modal="true">
      <div class="modal-box">
        <h3>{{ modals.confirm.state.title }}</h3>
        <p>{{ modals.confirm.state.message }}</p>
        <ul v-if="modals.confirm.state.details.length" class="confirm-details">
          <li v-for="(item, i) in modals.confirm.state.details" :key="i">{{ item }}</li>
        </ul>
        <label
          v-for="box in modals.confirm.state.checkboxes"
          :key="box.key"
          class="luyun-check-row confirm-check"
        >
          <LuyunCheckbox v-model="modals.confirm.checked[box.key]" />
          <span>{{ box.label }}</span>
        </label>
        <div class="actions">
          <button type="button" class="btn" @click="modals.confirm.close()">取消</button>
          <button
            type="button"
            :class="['btn', modals.confirm.state.danger ? 'btn-danger' : 'btn-primary']"
            :disabled="!modals.confirm.ready()"
            @click="modals.confirm.submit()"
          >{{ modals.confirm.state.confirmLabel }}</button>
        </div>
      </div>
    </div>

    <!-- 恢复完成：没有"取消"语义，唯一的按钮就是关闭路径 -->
    <div v-if="modals.importSuccess?.isOpen()" class="modal-overlay show" role="dialog" aria-modal="true">
      <div class="modal-box">
        <h3>{{ modals.importSuccess.copy().title }}</h3>
        <p>{{ modals.importSuccess.state.message }}</p>
        <div class="actions">
          <button type="button" class="btn btn-primary" @click="modals.importSuccess.close()">
            {{ modals.importSuccess.copy().confirmLabel }}
          </button>
        </div>
      </div>
    </div>

    <!-- 重置数据库密码：二次确认必须输入当前后台超级管理员密码，不使用浏览器原生 prompt -->
    <div v-if="modals.dbReset?.isOpen()" class="modal-overlay show" role="dialog" aria-modal="true">
      <div class="modal-box">
        <h3>{{ modals.dbReset.copy().title }}</h3>
        <p>{{ modals.dbReset.copy().message }}</p>
        <div class="grid">
          <div class="full">
            <label for="dbResetPassword">{{ modals.dbReset.copy().fieldLabel }}</label>
            <div class="password-row">
              <input
                class="input"
                id="dbResetPassword"
                v-model="modals.dbReset.state.password"
                :type="modals.dbReset.isPasswordVisible() ? 'text' : 'password'"
                autocomplete="current-password"
                :placeholder="modals.dbReset.copy().placeholder"
                @keyup.enter="modals.dbReset.submit()"
              >
              <button type="button" class="toggle" @click="modals.dbReset.togglePassword()">{{ modals.dbReset.copy().toggleLabel }}</button>
            </div>
          </div>
        </div>
        <div v-if="modals.dbReset.state.error" class="alert error show import-block">{{ modals.dbReset.state.error }}</div>
        <div class="actions">
          <button type="button" class="btn" :disabled="modals.dbReset.busy()" @click="modals.dbReset.close()">取消</button>
          <button type="button" class="btn btn-danger" :disabled="modals.dbReset.busy()" @click="modals.dbReset.submit()">{{ modals.dbReset.copy().confirmLabel }}</button>
        </div>
      </div>
    </div>

    <div v-if="modals.token?.isOpen()" class="modal-overlay show" role="dialog" aria-modal="true">
      <div class="modal-box">
        <h3>{{ modals.token.copy().title }}</h3>
        <p>{{ modals.token.copy().message }}</p>
        <div class="token-display">{{ modals.token.state.plaintext }}</div>
        <div class="actions">
          <button type="button" class="btn" @click="modals.token.copyToken()">{{ modals.token.copy().copyLabel }}</button>
          <button type="button" class="btn btn-primary" @click="modals.token.close()">{{ modals.token.copy().confirmLabel }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.setup-page {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background:
    radial-gradient(circle at 12% 0%, rgba(99, 102, 241, 0.14), transparent 30%),
    radial-gradient(circle at 88% 10%, rgba(6, 182, 212, 0.08), transparent 28%),
    var(--bg);
  color: var(--text);
  min-height: 100vh;
}

.container {
  max-width: 920px;
  margin: 32px auto 80px;
  background: rgba(17, 24, 39, 0.92);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 28px 32px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
}

.back-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  /* 44px 是本页最常用的一个触控目标（每个分节里都在），也是移动端的地板值。 */
  min-height: 44px;
  margin-bottom: 14px; padding: 7px 14px;
  background: var(--card2); border: 1px solid var(--border); border-radius: 7px;
  color: var(--text-dim); font-size: 13px; font-weight: 600;
  cursor: pointer; font-family: inherit; transition: all 0.15s;
}
.back-btn:hover { color: var(--text); border-color: var(--accent); }

h1 { font-size: 20px; margin-bottom: 6px; display: flex; align-items: center; gap: 10px; }

/* 页头一行状态摘要：采集状态 · 版本 · 上次备份（拿不到的那项不渲染）。 */
.status-summary {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px;
  color: var(--text-dim); font-size: 13px; margin-bottom: 18px; line-height: 1.6;
}
.status-summary__sep { color: var(--border); }
.status-summary__item { font-variant-numeric: tabular-nums; }
.status-summary__item.is-warn { color: var(--yellow); }

.alert {
  padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 16px;
}
.alert.success { background: rgba(34, 197, 94, 0.12); border: 1px solid rgba(34, 197, 94, 0.3); color: #86efac; }
.alert.error { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.alert.info { background: rgba(59, 130, 246, 0.10); border: 1px solid rgba(59, 130, 246, 0.25); color: #93c5fd; }

/* 弹窗里那点表单/操作区：与分节里的同名类视觉一致（分节各自带一份自己的）。 */
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 14px; }
.grid .full { grid-column: 1 / -1; }
.grid .input { width: 100%; }
label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 5px; font-weight: 600; }
.actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 8px; flex-wrap: wrap; }
.import-block { margin-top: 12px; }
.grid .luyun-check-row { display: flex; }
.confirm-check { margin-top: 10px; }

.password-row { position: relative; }
.password-row .input { padding-right: 64px; }
.password-row .toggle {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: transparent; border: none; color: var(--text-dim);
  font-size: 11px; cursor: pointer; padding: 4px 8px;
}
.password-row .toggle:hover { color: var(--text); }

.setup-body { display: flex; gap: 24px; align-items: flex-start; }
.setup-nav {
  flex: 0 0 180px; display: flex; flex-direction: column; gap: 4px;
  position: sticky; top: 32px;
}
.setup-nav .nav-item {
  display: inline-flex; align-items: center; gap: 8px;
  text-align: left; padding: 10px 14px; background: transparent;
  border: 1px solid transparent; border-radius: 8px;
  color: var(--text-dim); font-size: 13px; font-weight: 600;
  cursor: pointer; font-family: inherit; transition: all 0.15s;
  /* 触控高度地板值 44px（桌面把侧栏一项撑高一截也没坏处，鼠标点起来更稳）。 */
  min-height: 44px;
}
.setup-nav .nav-item :deep(svg) { opacity: 0.8; }
.setup-nav .nav-item:hover { color: var(--text); background: var(--card2); }
.setup-nav .nav-item.active {
  color: var(--accent); background: rgba(99, 102, 241, 0.12);
  border-color: rgba(99, 102, 241, 0.35);
}
.setup-nav .nav-item.active :deep(svg) { opacity: 1; }
.setup-content { flex: 1 1 auto; min-width: 0; }

.modal-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.65);
  z-index: 1000; display: flex; align-items: center; justify-content: center; padding: 20px;
}
.modal-box {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  padding: 24px; max-width: 520px; width: 100%;
}
.modal-box h3 { font-size: 16px; margin-bottom: 10px; }
.modal-box p { font-size: 13px; color: var(--text-dim); margin-bottom: 14px; line-height: 1.6; }
.token-display {
  background: var(--card2); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px; word-break: break-all; margin-bottom: 16px; user-select: all;
}

.confirm-details {
  margin: 0 0 8px; padding-left: 18px;
  font-size: 12px; color: var(--text-dim); line-height: 1.7;
}
.confirm-details li { margin-bottom: 2px; }

@media (max-width: 700px) {
  .container { padding: 20px 18px; }
  .grid { grid-template-columns: 1fr; }
  /* `align-items: flex-start` 是给桌面那一行的（竖排侧栏与内容顶端对齐）；换成纵向排列后
     它会让每个子项按**内容宽度**排（fit-content），内容比容器宽时子项就撑出容器：320px
     实测数据节被备份总览的 332px min-content 顶成 332，而容器只有 294（文档级
     scrollWidth 仍是 320 —— 有祖先把它裁住了 —— 但分节里 187 个元素越界）。
     改成 stretch，子项回到容器宽度，内容再宽也只在组件内部，不会把分节撑破。 */
  .setup-body { flex-direction: column; gap: 12px; align-items: stretch; }
  .setup-nav {
    flex: none; width: 100%; flex-direction: row; overflow-x: auto;
    /* 横排后 4px 的间隔会把五项顶出 4px（实测 356 > 352）：收到 2px 正好放得下。 */
    gap: 2px;
    /* `relative` 是给两端渐隐的定位基准：桌面档这里是 sticky（本身就定位），
       窄屏改成 static 后伪元素会跑到页面角落去。 */
    position: relative;
    border-bottom: 1px solid var(--border); padding-bottom: 8px;
    /* 分节 5 项在 390px 下仍然会溢出（内容宽 > 可视宽），滚动条要么很粗要么被系统
       藏掉，两种都难看，索性统一隐藏 —— 溢出交给两端的渐隐提示来表达（见下面
       ::before/::after）。`scroll-padding-inline` 让 scrollActiveIntoView 居中后的
       当前项不会被渐隐盖住。 */
    scrollbar-width: none;
    scroll-padding-inline: 24px;
  }
  .setup-nav::-webkit-scrollbar { display: none; }
  /* 五项在 390px 下要能**全部**露出来（A6 的原样：7 项时有 4 项在屏外、看不出还能滚）：
     节名已压到 ≤4 字，这里再把图标与文字的距离、内边距收一档 —— 5 × 66px + 间隙 = 346px
     ≤ 容器内宽 352px。更窄的屏（320/360px）再走下面那条 430px 的规则。 */
  .setup-nav .nav-item { white-space: nowrap; padding: 8px 10px; gap: 6px; }

  /* 溢出提示：只在真的还有内容的那一侧出现。没有它，用户会以为"只有前两节"
     —— 后面的分节全在屏外。 */
  .setup-nav::before,
  .setup-nav::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 8px;
    width: 22px;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .setup-nav::before {
    left: 0;
    background: linear-gradient(to right, rgba(17, 24, 39, 0.95), transparent);
  }
  .setup-nav::after {
    right: 0;
    background: linear-gradient(to left, rgba(17, 24, 39, 0.95), transparent);
  }
  .setup-nav:not(.is-scroll-end)::after { opacity: 1; }
  .setup-nav:not(.is-scroll-start)::before { opacity: 1; }
}

/* 320 / 360px 机型（可视内宽只有 282 / 322px）：
 * 5 项导航加起来 348px，390px 是刚好放下的临界，320 机型上「账号」整个在屏外 —— 而它是
 * 退出登录与 API Token 唯一的入口。这里不动字号、不删节名（两条都是任务里明确否掉的
 * 换宽度方式），只做三件小事：容器左右各让 6px、图标收到 12px、图标与文字的间距与水平
 * 内边距各收 2px。实测 320px 下导航内容 288px ≤ 可视 294px，5 项全部可见、不再需要横滑。
 * 溢出与两端渐隐仍然保留（换更小的屏或用户放大字体时照样会溢出，那时才用得着滚）。 */
@media (max-width: 430px) {
  .container { padding: 18px 12px; }
  .setup-nav .nav-item { padding: 8px 6px; gap: 4px; }
  .setup-nav .nav-item :deep(svg) { width: 12px; height: 12px; }
  /* 密码框的「显示 / 隐藏」在本页有两处同名交互：采集节密码框（在分节里，t13 修过）
     与这个「重置数据库密码」弹窗（在壳里）。两处取同一档位 —— 一个交互两种尺寸，
     用户在不同地方点到的热区不一样。横向 padding 与基础规则一致（只有 4px→0 的纵向
     变化 + min-height），所以按钮宽度、右边缘位置都不变。 */
  .password-row .toggle {
    display: inline-flex; align-items: center; justify-content: center;
    min-height: 40px; padding: 0 8px;
  }
}
</style>
