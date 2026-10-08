<script setup>
// ============================================================================
// 采集节（原「POS 凭据」+「运行配置」两节合并）
//
// 契约（ADR 0099；唯一一份接口定义在 ./sectionContract.js）
//   props  active: Boolean，refreshKey: Number
//   翻成 true 时本节自取数据（当前凭据 + 运行配置）；refreshKey 由壳在「整库恢复」后 +1，
//   用来重取这两份被恢复覆盖过的配置。
//   emits  无 —— 本节不与壳交换状态。
//   inject SETTINGS_MODAL_HOST：只有「清空凭据」用得到，两步确认走 host.requestConfirm(opts, onConfirm)，
//   弹窗由壳渲染。
//
// 文案规则（ADR 0099）：默认态只留操作必需（每条 ≤ 20 字）与风险/后果提示；机制解释
// （存储路径、加密方式、接口字段、为什么这样设计）一律进默认折叠的 <details>。
//
// 反馈归属：每个动作区一条就地提示条，反馈落在**触发它的那个按钮旁边**（见 runSpotted）。
// ============================================================================
import { computed, inject, reactive, watch } from 'vue'
import PanelHeader from '../../components/ui/PanelHeader.vue'
import SvgIcon from '../../components/SvgIcon.vue'
import LuyunCheckbox from '../../components/ui/LuyunCheckbox.vue'
import LuyunNumberInput from '../../components/ui/LuyunNumberInput.vue'
import LuyunTimePicker from '../../components/ui/LuyunTimePicker.vue'
import { usePosCredentials } from '../../composables/usePosCredentials'
import { useRuntimeSettings } from '../../composables/useRuntimeSettings'
import { formatTs } from '../../utils/backupProgress'
import { SETTINGS_MODAL_HOST } from './sectionContract'

const props = defineProps({
  /** 本分节是否为当前分节（壳给）；翻成 true 时自取数据。 */
  active: { type: Boolean, default: false },
  /** 整库恢复之后的刷新计数（壳给）；变化即重取 POS 凭据与运行配置。 */
  refreshKey: { type: Number, default: 0 },
})

const modalHost = inject(SETTINGS_MODAL_HOST, null)

// ===== 就地反馈条：一个动作区一条 =====
const AUTO_HIDE_MS = 3500

/** 一条提示条的初始状态。 */
function createAlertBar() {
  return { show: false, type: 'info', message: '' }
}

const alert = reactive(createAlertBar()) // POS 凭据主操作区：保存并启用 / 清空凭据 / 刷新 / 表单校验
const verifyAlert = reactive(createAlertBar()) // 验证登录
const discoverAlert = reactive(createAlertBar()) // 从账号拉取门店（含多门店切换）
const parseAlert = reactive(createAlertBar()) // 解析
const runtimeAlert = reactive(createAlertBar()) // 运行配置：保存并生效 / 恢复默认 / 刷新

const ALERT_BARS = {
  pos: alert,
  verify: verifyAlert,
  discover: discoverAlert,
  parse: parseAlert,
  runtime: runtimeAlert,
}
const alertTimers = new Map()

// composable 只吐一条 showAlert(type, message)，分不出是哪个按钮触发的；触发它的调用点
// 知道。所以由调用点（模板里的 runSpotted('discover', …)）把 spot 记上：动作发起前记、
// 落地后还回去（异步动作的提示是在 await 之后才发的，不能当场还）。
let activeSpot = 'pos'

function runSpotted(spot, action) {
  const prev = activeSpot
  activeSpot = spot
  const restore = () => { activeSpot = prev }
  let result
  try {
    result = action()
  } catch (err) {
    restore()
    throw err
  }
  if (result && typeof result.then === 'function') {
    // 用 then(restore, restore) 而不是 finally()：finally() 会再造一个 promise，那个被丢掉的
    // 副本一旦跟着失败就是一条 unhandled rejection；这里两个分支都接住，调用方拿到的还是原 promise。
    result.then(restore, restore)
    return result
  }
  restore()
  return result
}

/** 往某条提示条里写一条反馈；成功提示 3.5s 后自动收起。 */
function writeAlert(name, type, message) {
  const bar = ALERT_BARS[name]
  const timer = alertTimers.get(name)
  if (timer) clearTimeout(timer)
  bar.type = type
  bar.message = message
  bar.show = true
  if (type === 'success') {
    alertTimers.set(name, setTimeout(() => { bar.show = false }, AUTO_HIDE_MS))
  } else {
    alertTimers.delete(name)
  }
}

function showAlert(type, message) {
  writeAlert(ALERT_BARS[activeSpot] ? activeSpot : 'pos', type, message)
}

/**
 * composable 在每个动作开始时调它，清掉上一轮的提示。**成功提示不清**：保存成功后
 * composable 紧接着还会再调一次（onSubmitCred 的 showAlert('success') 之后是 fetchCurrent()，
 * 而 fetchCurrent 开头就 clearAlert），一刀切会把刚显示的成功提示当场抹掉 —— 原页面就是
 * 这样，保存成功那条绿条只闪一下。「成功提示本来就 3.5s 自动消失」比"清干净"值钱。
 */
function clearAlert() {
  for (const [name, bar] of Object.entries(ALERT_BARS)) {
    if (bar.show && bar.type === 'success') continue
    bar.show = false
    const timer = alertTimers.get(name)
    if (timer) {
      clearTimeout(timer)
      alertTimers.delete(name)
    }
  }
}

const {
  configured,
  credForm,
  phonePlaceholder,
  showPassword,
  verifying,
  discoveringShops,
  discoveredShops,
  saving,
  togglePwdLabel,
  verifyBtnLabel,
  discoverBtnLabel,
  saveBtnLabel,
  metaItems,
  resetVerifiedSignature,
  fetchCurrent,
  onParseUrl,
  onDiscoverShops,
  onPickDiscoveredShop,
  togglePasswordVisibility,
  onVerify,
  onSubmitCred,
  onClearCredentials,
} = usePosCredentials({ showAlert, clearAlert })

const {
  runtimeForm,
  runtimeLoading,
  runtimeSaving,
  runtimeUpdatedAt,
  runtimeSaveLabel,
  loadRuntimeSettings,
  saveRuntimeSettings,
  resetRuntimeDefaults,
} = useRuntimeSettings({ showAlert, clearAlert })

const posPill = computed(() => (configured.value
  ? { tone: 'ok', label: '凭据已配置' }
  : { tone: 'warn', label: '凭据未配置' }))

// 同一事实只出现一处：手机号 / shop_id / company_id / 门店名称 / delivery_shop_id 都只在
// 表单里（手机号在输入框的占位提示里），facts 只留只读元信息。
const READONLY_FACT_KEYS = new Set(['更新时间'])
const posFacts = computed(() => (metaItems.value || [])
  .filter(([k]) => READONLY_FACT_KEYS.has(k))
  .map(([k, v]) => ({ k, v })))

const runtimePill = computed(() => (runtimeUpdatedAt.value
  ? { tone: 'info', label: '已自定义' }
  : { tone: 'neutral', label: '使用默认值' }))

// 运行配置同理：营业时段 / 轮询间隔 / 浏览器模式都是表单里的值，facts 不复述它们，
// 只留只读的"上次保存"。
const runtimeFacts = computed(() => [
  { k: '上次保存', v: formatTs(runtimeUpdatedAt.value) || '尚未自定义' },
])

/** 删凭据：清空后爬虫待机，属于不可逆动作，先走壳的两步确认再执行。 */
function onClearCredentialsConfirm() {
  modalHost?.requestConfirm(
    {
      title: '确认清空 POS 凭据',
      message: '清空后爬虫进入待机，直到下次保存新凭据；已采集的数据不受影响。',
      details: ['本机加密凭据文件会被删除', '需要重新填写手机号与密码才能恢复采集'],
      danger: true,
      confirmLabel: '清空凭据',
      checkboxes: [{ key: 'clear', label: '我确认清空当前 POS 凭据', required: true }],
    },
    onClearCredentials,
  )
}

/** 恢复默认只改表单、不落库：就近说清楚"还没保存"，否则用户以为已经生效。 */
function onResetRuntimeDefaults() {
  resetRuntimeDefaults()
  writeAlert('runtime', 'info', '已填入默认值，保存后生效。')
}

// 自取数据：原 switchSection 的 pos / runtime 两支。
watch(
  () => props.active,
  (on) => {
    if (!on) return
    runSpotted('pos', fetchCurrent)
    runSpotted('runtime', loadRuntimeSettings)
  },
  { immediate: true },
)

// 整库恢复之后：凭据与运行配置可能已被覆盖，重取一次（原 onAfterRollback 的两步）。
watch(() => props.refreshKey, () => {
  runSpotted('pos', fetchCurrent)
  runSpotted('runtime', loadRuntimeSettings)
})
</script>

<template>
  <div class="settings-section">
    <PanelHeader
      icon="store"
      title="POS 凭据"
      :tone="posPill.tone"
      :pill-label="posPill.label"
      :facts="posFacts"
    >
      <template #actions>
        <button type="button" class="btn" :disabled="verifying" @click="runSpotted('verify', onVerify)">{{ verifyBtnLabel }}</button>
      </template>
    </PanelHeader>
    <div v-if="verifyAlert.show" class="alert show" :class="verifyAlert.type">{{ verifyAlert.message }}</div>

    <form autocomplete="off" @submit.prevent="runSpotted('pos', onSubmitCred)">
      <fieldset>
        <legend>账号信息</legend>
        <div class="grid">
          <div>
            <label for="phone">登录手机号</label>
            <input
              class="input"
              id="phone"
              v-model="credForm.phone"
              type="text"
              inputmode="numeric"
              autocomplete="off"
              :placeholder="phonePlaceholder"
              @input="resetVerifiedSignature"
            >
          </div>
          <div>
            <label for="password">登录密码</label>
            <div class="password-row">
              <input
                class="input"
                id="password"
                v-model="credForm.password"
                :type="showPassword ? 'text' : 'password'"
                autocomplete="new-password"
                placeholder="若无变化可留空，将沿用原密码"
                @input="resetVerifiedSignature"
              >
              <button type="button" class="toggle" @click="togglePasswordVisibility">{{ togglePwdLabel }}</button>
            </div>
          </div>
        </div>
        <p class="hint">龙管家 2.0 App 登录账号，不是网页版</p>
      </fieldset>

      <fieldset>
        <legend>门店信息</legend>
        <div class="grid">
          <div class="full">
            <div class="field-block">
              <div class="field-block__head">
                <span class="field-block__title">从龙管家 2.0 账号拉取（推荐）</span>
                <button
                  type="button"
                  class="btn btn-sm"
                  :disabled="discoveringShops"
                  @click="runSpotted('discover', onDiscoverShops)"
                >{{ discoverBtnLabel }}</button>
              </div>
              <div v-if="discoverAlert.show" class="alert show" :class="discoverAlert.type">{{ discoverAlert.message }}</div>
              <p class="hint">未配置凭据时，需先填手机号与密码。</p>
              <div v-if="discoveredShops.length > 1" class="field-block__pick">
                <label for="discoveredShopPick">多个门店时选择</label>
                <select id="discoveredShopPick" class="input" @change="runSpotted('discover', () => onPickDiscoveredShop($event))">
                  <option
                    v-for="(shop, idx) in discoveredShops"
                    :key="`${shop.shop_id}-${shop.company_id}`"
                    :value="idx"
                  >
                    {{ shop.shop_name || shop.shop_id }} — shop_id={{ shop.shop_id }}, company_id={{ shop.company_id }}
                  </option>
                </select>
              </div>
            </div>
          </div>
          <div class="full">
            <div class="field-block">
              <div class="field-block__head">
                <span class="field-block__title">从报表 URL 解析（可选）</span>
                <button type="button" class="btn btn-sm" @click="runSpotted('parse', onParseUrl)">解析</button>
              </div>
              <div v-if="parseAlert.show" class="alert show" :class="parseAlert.type">{{ parseAlert.message }}</div>
              <input
                class="input"
                id="targetUrl"
                v-model="credForm.targetUrl"
                type="url"
                placeholder="https://cy7mm.wuuxiang.com/home/tableList/1/100001/200002?shopName=..."
              >
              <p class="hint">粘贴 App 内「实时桌态」页的完整地址。</p>
            </div>
          </div>
          <div>
            <label for="shopId">shop_id</label>
            <input class="input" id="shopId" v-model="credForm.shopId" type="text" inputmode="numeric" placeholder="例如 100001" @input="resetVerifiedSignature">
          </div>
          <div>
            <label for="companyId">company_id</label>
            <input class="input" id="companyId" v-model="credForm.companyId" type="text" inputmode="numeric" placeholder="例如 200002" @input="resetVerifiedSignature">
          </div>
          <div class="full">
            <label for="shopName">门店名称</label>
            <input class="input" id="shopName" v-model="credForm.shopName" type="text" placeholder="例如 LuckIn" @input="resetVerifiedSignature">
          </div>
          <div>
            <label for="deliveryShopId">delivery_shop_id</label>
            <input class="input" id="deliveryShopId" v-model="credForm.deliveryShopId" type="text" inputmode="numeric" placeholder="留空则与 company_id 相同" @input="resetVerifiedSignature">
          </div>
        </div>
      </fieldset>

      <details class="section-help">
        <summary>
          <SvgIcon name="chevron-right" :size="14" class="section-help__icon" />
          <span>凭据与门店字段说明</span>
        </summary>
        <ul class="section-help__list">
          <li>「验证登录」只做一次真实登录探测、不写入凭据；「保存并启用」才把当前填写内容落盘。</li>
          <li>手机号与密码是龙管家 <strong>2.0 App</strong> 账号（不是 cy7mm 网页 1.0 账号），Fernet 加密保存在本机 <code>data/credentials.enc</code>，编辑时不回显密码。</li>
          <li>「从账号拉取门店」自动获取 <code>shop_id</code> / <code>company_id</code> / 店名，无需浏览器登录 cy7mm。</li>
          <li>URL 解析：若已在 App 内打开「报表 → 实时桌态 → 占用桌台」，可复制 WebView 地址栏的完整 URL 粘贴解析；勿使用带 <code>{shopId}</code> 的占位模板。</li>
          <li>字段来源：<code>shop_id</code> = URL 第 1 段（centerId）；<code>company_id</code> = URL 第 2 段；<code>shopName</code> 参数即门店名称；<code>delivery_shop_id</code> 是已结账单 / 外卖订单接口里 <code>shopId</code> 与 <code>shops</code> 字段使用的 ID，通常等于 <code>company_id</code>。</li>
        </ul>
      </details>

      <div v-if="alert.show" class="alert show" :class="alert.type">{{ alert.message }}</div>
      <div class="actions is-start">
        <button type="button" class="btn" @click="runSpotted('pos', fetchCurrent)">刷新</button>
        <button type="submit" class="btn btn-primary" :disabled="saving">{{ saveBtnLabel }}</button>
        <!-- 破坏性操作放最后，离主操作远一点 -->
        <button
          v-if="configured"
          type="button"
          class="btn btn-danger"
          @click="onClearCredentialsConfirm"
        >清空凭据</button>
      </div>
    </form>

    <PanelHeader
      icon="timer"
      title="运行配置"
      :tone="runtimePill.tone"
      :pill-label="runtimePill.label"
      :facts="runtimeFacts"
    />

    <fieldset>
      <legend>营业时段</legend>
      <div class="grid">
        <div>
          <label for="workStart">营业开始时间</label>
          <LuyunTimePicker id="workStart" v-model="runtimeForm.work_start" />
        </div>
        <div>
          <label for="workEnd">营业结束时间</label>
          <LuyunTimePicker id="workEnd" v-model="runtimeForm.work_end" />
        </div>
      </div>
      <div class="hint">开始须早于结束（不支持跨零点）。</div>
      <div class="hint">非营业时段自动暂停采集。</div>
    </fieldset>

    <fieldset>
      <legend>采集频率</legend>
      <div class="grid">
        <div>
          <label for="intervalMin">轮询间隔下限（秒）</label>
          <LuyunNumberInput id="intervalMin" v-model="runtimeForm.interval_min" :min="1" :max="3600" />
        </div>
        <div>
          <label for="intervalMax">轮询间隔上限（秒）</label>
          <LuyunNumberInput id="intervalMax" v-model="runtimeForm.interval_max" :min="1" :max="3600" />
        </div>
      </div>
      <div class="hint">实际间隔在上下限之间随机取值。</div>
    </fieldset>

    <fieldset>
      <legend>高级选项</legend>
      <div class="grid">
        <div>
          <span class="field-label">浏览器无头模式</span>
          <label class="luyun-check-row">
            <LuyunCheckbox v-model="runtimeForm.headless" />
            <span>{{ runtimeForm.headless ? '开启（后台运行）' : '关闭（显示窗口，调试用）' }}</span>
          </label>
        </div>
        <div>
          <label for="retryCount">失败重试次数</label>
          <LuyunNumberInput id="retryCount" v-model="runtimeForm.retry_count" :min="0" :max="10" />
        </div>
        <div>
          <label for="timeoutMs">超时（毫秒）</label>
          <LuyunNumberInput id="timeoutMs" v-model="runtimeForm.timeout_ms" :min="1000" :max="300000" :step="1000" />
        </div>
        <div>
          <label for="deliveryCancelMiss">外卖取消判定次数</label>
          <LuyunNumberInput id="deliveryCancelMiss" v-model="runtimeForm.delivery_cancel_miss_threshold" :min="1" :max="20" />
          <div class="hint">连续缺席几次采集判为取消，越大越保守。</div>
        </div>
      </div>
    </fieldset>

    <details class="section-help">
      <summary>
        <SvgIcon name="chevron-right" :size="14" class="section-help__icon" />
        <span>采集机制说明</span>
      </summary>
      <ul class="section-help__list">
        <li>每轮采集结束后在上下限之间随机等待，降低对 POS 站点的规律性压力。</li>
        <li>运行配置存在数据库里，保存即热生效，无需重启服务。</li>
      </ul>
    </details>

    <div v-if="runtimeAlert.show" class="alert show" :class="runtimeAlert.type">{{ runtimeAlert.message }}</div>
    <div class="actions is-start">
      <button type="button" class="btn" :disabled="runtimeLoading" @click="runSpotted('runtime', loadRuntimeSettings)">刷新</button>
      <button type="button" class="btn" @click="onResetRuntimeDefaults">恢复默认</button>
      <button type="button" class="btn btn-primary" :disabled="runtimeSaving" @click="runSpotted('runtime', saveRuntimeSettings)">{{ runtimeSaveLabel }}</button>
    </div>
  </div>
</template>

<style scoped>
/* 提示条就在触发它的按钮旁边，所以上下留白收得比页面级 alert 紧。 */
.alert {
  padding: 8px 12px; border-radius: 8px; font-size: 12px; line-height: 1.6;
  margin: 8px 0; overflow-wrap: anywhere;
}
.alert.success { background: rgba(34, 197, 94, 0.12); border: 1px solid rgba(34, 197, 94, 0.3); color: #86efac; }
.alert.error { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.alert.info { background: rgba(59, 130, 246, 0.10); border: 1px solid rgba(59, 130, 246, 0.25); color: #93c5fd; }

fieldset {
  border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px 18px; margin-bottom: 16px;
  background: rgba(10, 13, 22, 0.5);
}
legend { font-size: 12px; font-weight: 700; color: var(--text-dim); padding: 0 8px; letter-spacing: 0.5px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 14px; }
.grid .full { grid-column: 1 / -1; }
.grid .input { width: 100%; }
label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 5px; font-weight: 600; }
.field-label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 5px; font-weight: 600; }
.hint { font-size: 11px; color: var(--text-dim); margin-top: 4px; line-height: 1.5; opacity: 0.85; }

.password-row { position: relative; }
.password-row .input { padding-right: 64px; }
.password-row .toggle {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: transparent; border: none; color: var(--text-dim);
  font-size: 11px; cursor: pointer; padding: 4px 8px;
}
.password-row .toggle:hover { color: var(--text); }
.actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 8px; flex-wrap: wrap; }
/* 备份 / 更新面板里的操作区一律左对齐，和上方表单对齐。 */
.actions.is-start { justify-content: flex-start; }
/* 窄屏下宁可换行也不把按钮文字挤成两行（390px 实测：不禁止折行时"保存并启用"会被折成两行）。 */
.actions .btn, .field-block__head .btn { white-space: nowrap; }
.field-block {
  padding: 10px 12px; border-radius: 8px;
  background: rgba(10, 13, 22, 0.5); border: 1px solid var(--border);
}
.field-block__head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.field-block__title { font-size: 12px; font-weight: 600; color: var(--text); }
.field-block__head .btn { margin-left: auto; }
.field-block .hint { margin-top: 6px; }
.field-block .input { margin-top: 2px; }
.field-block__pick { margin-top: 8px; }
.field-block__pick label { margin-bottom: 4px; }

/* 机制解释的折叠块：默认收起，皮与系统节的 .section-help 同一套（chevron 图标 + 展开旋转 90°）。 */
.section-help { margin-top: 10px; }
.section-help > summary {
  display: flex; align-items: center; gap: 6px; min-height: 32px;
  cursor: pointer; font-size: 12px; font-weight: 600; color: var(--text-dim);
  list-style: none;
}
.section-help > summary::-webkit-details-marker { display: none; }
.section-help > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.section-help__icon { color: var(--text-dim); transition: transform 0.15s ease; }
.section-help[open] > summary,
.section-help[open] .section-help__icon { color: var(--text); }
.section-help[open] .section-help__icon { transform: rotate(90deg); }
.section-help__list {
  margin: 0; padding-left: 18px;
  font-size: 11px; line-height: 1.7; color: var(--text-dim);
  max-width: 76ch;
}
.section-help__list li { margin-bottom: 4px; overflow-wrap: anywhere; }

@media (prefers-reduced-motion: reduce) {
  .section-help__icon { transition: none; }
}

@media (max-width: 700px) {
  .grid { grid-template-columns: 1fr; }
}

/* 窄屏触控尺寸：折叠区的开合把手与密码框的显隐开关都只有 32/36 与 22/24px 高，
 * 手指点不中。只在 ≤430px 抬到 44 / 40px —— 桌面那一档是鼠标点，密度维持原样。
 * （`.btn` / `.btn-sm` / 勾选行的下限在 styles/theme.css 里，这里只补分节自己的。） */
@media (max-width: 430px) {
  .section-help > summary { min-height: 44px; }
  /* 密码框的「显示 / 隐藏」：绝对定位在输入框右侧，撑高后仍垂直居中。 */
  .password-row .toggle {
    display: inline-flex; align-items: center; justify-content: center;
    min-height: 40px; padding: 0 8px;
  }
}
</style>
