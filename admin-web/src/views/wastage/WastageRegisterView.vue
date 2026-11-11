<script setup>
/**
 * 员工端的「损耗登记」页（从 worktool2 那个小程序的 pages/wastage/add 搬过来）。
 *
 * 一页回答两件事：**记一笔**（物料 / 原因 / 数量与单位 / 日期 / 最多 3 张现场照片 / 备注），
 * 以及**看自己最近记的几笔**（员工最常问的是「我那天记了没有」），记错了还能当场改或撤回。
 *
 * 两个身份都能进（页面清单里是 audience: both）：员工用员工会话、店长用管理端会话。
 * 接口按**归属**分——员工只看得到自己记的；全店统计与明细在 /workbench/kitchen/loss
 * （管理端，或勾了「损耗分析」能力的员工）。所以这一页上没有任何「看别人的」入口。
 *
 * 三样东西来自服务端，页面不写第二份：GET /api/wastage/meta（六个原因、常用单位、
 * 那四个上限、更正窗口、今天）、/templates（我存过的 + 我最近常这么记的）、/suggestions
 * （填名字时的补全）。日期用站内日期组件（原生 input[type=date] 的显示格式跟着浏览器
 * 语言走，页面控制不了 —— 2026-10-13 用户裁定）。
 *
 * 反馈走页内一句话（不是 toast），而且**必须挨着操作点**：2026-11-07 实测（390×844）提交后
 * 页面停在页底，原来放在页顶的「已记下」与「选了其他要写说明」都在视口之外。所以这一轮
 * 的错误与成功提示统一放在**吸底条里、按钮正上方** —— 那里是员工点提交时唯一一定在视野里的
 * 位置；提交成功后**页面不滚动**（上一版会 scrollIntoView 到记录区，实测连记 5 笔每笔都被
 * 弹走 850px，第 2~5 笔要手动滑回来）。
 *
 * 2026-11-07 这一轮（spec 4.1~4.5）改的七件事：
 * ① 表单重排：物料 / 原因三列两行 / 数量与单位 chips 三项必填在前，照片、备注、日期收成
 *    一行小按钮（点开才展开），首屏一屏看完；单位从原生 select 改成 chips（手机上少一层面板）；
 * ② 提交按钮不再 disabled（只有 busy 时禁用）：点了就地校验、聚焦缺的那一项，按钮文案还会
 *    写着「还差什么」；那几条就地校验从句死代码变成真的会跑的路径；
 * ③ 表单块底部留出吸底条高度，滚到最底时最后一个字段不会被按钮盖住；
 * ④ 记录列表：can_amend 的行多「改」「撤回」两个小按钮 —— 改 = 表单进入编辑态（保存修改 /
 *    取消，照片保留不能换），撤回 = 行内二次确认（3 秒）；
 * ⑤ 模板删除二次确认：点 × 变成「确认删」，3 秒内再点才真删，点别处或超时复位；
 * ⑥ 草稿存 localStorage（luyun.wastage.draft）：现场常被电话/切走打断，回来还在；
 * ⑦ 从备货计划带过来（?from=prep&item=…&unit=…）：预填物料与单位、显示来源，不填数量。
 *    备货品的单位可能是损耗清单里没有的（条 / 底 / 板 / 碗），那种走「其他…」的自定义单位，
 *    不静默丢掉。
 *
 * 上一轮（2026-11-07 审查后）的六处仍然有效：错误就地、手机档吸底、选「其他」备注就地展开、
 * 日期「今天 / 昨天」快捷、物料「最近记过」chips、提交成功后重拉模板且原因不清空。
 *
 * 2026-11-11（用户反馈「现场照片步骤太多、相机要固定占满屏」）：
 * ⑧ 现场照片从「展开照片区 → 点拍一张」两步并成一步：点「＋现场照片 / 再拍」直接开**全屏**
 *    取景浮层（Teleport 到 body、fixed 占满、打开期间锁页面滚动），拍满 3 张自动关；
 *    已拍的缩略图直接列在表单里，可删可补拍。
 *
 * 台账语义（GLOSSARY.md）：损耗记的是**已经发生的事**，24 小时内本人可以更正或撤回，每次
 * 更正留一条 revision；照片是现场证据，改的时候保留、不能换。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
// 页内取景用卫生那套现成的相机（getUserMedia + canvas 出 JPEG）：2026-10-17 用户裁定
// 「像拍卫生照一样，不跳系统相机」。它同时解决了 iPhone 的 HEIC —— 页内相机拍出来直接是
// JPEG；走系统相机拿到的是 HEIC，后端认不出格式会拒（用户真机上撞的就是这个）。
import HygieneLiveCamera from '../../components/hygiene/HygieneLiveCamera.vue'
import { api } from '../../api/client.js'
import { loginRedirectTarget } from '../../utils/loginNext'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { IDENTITY_ADMIN, IDENTITY_STAFF } from '../../utils/workbenchIdentity'

/** 草稿的 localStorage key：跨页面/跨进程都读同一个名字（spec 4.3）。 */
const DRAFT_KEY = 'luyun.wastage.draft'
/** 二次确认（模板删除、记录撤回）的自动复位时间。 */
const CONFIRM_RESET_MS = 3000

const route = useRoute()
const router = useRouter()
const identityStore = useWorkbenchIdentityStore()

const state = ref('loading') // loading | ready | error
const errorText = ref('')

// 服务端给的清单与上限（不在这里写死 3 / 200 / 六个原因）。
const reasons = ref([])
const units = ref([])
const maxImages = ref(3)
const maxItemName = ref(50)
const maxRemark = ref(200)
const maxTemplates = ref(20)
const maxImageBytes = ref(20 * 1024 * 1024)
const amendWindowHours = ref(24)
const today = ref('')

// 表单。
const itemName = ref('')
const quantity = ref('')
const unitChoice = ref('')
const unitCustom = ref('')
const reason = ref('')
const remark = ref('')
/** 可选的「对应备货品」：留空 = 不挂，复盘按品名兜底（0049 的 prep_item_id 可空）。 */
const prepItems = ref([])
const prepItemId = ref('')
/** 「从备货单选」面板（2026-11-07）：物料不一定要手打 —— 备货品主数据里已经有品名和单位，
 *  选中就把两件一起填好，还顺带挂上「对应备货品」的关联。只在新建时给入口。 */
const prepPickerOpen = ref(false)
const prepPickerSearch = ref('')
const prepPickerInput = ref(null)
const day = ref('')
const images = ref([])
const uploading = ref(false)
/** 全屏取景浮层是否打开：点「＋现场照片 / 再拍」一下就到（不跳系统相机），拍满 3 张自动关。 */
const cameraOpen = ref(false)

// 备注 / 日期是选填项，默认收起、点开才展开（spec 4.1.5）。
// 现场照片不再占一个折叠态：点「＋现场照片」直接开全屏相机（2026-11-11）。
const remarkOpen = ref(false)
const dateOpen = ref(false)
/** 「对应备货品」也是选填：不选时复盘按品名兜底（0049 的 prep_item_id 可空）。
 *  2026-11-07 从必填三项中间挪进选填行 —— 它占 70px，会把必填项挤出首屏。 */
const prepSelectOpen = ref(false)

// 就地校验要聚焦的那几个控件。
const formBox = ref(null)
const itemInput = ref(null)
const quantityInput = ref(null)
const unitBox = ref(null)
const reasonBox = ref(null)
const remarkInput = ref(null)
const unitCustomInput = ref(null)

// 反馈落点：错误与成功都放在吸底条里、按钮上方（spec 4.1.6 / 4.1.7）。
const note = ref('')
const formError = ref('')
/** 提交失败时提示「再点一次就是重试」（spec 4.3.2）；就地校验的提示不带这句。 */
const formErrorRetry = ref(false)

// 记录区自己的两条话（撤回/改失败、撤回成功）—— 跟着那几条记录走，不挤占表单的吸底提示。
const listError = ref('')
const listNote = ref('')

/** 刚提交成功那一条的 id：记录区里把那条高亮，员工一眼看到「刚才这笔在」。 */
const lastSavedId = ref(null)

// 编辑态（spec 4.2.2）：editingId 非空时表单改的是这一条。
const editingId = ref(null)
const editingDate = ref('')
const editingImages = ref(0)

// 二次确认（spec 4.2.3 / 4.4）。
const confirmDeleteId = ref(null)
const revokeConfirmId = ref(null)
const revoking = ref(false)
let templateTimer = null
let revokeTimer = null

const templates = ref([])
const suggestions = ref([])
const records = ref([])
const total = ref(0)
const busy = ref(false)

const draftRestored = ref(false)
/** 从备货计划带过来的一句来源提示（spec 4.5）。 */
const prepSource = ref('')
let prefillApplied = false
/** 草稿恢复完之前不许回写：否则 load() 里给 day 赋今天就会把刚读到的草稿清掉。 */
let draftReady = false

/** 单位：选的是 chips 里的一项，__custom 那一档给「罐 / 箱」这种没列出来的。 */
const unit = computed(() =>
  unitChoice.value === '__custom' ? unitCustom.value.trim() : unitChoice.value,
)
const isOther = computed(() => reason.value === 'other')
/** 单位 chips 只摆常用的前 6 个（spec 4.1.3），剩下的一律走「其他…」手输。
 *  例外：当前选中的单位若在清单里、但排在 6 名之后（老记录改的时候、或备货计划带出的
 *  「瓶 / 包 / 斤」），把它补进来 —— 否则 chip 行里没有一颗是选中的，看着像单位没选，
 *  而 unit 又确实有值，提交时人会以为页面坏了。 */
const unitChips = computed(() => {
  const head = units.value.slice(0, 6)
  const current = unitChoice.value
  if (current && current !== '__custom' && units.value.includes(current) && !head.includes(current)) {
    return head.concat([current])
  }
  return head
})
const maxImageMb = computed(() => Math.max(1, Math.round(maxImageBytes.value / (1024 * 1024))))

/** 'YYYY-MM-DD' 加减天数。只做日期算数，不碰时区（页面上的日期都是门店自然日）。 */
function shiftDate(iso, delta) {
  const parts = String(iso || '').split('-').map(Number)
  if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n) || n <= 0)) return iso
  const at = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]))
  at.setUTCDate(at.getUTCDate() + delta)
  return at.toISOString().slice(0, 10)
}

/** 昨天：跨零点以后补记前一晚的损耗时，点一下就对上（最晚仍是今天，服务端那道闸不动）。 */
const yesterday = computed(() => (today.value ? shiftDate(today.value, -1) : ''))

/** 补记提示（F 组）：日期 chip 只写「改日期」，改没改由吸底条在提交按钮上方确认 ——
 *  390 宽选填行放不下「改日期 · 昨天」这种动态文案，横滑会把日期本身切掉；而「记错日期」
 *  恰恰是提交前最该看见的一件事，所以放到按钮旁边。 */
const backdatedNote = computed(() => {
  if (!day.value || !today.value || day.value === today.value) return ''
  const label = day.value === yesterday.value ? '昨天' : ' ' + day.value
  return '这一笔记在' + label + '（补记）'
})

/** 编辑态顶栏那一句「正在改 10-16 那一笔」。 */
const editingDateLabel = computed(() => {
  if (!editingDate.value) return ''
  if (editingDate.value === today.value) return '今天'
  if (editingDate.value === yesterday.value) return '昨天'
  return editingDate.value
})

/** 顶部状态（A2）：今天 / 昨天各几笔，由服务端按归属直接给 counts。
 *  别再从已加载的 8 条里数：凌晨补记前一晚时，页面上会变成「今天已记 0 笔」而下面
 *  正躺着 8 条昨天的记录 —— 两句话互相打架，人会以为登记没生效。 */
const dayCounts = ref({ today: null, yesterday: null })
const statusText = computed(() => {
  const todayTotal = dayCounts.value.today
  const yesterdayTotal = dayCounts.value.yesterday
  if (todayTotal === null || todayTotal === undefined) {
    // 老响应没有 counts：退回从已加载的记录里数，别把页面弄崩。
    const counted = records.value.filter((row) => row.wastage_date === today.value).length
    const capped = records.value.length > 0 && total.value > records.value.length && counted >= records.value.length
    return '今天已记 ' + (capped ? counted + '+' : String(counted)) + ' 笔'
  }
  if (!todayTotal && !yesterdayTotal) return '今天还没有记录'
  return '今天 ' + todayTotal + ' 笔 · 昨天 ' + yesterdayTotal + ' 笔'
})

/** 物料名可以点「最近记过的」直接填：手打「五花肉」三个字比点一下慢得多，而手机上的
 *  datalist 补全在 iOS 上基本不弹（这一页的员工绝大多数拿 iPhone）。 */
const quickItems = computed(() => {
  const current = itemName.value.trim()
  return suggestions.value.filter((row) => row && row.item_name !== current).slice(0, 8)
})

/** 「从备货单选」的列表：按名字搜索（员工记得住「餐包」，不一定记得分类）。 */
const prepPickerList = computed(() => {
  const keyword = prepPickerSearch.value.trim()
  const rows = prepItems.value.filter(
    (row) => row && (!keyword || String(row.item_name || '').includes(keyword)),
  )
  return rows.slice(0, 80)
})

/** 还差哪一项（按钮上那句小字「还差原因」）—— 只用于文案，校验仍是 firstMissing()。 */
const missingLabel = computed(() => {
  if (!itemName.value.trim()) return '物料'
  if (!reason.value) return '原因'
  if (!(Number(quantity.value) > 0)) return '数量'
  if (!unit.value) return '单位'
  if (isOther.value && !remark.value.trim()) return '说明'
  return ''
})

/** 吸底主按钮文案：编辑态是「保存修改」，缺项时顺带说一下还差什么（spec 4.1.6 的可选项）。 */
const submitLabel = computed(() => {
  if (busy.value) return editingId.value ? '保存中…' : '提交中…'
  const base = editingId.value ? '保存修改' : '确认登记'
  return missingLabel.value ? base + ' · 还差' + missingLabel.value : base
})

const canSaveTemplate = computed(
  () => !busy.value && !!itemName.value.trim() && !!unit.value && !!reason.value,
)

function redirectLogin() {
  const target = loginRedirectTarget(router.currentRoute.value)
  if (target) router.replace(target)
}

/** 这一轮请求按哪套凭据解析：员工档声明 staff、超管档声明 admin。
 *
 *  共用电脑上两套 cookie 都在（ADR 0091），而后端默认「管理端优先」—— 员工在员工页记的
 *  一笔会归属成管理端、明细还能看到全店。所以页面按**此刻的工作台身份**显式声明一次。
 *  声明只选凭据、不放宽校验（同 WS 的 ?identity=）：那一支没有有效会话就是 401，
 *  不回落到另一支。
 *
 *  身份探针还没给出结论（null）时**不声明**：退回旧的 cookie 优先级（顶多还是旧行为），
 *  绝不自作主张猜一个声明去提权；探针回来后下面的 watch 会按新身份重读一次。
 */
function identityParams() {
  const identity = identityStore.identity
  if (identity === IDENTITY_STAFF) return { identity: 'staff' }
  if (identity === IDENTITY_ADMIN) return { identity: 'admin' }
  return {}
}

/** 给 api.upload 用：它只收 path（没有 params 参数），所以查询串直接拼在路径上。 */
function withIdentity(path) {
  const { identity } = identityParams()
  return identity ? path + '?identity=' + identity : path
}

// 身份探针晚于本页第一轮请求时（切换器先发起探针、或两套会话都在的共用电脑），拿到结论
// 后按新身份重读：否则员工页上还是按管理端拿到的全店视图。
watch(
  () => identityStore.identity,
  (next, prev) => {
    if (next && next !== prev && state.value === 'ready') load(true)
  },
)

/**
 * 从备货计划带过来（spec 4.5）：?from=prep&item=五花肉&unit=kg → 预填物料与单位。
 * **不填数量**：数量必须现场确认。只在第一轮 load 之后跑一次；query 只认 from=prep，
 * 别的来源一律忽略（不猜）。备货品的单位可能不在损耗清单里（条/底/板/碗），
 * applyUnitChoice 会把它落到「其他…」并填好，见 Lead 2026-11-07 补的边界。
 */
function applyQueryPrefill() {
  if (prefillApplied) return false
  prefillApplied = true
  const query = route.query || {}
  const read = (key) => {
    const raw = query[key]
    return String(Array.isArray(raw) ? raw[0] : raw || '')
  }
  if (read('from') !== 'prep') return false
  const name = read('item').trim()
  const unitValue = read('unit').trim()
  if (!name) return false
  itemName.value = name.slice(0, maxItemName.value)
  if (unitValue) applyUnitChoice(unitValue)
  prepSource.value =
    '从备货计划带过来：' + [itemName.value, unitValue].filter(Boolean).join(' ') + '（数量自己填）'
  return true
}

function readDraft() {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY)
    if (!raw) return null
    const data = JSON.parse(raw)
    return data && typeof data === 'object' ? data : null
  } catch {
    return null
  }
}

function removeDraft() {
  try {
    window.localStorage.removeItem(DRAFT_KEY)
  } catch {
    // 隐私模式写不进 localStorage：本次照样能提交，只是没有草稿
  }
}

function clearDraft() {
  removeDraft()
  draftRestored.value = false
}

/** 表单有变化就存草稿（spec 4.3.1）。只存有内容的那一笔：提交成功后单位/原因/日期是有意
 *  保留的，没有物料/数量/备注时不该留下一个「未完成的一笔」打扰下一次进来的人。 */
function saveDraft() {
  if (!draftReady || editingId.value) return
  const data = {
    item_name: itemName.value,
    quantity: quantity.value,
    unit_choice: unitChoice.value,
    unit_custom: unitCustom.value,
    reason: reason.value,
    remark: remark.value,
    day: day.value,
  }
  const empty =
    !String(data.item_name || '').trim() && !data.quantity && !String(data.remark || '').trim()
  if (empty) {
    removeDraft()
    return
  }
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(data))
  } catch {
    // 同上：存不下就算了，不拦提交
  }
}

/** 草稿恢复（spec 4.3.1）：有内容才恢复，并把「已恢复」那一句摆出来。 */
function restoreDraft() {
  const data = readDraft()
  if (!data) return
  const hasContent =
    Boolean(String(data.item_name || '').trim()) ||
    Boolean(data.quantity) ||
    Boolean(String(data.remark || '').trim())
  if (!hasContent) {
    removeDraft()
    return
  }
  itemName.value = String(data.item_name || '')
  quantity.value = data.quantity ?? ''
  reason.value = String(data.reason || '')
  applyUnitChoice(data.unit_choice === '__custom' ? data.unit_custom : data.unit_choice)
  remark.value = String(data.remark || '')
  if (data.day) day.value = String(data.day)
  if (remark.value.trim()) remarkOpen.value = true
  draftRestored.value = true
}

function pickDay(value) {
  if (!value) return
  day.value = value
}

/** 点「最近记过」：名字和单位一起填好（2026-11-07）。chip 上写着「五花肉 kg」，只填名字
 *  就还得再点一次单位 —— 而这一页存在的意义就是少点。单位取服务端给的最近一次写法。 */
function pickItem(name, itemUnit) {
  itemName.value = name
  if (itemUnit) applyUnitChoice(itemUnit)
}

/** 打开「从备货单选」：顺手锁住背后页面的滚动（弹层是 fixed，手指划过遮罩会带着背景跑）。 */
function openPrepPicker() {
  prepPickerSearch.value = ''
  prepPickerOpen.value = true
  if (typeof document !== 'undefined') document.body.style.overflow = 'hidden'
  // 打开就把光标放进搜索框：手机上少点一次，也能直接弹键盘筛。
  nextTick(() => {
    if (prepPickerInput.value) prepPickerInput.value.focus()
  })
}

function closePrepPicker() {
  prepPickerOpen.value = false
  prepPickerSearch.value = ''
  if (typeof document !== 'undefined') document.body.style.overflow = ''
}

/** 选中一份备货品：品名 + 单位一起填，顺带挂上「对应备货品」。**不填数量** —— 坏了多少
 *  只有站在现场的人知道。 */
function pickPrepItem(row) {
  if (!row) return
  itemName.value = String(row.item_name || '')
  if (row.unit) applyUnitChoice(row.unit)
  prepItemId.value = row.id === undefined || row.id === null ? '' : String(row.id)
  prepSource.value =
    '从备货清单选的：' + [row.item_name, row.unit].filter(Boolean).join(' ') + '（数量自己填）'
  closePrepPicker()
}

function pickUnit(value) {
  if (value === '__custom') {
    unitChoice.value = '__custom'
    nextTick(() => {
      if (unitCustomInput.value && typeof unitCustomInput.value.focus === 'function') {
        unitCustomInput.value.focus({ preventScroll: true })
      }
    })
    return
  }
  unitChoice.value = value
  unitCustom.value = ''
}

/** 把一个服务端/历史给的单位值落进 chips：命中常用项就选中，没命中就落到「其他…」并填好
 *  （备货品的「条 / 底 / 板 / 碗」不属于损耗 UNITS，不能静默丢）。 */
function applyUnitChoice(value) {
  const next = String(value || '').trim()
  if (!next) {
    unitChoice.value = ''
    unitCustom.value = ''
    return
  }
  if (units.value.includes(next)) {
    unitChoice.value = next
    unitCustom.value = ''
    return
  }
  unitChoice.value = '__custom'
  unitCustom.value = next
}

function formatQuantity(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  // 整数不拖一串 0（1 而不是 1.000），小数最多三位（与库里的刻度一致）。
  return String(Math.round(number * 1000) / 1000)
}

async function load(quiet = false) {
  if (!quiet) state.value = 'loading'
  try {
    const params = identityParams()
    const [meta, tpl, sug, list, items] = await Promise.all([
      api.get('/api/wastage/meta', params),
      api.get('/api/wastage/templates', params),
      api.get('/api/wastage/suggestions', params),
      api.get('/api/wastage/records', { ...params, limit: 8 }),
      // 「对应备货品」的候选项来自备货品主数据（同一个「任一身份」门，不新开权限）。
      // 它读不到不该把整页拖成错误态：下拉留空，登记照常能提交。
      api.get('/api/prep-plan/items', params).catch(() => ({ items: [] })),
    ])
    reasons.value = meta.meta.reasons
    units.value = meta.meta.units
    maxImages.value = meta.meta.max_images
    maxItemName.value = meta.meta.max_item_name
    maxRemark.value = meta.meta.max_remark
    maxTemplates.value = meta.meta.max_templates
    maxImageBytes.value = meta.meta.max_image_bytes
    amendWindowHours.value = Number(meta.meta.amend_window_hours) || 24
    today.value = meta.today
    templates.value = tpl.templates || []
    suggestions.value = sug.suggestions || []
    prepItems.value = Array.isArray(items.items) ? items.items : []
    records.value = list.records || []
    total.value = list.total || 0
    dayCounts.value = list.counts || { today: null, yesterday: null }
    if (!day.value) day.value = meta.today
    state.value = 'ready'
  } catch (err) {
    if (err.status === 401) {
      redirectLogin()
      return
    }
    errorText.value = err.message || '读不到损耗登记的资料'
    state.value = 'error'
  }
}

async function loadRecords() {
  try {
    const list = await api.get('/api/wastage/records', { ...identityParams(), limit: 8 })
    records.value = list.records || []
    total.value = list.total || 0
    dayCounts.value = list.counts || { today: null, yesterday: null }
  } catch (err) {
    if (err.status !== 401) listError.value = err.message || '读不到最近的记录'
  }
}

/** 「查看全部」展开态（A1）：默认 8 条是给「刚记完一眼看到」的；要核对第 9 笔以前的事，
 *  得能翻到全部 —— 否则 24 小时更正入口对第 9 条以前的记录等于不存在。 */
const allRecords = ref([])
const showAll = ref(false)
const loadingAll = ref(false)
const visibleRecords = computed(() => (showAll.value ? allRecords.value : records.value))
const hasMoreRecords = computed(() => total.value > records.value.length)

/** 一次拉完：接口 limit 上限 100，按 offset 逐页拼；最多 10 页（1000 条）防手滑。 */
async function loadAllRecords() {
  loadingAll.value = true
  try {
    let rows = []
    let totalCount = total.value
    for (let page = 0; page < 10; page += 1) {
      const res = await api.get('/api/wastage/records', { ...identityParams(), offset: rows.length, limit: 100 })
      const batch = res.records || []
      totalCount = Number(res.total) || totalCount
      rows = rows.concat(batch)
      if (!batch.length || rows.length >= totalCount) break
    }
    allRecords.value = rows
    total.value = totalCount
    showAll.value = true
  } catch (err) {
    if (err.status !== 401) listError.value = err.message || '读不到全部记录'
  } finally {
    loadingAll.value = false
  }
}

async function toggleAllRecords() {
  if (showAll.value) {
    showAll.value = false
    return
  }
  await loadAllRecords()
}

/** 提交/改/撤之后刷新记录区：展开态要连全部一起重拉，别让展开的列表停在旧数据上。 */
async function refreshRecords() {
  await loadRecords()
  if (showAll.value) await loadAllRecords()
}

/** 模板单独抽出来：提交成功后要重拉一次，下一笔才点得到刚记过的组合。 */
async function loadTemplates() {
  templates.value = (await api.get('/api/wastage/templates', identityParams())).templates || []
}

async function loadSuggestions() {
  suggestions.value = (await api.get('/api/wastage/suggestions', identityParams())).suggestions || []
}

/** 把一句话的错误放到按钮上方、并聚焦缺的那一项（不是页顶 —— 页顶在提交时通常已经滚出
 *  视口了，2026-11-07 实测）。提示本身在吸底条里，所以不会再滚一次页面。 */
async function showError(message, target) {
  formError.value = message
  formErrorRetry.value = false
  await nextTick()
  // 目标用函数取：备注区是刚被展开的，nextTick 之前 ref 还是 null。
  const el = typeof target === 'function' ? target() : target
  if (el && typeof el.focus === 'function') el.focus()
}

/** 缺什么就返回什么（顺序即校验顺序）：返回 { message, target }，全齐返回 null。 */
function firstMissing() {
  if (!itemName.value.trim()) {
    return { message: '先填物料：哪个东西坏了？', target: () => itemInput.value }
  }
  // 表单顺序是「物料 → 原因 → 数量与单位」（2026-11-11 用户要求），校验跟着视觉顺序走。
  if (!reason.value) {
    return { message: '请选一个损耗原因', target: () => reasonBox.value }
  }
  if (!(Number(quantity.value) > 0)) {
    return { message: '数量要大于 0：坏了多少就填多少', target: () => quantityInput.value }
  }
  if (!unit.value) {
    return { message: '请选一个单位：多少份 / kg / 个', target: () => unitBox.value }
  }
  if (isOther.value && !remark.value.trim()) {
    // 「其他」的说明收在「＋备注」里：就地展开，员工才看得见要补什么。
    remarkOpen.value = true
    return {
      message: '选了「其他」要写一句说明：不然统计里看不出是什么情况',
      target: () => remarkInput.value,
    }
  }
  return null
}

function pickTemplate(tpl) {
  cancelTemplateConfirm()
  if (
    tpl.id &&
    tpl.item_name === itemName.value &&
    tpl.unit === unit.value &&
    tpl.reason === reason.value
  ) {
    // 再点一下就是取消（同源项目）。
    itemName.value = ''
    reason.value = ''
    unitChoice.value = ''
    unitCustom.value = ''
    return
  }
  itemName.value = tpl.item_name
  reason.value = tpl.reason
  applyUnitChoice(tpl.unit)
}

async function saveAsTemplate() {
  formError.value = ''
  formErrorRetry.value = false
  note.value = ''
  try {
    await api.post(
      '/api/wastage/templates',
      {
        item_name: itemName.value.trim(),
        unit: unit.value,
        reason: reason.value,
      },
      identityParams(),
    )
    note.value = '已存为常用模板，下次点一下就能填好。'
    await loadTemplates()
  } catch (err) {
    if (err.status === 401) return redirectLogin()
    formError.value = err.message || '存模板失败'
  }
}

/** 点 ×：先变成「确认删」，3 秒内再点才真删（spec 4.4）。点别处由 document 监听复位。 */
function askRemoveTemplate(tpl) {
  if (!tpl || !tpl.id) return
  cancelRevokeConfirm()
  cancelTemplateConfirm()
  confirmDeleteId.value = tpl.id
  templateTimer = setTimeout(() => {
    templateTimer = null
    confirmDeleteId.value = null
  }, CONFIRM_RESET_MS)
}

function cancelTemplateConfirm() {
  if (templateTimer) {
    clearTimeout(templateTimer)
    templateTimer = null
  }
  confirmDeleteId.value = null
}

async function removeTemplate(tpl) {
  if (!tpl || !tpl.id) return
  cancelTemplateConfirm()
  formError.value = ''
  formErrorRetry.value = false
  note.value = ''
  try {
    await api.delete('/api/wastage/templates/' + tpl.id, identityParams())
    note.value = '已删掉模板：' + tpl.item_name
    await loadTemplates()
  } catch (err) {
    if (err.status === 401) return redirectLogin()
    formError.value = err.message || '删模板失败'
  }
}

function toggleRemark() {
  remarkOpen.value = !remarkOpen.value
}

function toggleDate() {
  dateOpen.value = !dateOpen.value
}
function togglePrepSelect() {
  prepSelectOpen.value = !prepSelectOpen.value
}

/** 选填行那颗按钮的文案：没挂就是「＋备货品」，挂上了就报出是哪一个（否则员工看不出
 *  自己刚才那一下挂没挂上）。
 *  390 宽这一行只有 332px：四颗按钮原来 381px，第 4 颗被右缘切掉一截。所以默认文案
 *  从「＋对应备货品」缩成「＋备货品」——展开区里的字段名仍然是完整的「对应备货品」。 */
const prepPickedLabel = computed(() => {
  if (!prepItemId.value) return '＋备货品'
  const hit = prepItems.value.find((row) => String(row.id) === String(prepItemId.value))
  return hit ? '备货品 · ' + [hit.item_name, hit.unit].filter(Boolean).join(' ') : '＋备货品'
})

/** 点「＋现场照片 / 再拍」：**一下**就开全屏取景（页内相机，不跳系统相机）。
 *  2026-11-11 之前要先展开「现场照片」那一节、再点「拍一张」：现场手忙脚乱时多点一次就
 *  容易放弃拍（用户反馈「步骤太多，点好几次才打开相机」）。 */
function openCamera() {
  if (images.value.length >= maxImages.value) {
    formError.value = '现场照片最多 ' + maxImages.value + ' 张'
    return
  }
  formError.value = ''
  cameraOpen.value = true
  // 浮层自己 fixed 占满，但底下的表单还能滚：锁住它，免得手指在取景框上划把页面带走。
  if (typeof document !== 'undefined') document.body.style.overflow = 'hidden'
}

function closeCamera() {
  cameraOpen.value = false
  // 从备货单选也是浮层，两个不会同时开；只放自己那把锁。
  if (typeof document !== 'undefined' && !prepPickerOpen.value) {
    document.body.style.overflow = ''
  }
}

/** 页内相机交回一张 JPEG（长边 1600、q0.75）：直接传，传完缩略图就出现在首位那块里。
 *
 *  拍满 3 张自动收起相机 —— 否则取景框还开着、按钮已经点不动，看着像卡住了。 */
async function onCaptured(blob) {
  await uploadOne(blob, 'wastage.jpg')
  if (images.value.length >= maxImages.value) closeCamera()
}

async function uploadOne(file, filename = 'wastage.jpg') {
  uploading.value = true
  formError.value = ''
  try {
    const form = new FormData()
    form.append('file', file, filename)
    // 上传也要声明身份：图归属决定提交时能不能绑进这一笔（双 cookie 下默认归管理端）。
    const data = await api.upload(withIdentity('/api/wastage/images'), form)
    images.value = [...images.value, data.image]
  } catch (err) {
    if (err.status === 401) {
      redirectLogin()
      return
    }
    formError.value = err.message || '照片上传失败'
  } finally {
    uploading.value = false
  }
}

function removeImage(index) {
  images.value = images.value.filter((_, i) => i !== index)
}

function resetForm() {
  itemName.value = ''
  quantity.value = ''
  remark.value = ''
  prepItemId.value = ''
  images.value = []
  // 日期、单位**与原因**留着：连着记同一批损耗时那三样通常一样（2026-11-07 审查：原来
  // 原因被清空，同一天记三样烂菜要重选三次）。
  day.value = today.value || day.value
}

/** 提交成功之后（新建与修改共用）：清表单、收折叠区、删草稿、清来源提示。 */
function afterSaved() {
  resetForm()
  remarkOpen.value = false
  dateOpen.value = false
  prepSelectOpen.value = false
  closeCamera()
  prepSource.value = ''
  clearDraft()
}

function focusItem() {
  if (itemInput.value && typeof itemInput.value.focus === 'function') {
    itemInput.value.focus({ preventScroll: true })
  }
}

/** 收起编辑态（取消 / 保存成功 / 被撤回）。 */
function exitEdit() {
  editingId.value = null
  editingDate.value = ''
  editingImages.value = 0
}

function cancelEdit() {
  if (busy.value) return
  exitEdit()
  afterSaved()
  formError.value = ''
  formErrorRetry.value = false
  note.value = ''
  focusItem()
}

/** 点记录上的「改」：把这一条填进表单，进入编辑态（spec 4.2.2）。照片保留、不能换。 */
async function startEdit(row) {
  if (!row || busy.value) return
  cancelRevokeConfirm()
  cancelTemplateConfirm()
  editingId.value = row.id
  editingDate.value = row.wastage_date || ''
  editingImages.value = row.images ? row.images.length : 0
  itemName.value = row.item_name || ''
  quantity.value = formatQuantity(row.quantity)
  applyUnitChoice(row.unit || '')
  reason.value = row.reason || ''
  remark.value = row.remark || ''
  day.value = row.wastage_date || today.value
  images.value = []
  closeCamera()
  remarkOpen.value = Boolean(row.remark)
  dateOpen.value = false
  formError.value = ''
  formErrorRetry.value = false
  note.value = ''
  listError.value = ''
  listNote.value = ''
  clearDraft()
  await nextTick()
  if (formBox.value && typeof formBox.value.scrollIntoView === 'function') {
    formBox.value.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }
  focusItem()
}

/** 点「撤回」：先变成「确认撤回」+「取消」，3 秒内再点才真撤（spec 4.2.3）。 */
function askRevoke(row) {
  if (!row || !row.id || revoking.value) return
  cancelTemplateConfirm()
  cancelRevokeConfirm()
  revokeConfirmId.value = row.id
  revokeTimer = setTimeout(() => {
    revokeTimer = null
    revokeConfirmId.value = null
  }, CONFIRM_RESET_MS)
}

function cancelRevokeConfirm() {
  if (revokeTimer) {
    clearTimeout(revokeTimer)
    revokeTimer = null
  }
  revokeConfirmId.value = null
}

async function revoke(row) {
  if (!row || revoking.value) return
  revoking.value = true
  listError.value = ''
  listNote.value = ''
  // 撤回是新的动作：上一次「已改好 / 已记下」还挂在吸底条上的话，会让人以为刚才那下撤回的是它。
  note.value = ''
  formError.value = ''
  formErrorRetry.value = false
  try {
    await api.delete('/api/wastage/records/' + row.id, identityParams())
    records.value = records.value.filter((item) => item.id !== row.id)
    if (showAll.value) allRecords.value = allRecords.value.filter((item) => item.id !== row.id)
    total.value = Math.max(0, total.value - 1)
    if (lastSavedId.value === row.id) lastSavedId.value = null
    if (editingId.value === row.id) cancelEdit()
    listNote.value = '已撤回：' + row.item_name + ' ' + formatQuantity(row.quantity) + row.unit
    cancelRevokeConfirm()
  } catch (err) {
    if (err.status === 401) {
      redirectLogin()
      return
    }
    listError.value = err.message || '撤回失败'
    cancelRevokeConfirm()
    // 403/404/409 都说明这一行的 can_amend 已经过时：重拉一次，别再让人点第二次白点。
    await refreshRecords()
  } finally {
    revoking.value = false
  }
}

/** 新建一笔。 */
async function createRecord() {
  const data = await api.post(
    '/api/wastage/records',
    {
      item_name: itemName.value.trim(),
      quantity: Number(quantity.value),
      unit: unit.value,
      reason: reason.value,
      remark: remark.value.trim(),
      wastage_date: day.value,
      image_ids: images.value.map((row) => row.capture_id),
      // 不选就显式传 null：复盘按品名兜底，不会把空串当成一个 id。
      prep_item_id: prepItemId.value ? Number(prepItemId.value) : null,
    },
    identityParams(),
  )
  const record = data.record
  note.value =
    '已记下：' +
    record.item_name +
    ' ' +
    formatQuantity(record.quantity) +
    record.unit +
    ' · ' +
    record.reason_text
  lastSavedId.value = record.id ?? null
  afterSaved()
  // 三样一起重拉：记录（下面那条要出现）、模板（刚记的高频组合下一笔就能点）、
  // 名字建议（这一笔的物料名下次该能点出来）。
  await Promise.all([refreshRecords(), loadTemplates(), loadSuggestions()])
  await nextTick()
  // 提交成功后**页面不滚动**（spec 4.1.7）：只把焦点放回物料框，方便连着记下一笔。
  focusItem()
}

/** 改这一笔（PATCH，只改字段；照片保留不动）。 */
async function saveEdit() {
  const id = editingId.value
  const data = await api.patch(
    '/api/wastage/records/' + id,
    {
      item_name: itemName.value.trim(),
      quantity: Number(quantity.value),
      unit: unit.value,
      reason: reason.value,
      remark: remark.value.trim(),
      wastage_date: day.value,
    },
    identityParams(),
  )
  const record = data.record
  exitEdit()
  note.value =
    '已改好：' +
    record.item_name +
    ' ' +
    formatQuantity(record.quantity) +
    record.unit +
    ' · ' +
    record.reason_text
  lastSavedId.value = record.id ?? null
  afterSaved()
  await Promise.all([refreshRecords(), loadTemplates(), loadSuggestions()])
  await nextTick()
  focusItem()
}

async function submit() {
  if (busy.value) return
  formError.value = ''
  formErrorRetry.value = false
  note.value = ''
  listError.value = ''
  listNote.value = ''

  // 就地校验：缺什么就在按钮上方说清楚、聚焦那项。按钮不再 disabled，这几条才真正跑得到。
  const missing = firstMissing()
  if (missing) return showError(missing.message, missing.target)

  busy.value = true
  try {
    if (editingId.value) await saveEdit()
    else await createRecord()
  } catch (err) {
    if (err.status === 401) {
      redirectLogin()
      return
    }
    formError.value = err.message || (editingId.value ? '保存失败' : '提交失败')
    formErrorRetry.value = true
    // 权限/窗口/记录都没了：这一行的 can_amend 已经过时，重拉列表让人看到真实状态。
    if (editingId.value && [403, 404, 409].includes(err.status)) await refreshRecords()
  } finally {
    busy.value = false
  }
}

/** 点别处复位二次确认（模板删除 / 记录撤回）。确认按钮所在容器用 @click.stop 挡住冒泡。 */
function onDocumentClick() {
  cancelTemplateConfirm()
  cancelRevokeConfirm()
}

/** @click.stop 的占位处理：只负责别让这次点击冒到 document 把确认态复位。 */
function ignoreClick() {}

// 表单变化时存草稿（spec 4.3.1）。拆开写比一个 getter 更直白，也避免图片参与草稿。
watch([itemName, quantity, unitChoice, unitCustom, reason, remark, day], saveDraft)
// 选「其他」就地把备注展开（原来它在页底，红字提示被底栏挡着，提交后才被服务端拒）。
watch(reason, (value) => {
  if (value === 'other') remarkOpen.value = true
})

onMounted(async () => {
  document.title = workbenchDocumentTitle('损耗登记')
  document.addEventListener('click', onDocumentClick)
  // 共用电脑上两套 cookie 都在：先等身份探针给出结论，别让员工那一档先按管理端
  // 拿到一次全店明细。探针由壳 / 切换器发起；这里只在它还没开始时补一次（在飞时
  // refresh() 直接返回，由上面的 watch 在结论回来后重读）。
  if (!identityStore.probed) await identityStore.refresh()
  await load()
  if (state.value !== 'ready') return
  // 从备货计划带过来的一笔优先于旧草稿：那是一次明确的现场意图，草稿多半已经过时。
  if (!applyQueryPrefill()) restoreDraft()
  draftReady = true
})

onUnmounted(() => {
  document.body.style.overflow = ''
  document.removeEventListener('click', onDocumentClick)
  if (templateTimer) clearTimeout(templateTimer)
  if (revokeTimer) clearTimeout(revokeTimer)
})
</script>

<template>
  <div class="loss-page">
    <p v-if="state === 'loading'" class="loss-muted">正在读损耗登记…</p>

    <template v-else-if="state === 'error'">
      <p class="loss-error" role="alert">{{ errorText }}</p>
      <button class="btn btn-block" type="button" @click="load()">重试</button>
    </template>

    <template v-else>
      <!-- 顶部一句状态：今天记过没有、正在改哪一笔。 -->
      <p class="loss-status">
        <span class="loss-status-today">{{ statusText }}</span>
        <span v-if="editingId" class="loss-status-edit">正在改 {{ editingDateLabel }} 那一笔</span>
      </p>
      <!-- 从备货计划带过来（spec 4.5）：说清这物料/单位是哪来的，数量仍然要现场填。 -->
      <p v-if="prepSource" class="loss-source">{{ prepSource }}</p>
      <p v-if="draftRestored" class="loss-hint loss-draft">上次没提交完的那一笔已恢复</p>

      <!-- 常用模板：我存过的（可删）在前，我最近常这么记的在后。 -->
      <section class="loss-block">
        <div class="loss-block-hd">
          <span>常用模板</span>
          <span class="loss-hint">点一下填好</span>
        </div>
        <div v-if="templates.length" class="loss-chips">
          <span
            v-for="(tpl, index) in templates"
            :key="tpl.item_name + '-' + tpl.unit + '-' + tpl.reason + '-' + index"
            class="loss-chip"
            @click.stop="ignoreClick"
          >
            <button type="button" class="loss-chip-main" @click="pickTemplate(tpl)">
              <b>{{ tpl.item_name }}</b>
              <em>{{ tpl.unit }} · {{ tpl.reason_text }}</em>
            </button>
            <!-- 删除模板要二次确认（spec 4.4）：点 × 变成「确认删」，3 秒内再点才真删。 -->
            <button
              v-if="tpl.is_custom && confirmDeleteId !== tpl.id"
              type="button"
              class="loss-chip-x"
              :aria-label="'删除模板 ' + tpl.item_name"
              @click="askRemoveTemplate(tpl)"
            >×</button>
            <button
              v-else-if="tpl.is_custom"
              type="button"
              class="loss-chip-x loss-chip-x-confirm"
              :aria-label="'确认删除模板 ' + tpl.item_name"
              @click="removeTemplate(tpl)"
            >确认删</button>
          </span>
        </div>
        <p v-else class="loss-muted">
          还没有模板：填好下面这一笔后点「存为模板」，下次记同样的东西点一下就行（最多 {{ maxTemplates }} 个）。
        </p>
      </section>

      <form ref="formBox" class="loss-block loss-form" @submit.prevent="submit">
        <div class="loss-block-hd">
          <span>{{ editingId ? '改这一笔' : '这一笔损耗' }}</span>
          <span v-if="editingId" class="loss-hint">改完点下面「保存修改」</span>
        </div>

        <!-- 必填一：物料。手输仍是主路（名字可能很长、可能是新东西），下面把「最近记过的」
             摆成一排可点的 chips —— 手机上的 datalist 补全在 iOS 基本不弹（2026-11-07）。 -->
        <div class="loss-field">
          <div class="loss-label-row">
            <label class="loss-label" for="wastage-item-name">
              物料 <span class="loss-required">必填</span>
            </label>
            <button
              v-if="!editingId && prepItems.length"
              type="button"
              class="loss-link-btn"
              @click="openPrepPicker"
            >从备货单选 ›</button>
          </div>
          <input
            id="wastage-item-name"
            ref="itemInput"
            v-model="itemName"
            class="loss-input loss-input-lg"
            type="text"
            :maxlength="maxItemName"
            list="wastage-item-names"
            placeholder="例如：五花肉"
            autocomplete="off"
          />
          <datalist id="wastage-item-names">
            <option v-for="row in suggestions" :key="row.item_name" :value="row.item_name" />
          </datalist>
          <div v-if="quickItems.length" class="loss-quick">
            <span class="loss-quick-hd">最近记过</span>
            <div class="loss-quick-list">
              <button
                v-for="row in quickItems"
                :key="row.item_name"
                type="button"
                class="loss-quick-chip"
                @click="pickItem(row.item_name, row.unit)"
              >{{ row.item_name }}<em v-if="row.unit" class="loss-quick-unit">{{ row.unit }}</em></button>
            </div>
          </div>
        </div>

        <!-- 必填二：原因。三列两行（6 个按钮），首屏省一行；先选原因再填数量与单位。 -->
        <div class="loss-field">
          <span class="loss-label">原因 <span class="loss-required">必填</span></span>
          <div ref="reasonBox" class="loss-reasons" tabindex="-1">
            <button
              v-for="row in reasons"
              :key="row.value"
              type="button"
              class="loss-reason"
              :class="{ on: reason === row.value }"
              :aria-pressed="reason === row.value"
              @click="reason = row.value"
            >{{ row.label }}</button>
          </div>
        </div>

        <!-- 必填三：数量 + 单位。单位从原生 select 改成 chips（手机上少一层面板，2026-11-07）。
             2026-11-11 用户把「原因」提到数量前面：先选为什么丢，再填丢了多少。 -->
        <div class="loss-field">
          <span class="loss-label">数量与单位 <span class="loss-required">必填</span></span>
          <input
            ref="quantityInput"
            v-model="quantity"
            class="loss-input loss-input-lg"
            type="number"
            inputmode="decimal"
            step="0.001"
            min="0"
            placeholder="多少？例如：1.5"
          />
          <div ref="unitBox" class="loss-units" tabindex="-1">
            <button
              v-for="item in unitChips"
              :key="item"
              type="button"
              class="loss-unit-chip"
              :class="{ on: unitChoice === item }"
              :aria-pressed="unitChoice === item"
              @click="pickUnit(item)"
            >{{ item }}</button>
            <button
              type="button"
              class="loss-unit-chip"
              :class="{ on: unitChoice === '__custom' }"
              :aria-pressed="unitChoice === '__custom'"
              @click="pickUnit('__custom')"
            >其他…</button>
          </div>
          <input
            v-if="unitChoice === '__custom'"
            ref="unitCustomInput"
            v-model="unitCustom"
            class="loss-input loss-unit-custom"
            type="text"
            maxlength="10"
            placeholder="填单位，例如：罐"
          />
        </div>

        <!-- 选填三项收成一行小按钮（spec 4.1.5）：默认收起，点开才展开各自的区域。 -->
        <div class="loss-more">
          <button
            v-if="!editingId"
            type="button"
            class="loss-more-btn"
            :class="{ on: images.length }"
            @click="openCamera"
          >{{ images.length ? '现场照片 · ' + images.length + '/' + maxImages : '＋现场照片' }}</button>
          <span v-else class="loss-more-btn is-static">照片保留</span>
          <button
            type="button"
            class="loss-more-btn"
            :class="{ on: remarkOpen }"
            :aria-expanded="remarkOpen"
            @click="toggleRemark"
          >{{ remark.trim() ? '备注 · ' + remark.trim().length + ' 字' : '＋备注' }}</button>
          <button
            type="button"
            class="loss-more-btn"
            :class="{ on: dateOpen || !!backdatedNote }"
            :aria-expanded="dateOpen"
            @click="toggleDate"
          >改日期</button>
          <button
            v-if="!editingId && prepItems.length"
            type="button"
            class="loss-more-btn"
            :class="{ on: prepSelectOpen || prepItemId }"
            :aria-expanded="prepSelectOpen"
            @click="togglePrepSelect"
          >{{ prepPickedLabel }}</button>
        </div>

        <!-- 对应备货品（展开后）：选了就把这笔挂到备货主数据上，复盘能按它对齐数量。
             从「备货单选」进来时已经自动挂好，这里给手打名字的人留一个补挂的出口。 -->
        <label v-if="prepSelectOpen && !editingId" class="loss-field">
          <span class="loss-label">
            对应备货品<span class="loss-hint">选填 · 复盘按它对齐数量，不选按品名兜底</span>
          </span>
          <select v-model="prepItemId" class="loss-input loss-select-tap">
            <option value="">不选（复盘按品名兜底）</option>
            <option v-for="item in prepItems" :key="item.id" :value="String(item.id)">
              {{ item.item_name }}<template v-if="item.unit"> · {{ item.unit }}</template>
            </option>
          </select>
        </label>

        <!-- 现场照片：点选填行那颗按钮**一下**就开全屏相机（2026-11-11）。已经拍了的缩略图
             直接列在表单里 —— 不用再展开一层，删除与补拍都在这条上完成。 -->
        <div v-if="!editingId && images.length" class="loss-photo">
          <div class="loss-shots">
            <figure v-for="(shot, index) in images" :key="shot.capture_id" class="loss-shot">
              <img :src="shot.url" alt="现场照片" />
              <button
                type="button"
                class="loss-shot-x"
                :aria-label="'删掉第 ' + (index + 1) + ' 张'"
                @click="removeImage(index)"
              >×</button>
            </figure>
            <button
              v-if="images.length < maxImages"
              type="button"
              class="loss-shot-add loss-shot-camera"
              @click="openCamera"
            >
              <span class="loss-shot-plus" aria-hidden="true">＋</span>
              <span>{{ uploading ? '上传中…' : '再拍' }}</span>
            </button>
          </div>
        </div>

        <!-- 记录上的照片是现场证据：改的时候保留、不能换（spec 边界）。 -->
        <p v-if="editingId" class="loss-hint loss-edit-photo">
          {{ editingImages ? '原记录的 ' + editingImages + ' 张照片保留，不能换。' : '修改时不能补照片。' }}
        </p>

        <!-- 备注（展开后）：选「其他」时必填。 -->
        <div v-if="remarkOpen" class="loss-field">
          <label class="loss-label" for="wastage-remark">
            备注<span v-if="isOther" class="loss-required">（选了「其他」要说明）</span>
            <span v-else class="loss-hint">选填</span>
          </label>
          <textarea
            id="wastage-remark"
            ref="remarkInput"
            v-model="remark"
            class="loss-input loss-textarea"
            :maxlength="maxRemark"
            rows="2"
            :placeholder="isOther ? '具体是什么情况…' : '补充说明…'"
          />
        </div>

        <!-- 日期（展开后）：今天 / 昨天两颗快捷，跨零点补记上一晚点一下就对。 -->
        <div v-if="dateOpen" class="loss-field">
          <span class="loss-label">哪一天 <span class="loss-hint">最晚今天</span></span>
          <div v-if="today" class="loss-days">
            <button
              type="button"
              class="loss-day"
              :class="{ on: day === today }"
              :aria-pressed="day === today"
              @click="pickDay(today)"
            >今天</button>
            <button
              type="button"
              class="loss-day"
              :class="{ on: day === yesterday }"
              :aria-pressed="day === yesterday"
              @click="pickDay(yesterday)"
            >昨天</button>
            <span class="loss-hint">跨零点记前一晚，点「昨天」</span>
          </div>
          <LuyunDatePicker v-model="day" :max-date="today" placeholder="选择日期" aria-label="损耗日期" />
        </div>

        <!-- 吸底条：提示在上、按钮在下。错误/成功都放这里 —— 提交那一刻它一定在视野里。 -->
        <div class="loss-actions">
          <!-- 改过日期时把「记的是哪一天」放在提交按钮旁边：chip 只写「改日期」，
               免得 390 宽横滑把日期本身切掉（F 组）。编辑态顶部已说明在改哪一笔，不重复。 -->
          <p v-if="!editingId && backdatedNote" class="loss-note loss-feedback" role="status">{{ backdatedNote }}</p>
          <p v-if="formError" class="loss-error loss-feedback" role="alert">
            <span v-if="formErrorRetry" class="loss-error-retry">再点一次就是重试。</span>
            {{ formError }}
          </p>
          <p v-if="note" class="loss-note loss-feedback" role="status">{{ note }}</p>
          <div class="loss-actions-row">
            <button class="btn btn-primary" type="submit" :disabled="busy">{{ submitLabel }}</button>
            <button v-if="editingId" class="btn" type="button" :disabled="busy" @click="cancelEdit">取消</button>
            <button
              v-else
              class="btn"
              type="button"
              :disabled="!canSaveTemplate"
              @click="saveAsTemplate"
            >存为模板</button>
          </div>
        </div>
      </form>

      <section class="loss-block">
        <div class="loss-block-hd">
          <span>我最近的登记</span>
          <span class="loss-recent-hd">
            <span v-if="total" class="loss-hint">共 {{ total }} 笔</span>
            <button
              v-if="hasMoreRecords || showAll"
              type="button"
              class="loss-more-link"
              :disabled="loadingAll"
              @click="toggleAllRecords"
            >{{ showAll ? '收起' : (loadingAll ? '读取中…' : '查看全部 ›') }}</button>
          </span>
        </div>
        <p v-if="listError" class="loss-error" role="alert">{{ listError }}</p>
        <p v-if="listNote" class="loss-note" role="status">{{ listNote }}</p>
        <p v-if="!visibleRecords.length" class="loss-muted">还没有登记过。上面这一笔记完就会出现在这里。</p>
        <ul v-else class="loss-list">
          <li
            v-for="row in visibleRecords"
            :key="row.id"
            class="loss-item"
            :class="{ 'is-new': row.id === lastSavedId, 'is-editing': row.id === editingId }"
          >
            <div class="loss-item-hd">
              <span class="loss-date">{{ row.wastage_date }}</span>
              <b class="loss-name">{{ row.item_name }}</b>
              <span class="loss-qty">{{ formatQuantity(row.quantity) }}{{ row.unit }}</span>
              <span class="loss-reason-tag">{{ row.reason_text }}</span>
            </div>
            <p v-if="row.remark" class="loss-remark">{{ row.remark }}</p>
            <div v-if="row.images && row.images.length" class="loss-thumbs">
              <a
                v-for="shot in row.images"
                :key="shot.capture_id"
                :href="shot.url"
                target="_blank"
                rel="noopener"
              >
                <img :src="shot.url" alt="现场照片" />
              </a>
            </div>
            <!-- 只有 can_amend 的行才有更正入口（本人 + 未撤回 + 24h 内，判据在服务端）。 -->
            <div v-if="row.can_amend" class="loss-item-acts" @click.stop="ignoreClick">
              <template v-if="revokeConfirmId === row.id">
                <button
                  type="button"
                  class="loss-item-btn is-danger"
                  :disabled="revoking"
                  @click="revoke(row)"
                >确认撤回</button>
                <button type="button" class="loss-item-btn" @click="cancelRevokeConfirm">取消</button>
              </template>
              <template v-else>
                <button type="button" class="loss-item-btn" @click="startEdit(row)">改</button>
                <button type="button" class="loss-item-btn" @click="askRevoke(row)">撤回</button>
              </template>
            </div>
          </li>
        </ul>
      </section>

      <p class="loss-foot">
        损耗记的是已经发生的事：日期最晚到今天，数量按实际报损填（1.5 kg、半盒都行），
        「其他」那一档要写一句说明 —— 不然月底的统计里看不出是什么情况。
        记错了不用憋着：{{ amendWindowHours }} 小时内本人可以「改」或「撤回」。
      </p>
    </template>

    <!-- 从备货单选（2026-11-07）：备货品主数据里已有品名与单位，点一下两件一起填，
         还顺带挂上「对应备货品」的关联。数量仍由现场按实际报损填。 -->
    <div v-if="prepPickerOpen" class="loss-picker-scrim" @click="closePrepPicker"></div>
    <section
      v-if="prepPickerOpen"
      class="loss-picker"
      role="dialog"
      aria-modal="true"
      aria-label="从备货单选"
      @keydown.esc="closePrepPicker"
    >
      <div class="loss-picker-hd">
        <span>从备货单选</span>
        <button type="button" class="loss-picker-x" @click="closePrepPicker">关闭</button>
      </div>
      <input
        ref="prepPickerInput"
        v-model="prepPickerSearch"
        class="loss-input"
        type="search"
        placeholder="搜名字，例如：餐包"
        autocomplete="off"
      />
      <ul v-if="prepPickerList.length" class="loss-picker-list">
        <li v-for="row in prepPickerList" :key="row.id">
          <button type="button" class="loss-picker-item" @click="pickPrepItem(row)">
            <b>{{ row.item_name }}</b>
            <em>{{ [row.unit, row.category].filter(Boolean).join(' · ') }}</em>
          </button>
        </li>
      </ul>
      <p v-else class="loss-muted">没有匹配的备货品，直接在上面手打名字也行。</p>
    </section>

    <!-- 全屏相机（2026-11-11 用户要求）：固定占满屏幕、打开期间页面锁滚，取景框不跟着手指走。
         组件负责开流 / 抓拍 / 传 Blob，这里只给舞台、进度与收起。 -->
    <Teleport to="body">
      <div
        v-if="cameraOpen"
        class="loss-cam"
        role="dialog"
        aria-modal="true"
        aria-label="现场照片"
        @keydown.esc="closeCamera"
      >
        <div class="loss-cam-hd">
          <div class="loss-cam-copy">
            <p class="loss-cam-title">现场照片 <b>{{ images.length }}/{{ maxImages }}</b></p>
            <p class="loss-cam-sub">
              {{ uploading ? '正在上传这一张…' : '拍一张就自动上传 · 每张 ' + maxImageMb + 'MB 以内' }}
            </p>
          </div>
          <button type="button" class="loss-cam-close" @click="closeCamera">收起</button>
        </div>
        <p v-if="formError" class="loss-cam-err" role="alert">{{ formError }}</p>
        <div v-if="images.length" class="loss-cam-shots">
          <figure v-for="(shot, index) in images" :key="shot.capture_id" class="loss-cam-shot">
            <img :src="shot.url" alt="现场照片" />
            <button
              type="button"
              class="loss-shot-x"
              :aria-label="'删掉第 ' + (index + 1) + ' 张'"
              @click="removeImage(index)"
            >×</button>
          </figure>
        </div>
        <div class="loss-cam-stage">
          <HygieneLiveCamera @captured="onCaptured" />
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.loss-page {
  padding: 12px 14px 28px;
  /* 壳的 `.wb-main` 是 flex column：这一层带 `margin: 0 auto` 会从 stretch 变成
     fit-content 宽度，于是被内部单行横滑 chips 的 max-content 撑出 449px（整页能横向
     拖动、右侧内容被切，2026-11-07 实测）。给死 100% 宽度，auto margin 只剩居中。 */
  width: 100%;
  /* 登记是**一列表单**的事：桌面档拉到 1400px 宽只会让输入框长得没法看，所以给内容
     一个宽度上限并居中（手机档本来就比它窄，这条不生效）。 */
  max-width: 720px;
  margin: 0 auto;
}
.loss-muted { font-size: 13px; color: var(--ink-dim); }
.loss-error { font-size: 13px; color: var(--danger-bright); }
.loss-error-retry { display: block; font-size: 11.5px; margin-bottom: 2px; }
.loss-note { font-size: 13px; color: var(--action); }
/* 顶部「今天已记 N 笔」：一句状态，别做成一块卡片。 */
.loss-status {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  margin: 2px 0 10px;
  font-size: 12px; color: var(--ink-dim);
}
.loss-status-today { font-size: 13px; color: var(--ink); font-weight: 600; }
.loss-status-edit { font-size: 12px; color: var(--action); }
/* 「我最近的登记」右半边：共 N 笔 + 查看全部（A1）。查看全部是轻量文字按钮 ——
   44px 行高留给记录行里的「改/撤回」，这里只是把第 9 条以前的事翻出来的入口。 */
.loss-recent-hd { display: flex; align-items: baseline; gap: 10px; letter-spacing: 0; }
.loss-more-link {
  padding: 2px 0; margin: 0;
  font-size: 12px; font-family: inherit; font-weight: 600;
  color: var(--action); background: transparent; border: 0; cursor: pointer;
}
.loss-more-link:disabled { opacity: .5; }
/* 从备货计划带过来的那一句：说明物料/单位是别人带过来的，数量仍要现场填。 */
.loss-source {
  margin: 0 0 10px; padding: 6px 10px;
  border-radius: 8px; background: var(--action-soft);
  font-size: 12.5px; color: var(--ink);
}
.loss-draft { margin: 0 0 10px; }
.loss-block {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.loss-block-hd {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  font-size: 13px; letter-spacing: .06em; color: var(--ink); margin-bottom: 10px;
}
.loss-hint { font-size: 11px; letter-spacing: 0; color: var(--ink-dim); }
.loss-required { font-size: 11px; letter-spacing: 0; color: var(--danger-bright); }
/* 标签后面那半句（「选填」「最多 3 张」）与标签之间留一口气，别粘成「备注选填」。 */
.loss-label .loss-hint, .loss-label .loss-required { margin-left: 6px; }
.loss-field { display: block; margin-bottom: 12px; }
.loss-label { display: block; font-size: 12px; color: var(--ink-dim); margin-bottom: 5px; }
.loss-input {
  width: 100%; padding: 9px 10px; font-size: 14px; font-family: inherit;
  color: var(--ink); background: var(--surface-2);
  border: 1px solid var(--line); border-radius: 8px;
}
/* 必填那三项的输入框大一号：手机上点开就填，字号 16 也不会触发 iOS 缩放。 */
.loss-input-lg { padding: 11px 12px; font-size: 16px; }
.loss-input:focus { border-color: var(--action); }
/* 下拉是手机上常点的控件：与 `--tap` 对齐，别比一指窄。 */
.loss-select-tap { min-height: var(--tap); }
.loss-textarea { resize: vertical; }
/* 单位 chips：常用的前 6 个 + 「其他…」，选中用 --action 描边（spec 4.1.3）。 */
.loss-units { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.loss-unit-chip {
  min-height: var(--tap); padding: 0 12px;
  font-size: 13px; font-family: inherit;
  color: var(--ink); background: transparent;
  border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.loss-unit-chip.on {
  border-color: var(--action); color: var(--action);
  background: var(--action-soft); font-weight: 600;
}
.loss-unit-custom { margin-top: 8px; }
/* 原因：三列两行（spec 4.1.4），首屏比两列少一行。 */
.loss-reasons { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.loss-reason {
  padding: 9px 8px; font-size: 13px; font-family: inherit; text-align: center;
  color: var(--ink); background: transparent;
  border: 1px solid var(--line); border-radius: 8px; cursor: pointer;
}
.loss-reason.on { border-color: var(--action); color: var(--action); font-weight: 600; }
/* 选填三项收成一行小按钮（spec 4.1.5）：虚线 = 还没展开，实线 --action = 已展开。 */
/* 与上面原因区留一口气：首屏里吸底条正好切在这一行上沿时，别露出半截虚线按钮。 */
/* 选填行也单行横滑（2026-11-07）：第 4 颗「对应备货品」加进来后换行会多占 50px，
   而这一行必须只有一行高 —— 首屏要留给三项必填。 */
.loss-more {
  display: flex; flex-wrap: nowrap; gap: 8px; margin: 10px 0 12px;
  overflow-x: auto; padding-bottom: 2px; -webkit-overflow-scrolling: touch;
  scrollbar-width: none; scroll-snap-type: x proximity;
  -webkit-mask-image: linear-gradient(to right, currentColor calc(100% - 18px), transparent);
  mask-image: linear-gradient(to right, currentColor calc(100% - 18px), transparent);
}
.loss-more::-webkit-scrollbar { display: none; }
.loss-more > * { flex: 0 0 auto; scroll-snap-align: start; }
.loss-more-btn {
  min-height: var(--tap); padding: 0 12px;
  font-size: 12.5px; font-family: inherit;
  color: var(--ink-dim); background: transparent;
  border: 1px dashed var(--line-strong); border-radius: 999px; cursor: pointer;
}
.loss-more-btn.on { border-style: solid; border-color: var(--action); color: var(--action); }
.loss-more-btn.is-static { border-style: solid; cursor: default; }
/* 模板单行横滑（2026-11-07 实测）：5 颗换行要占 165px，首屏因此放不下「原因」第二行。
   横滑一屏只占一行，右边缘露半颗 = 可以滑（比卡片折叠少一次点击）。 */
.loss-chips {
  display: flex; flex-wrap: nowrap; gap: 8px;
  overflow-x: auto; padding-bottom: 2px; -webkit-overflow-scrolling: touch;
  scrollbar-width: none; scroll-snap-type: x proximity;
  /* 右缘淡出：露半颗 chip 表示「还能往左滑」，硬切一刀看着像溢出。 */
  -webkit-mask-image: linear-gradient(to right, currentColor calc(100% - 22px), transparent);
  mask-image: linear-gradient(to right, currentColor calc(100% - 22px), transparent);
}
.loss-chips::-webkit-scrollbar { display: none; }
.loss-chips > * { flex: 0 0 auto; scroll-snap-align: start; }
.loss-chip { display: inline-flex; align-items: stretch; border: 1px solid var(--line); border-radius: 999px; overflow: hidden; }
.loss-chip-main {
  display: flex; flex-direction: column; align-items: flex-start; gap: 1px;
  padding: 5px 12px; background: transparent; border: 0; color: var(--ink);
  font-family: inherit; font-size: 13px; cursor: pointer;
}
.loss-chip-main em { font-style: normal; font-size: 11px; color: var(--ink-dim); }
.loss-chip-x {
  border: 0; border-left: 1px solid var(--line); background: transparent;
  color: var(--ink-dim); font-size: 15px; padding: 0 10px; cursor: pointer;
}
.loss-chip-x:hover { color: var(--danger-bright); }
/* 模板删除的二次确认（spec 4.4）：× 变成一颗说人话的小按钮。 */
.loss-chip-x-confirm { font-size: 12px; font-weight: 600; color: var(--danger-bright); }
/* 最近记过的物料名：一排可点的 chips，点一下直接填（手机上的 datalist 补全不可靠）。
   「最近记过」四个字留在外面不跟着滚——第一版把它 sticky 在滚动容器里，chips 滑过去
   时左端被它那块背景截掉半个字，实测很难看（2026-11-07）。 */
.loss-quick { display: flex; align-items: baseline; gap: 6px; margin-top: 8px; min-width: 0; }
.loss-quick-hd { flex: 0 0 auto; font-size: 11px; color: var(--ink-dim); }
.loss-quick-list {
  display: flex; flex-wrap: nowrap; align-items: baseline; gap: 4px; min-width: 0;
  overflow-x: auto; padding-bottom: 2px; -webkit-overflow-scrolling: touch; scrollbar-width: none;
  scroll-snap-type: x proximity;
  -webkit-mask-image: linear-gradient(to right, currentColor calc(100% - 12px), transparent);
  mask-image: linear-gradient(to right, currentColor calc(100% - 12px), transparent);
}
.loss-quick-list::-webkit-scrollbar { display: none; }
.loss-quick-chip { flex: 0 0 auto; scroll-snap-align: start; }
.loss-quick-chip {
  padding: 5px 9px; font-size: 12.5px; font-family: inherit;
  color: var(--ink); background: transparent;
  border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.loss-quick-chip:active { border-color: var(--action); color: var(--action); }
/* 「最近记过」chip 上的单位（2026-11-07）：名字是主角，单位灰一档 —— 点一下两件一起填。 */
.loss-quick-unit { font-style: normal; margin-left: 3px; font-size: 11px; color: var(--ink-dim); }
/* 「从备货单选」入口：与「必填」同一行右侧的轻量链接，点开是底部弹层。
   不占 44px 行高 —— 首屏每一像素都要留给三项必填（主路径是手输与「最近记过」）。 */
.loss-label-row { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.loss-label-row .loss-label { margin-bottom: 5px; }
.loss-link-btn {
  padding: 2px 0; margin: 0;
  font-size: 12px; font-family: inherit; font-weight: 600;
  color: var(--action); background: transparent; border: 0; cursor: pointer;
}
/* 从备货单选：遮罩挡住底栏，弹层压在它上面；列表自己滚（备货品几十个）。 */
.loss-picker-scrim { position: fixed; inset: 0; z-index: var(--z-scrim); background: var(--scrim); }
.loss-picker {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: var(--z-dialog);
  display: flex; flex-direction: column; gap: 10px;
  max-height: 76vh; padding: 14px 14px calc(14px + var(--safe-b, 0px));
  background: var(--surface-0);
  border-top: 1px solid var(--line);
  border-top-left-radius: 16px; border-top-right-radius: 16px;
}
.loss-picker-hd {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  font-size: 14px; font-weight: 600; color: var(--ink);
}
.loss-picker-x {
  min-height: var(--tap); padding: 0 4px;
  font-size: 12px; font-family: inherit;
  color: var(--action); background: transparent; border: 0; cursor: pointer;
}
.loss-picker-list { list-style: none; margin: 0; padding: 0; overflow-y: auto; -webkit-overflow-scrolling: touch; }
.loss-picker-item {
  display: flex; align-items: baseline; justify-content: space-between; gap: 10px;
  width: 100%; min-height: var(--tap); padding: 0 2px;
  font-family: inherit; font-size: 13px; color: var(--ink); text-align: left;
  background: transparent; border: 0; border-top: 1px solid var(--line); cursor: pointer;
}
.loss-picker-item:first-child { border-top: 0; }
.loss-picker-item b { font-size: 14px; }
.loss-picker-item em { font-style: normal; font-size: 12px; color: var(--ink-dim); }
/* 日期快捷：今天 / 昨天。 */
.loss-days { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }
.loss-day {
  min-height: var(--tap); padding: 0 14px; font-size: 13px; font-family: inherit;
  color: var(--ink-dim); background: transparent;
  border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.loss-day.on { border-color: var(--action); color: var(--action); font-weight: 600; }
.loss-day:active { background: var(--action-soft); }
.loss-shots { display: flex; flex-wrap: wrap; gap: 8px; }
/* 现场照片展开后是表单里的一节：末尾一条淡分隔线把它与下面的选填行分开。 */
.loss-photo {
  margin-bottom: 14px; padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
}
.loss-shot { position: relative; margin: 0; width: 84px; height: 84px; border-radius: 8px; overflow: hidden; }
.loss-shot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.loss-shot-x {
  position: absolute; top: 2px; right: 2px; width: 20px; height: 20px; line-height: 1;
  border: 0; border-radius: 50%; background: var(--scrim); color: var(--ink); cursor: pointer;
}
.loss-shot-add {
  display: flex; align-items: center; justify-content: center; width: 84px; height: 84px;
  border: 1px dashed var(--line-strong); border-radius: 8px;
  background: transparent;
  font-family: inherit; font-size: 12px; color: var(--ink-dim);
  cursor: pointer; text-align: center;
}
/* 首位那颗「拍一张」：竖排（加号在上、字在下），触控面积就是一整格。 */
.loss-shot-camera { flex-direction: column; gap: 2px; }
.loss-shot-plus { font-size: 20px; line-height: 1; }
/* 全屏相机浮层（2026-11-11）：fixed 占满视口、内部 flex 撑满 —— 取景框不跟页面一起滚。
   z 用最高档 --z-queue（与图片灯箱同档）：相机开起来时底栏、吸底条都压不住它。 */
.loss-cam {
  position: fixed; inset: 0; z-index: var(--z-queue);
  display: flex; flex-direction: column;
  background: var(--surface-1);
  overscroll-behavior: contain;
}
.loss-cam-hd {
  flex: 0 0 auto;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: calc(10px + var(--safe-t)) 14px 10px;
  border-bottom: 1px solid var(--line);
}
/* 上传失败这句话必须出现在浮层里：吸底条那条错误提示被全屏浮层盖住了，用户看不到。 */
.loss-cam-err {
  flex: 0 0 auto; margin: 0; padding: 8px 14px;
  background: var(--danger-soft); color: var(--danger-bright);
  font-size: 13px; line-height: 1.5;
}
.loss-cam-title { margin: 0; font-size: 15px; font-weight: 700; color: var(--ink); }
.loss-cam-title b { color: var(--action); }
.loss-cam-sub { margin: 2px 0 0; font-size: 12px; line-height: 1.4; color: var(--ink-dim); }
.loss-cam-close {
  flex: 0 0 auto; min-height: var(--tap); padding: 0 18px;
  border: 1px solid var(--line-strong); border-radius: 999px;
  background: var(--surface-1); color: var(--ink);
  font-family: inherit; font-size: 14px; font-weight: 600; cursor: pointer;
}
/* 已经拍好的缩略图：横滑一条，不抢取景框的高度。 */
.loss-cam-shots {
  flex: 0 0 auto; display: flex; gap: 8px;
  padding: 8px 14px;
  overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none;
}
.loss-cam-shots::-webkit-scrollbar { display: none; }
.loss-cam-shot {
  position: relative; margin: 0; flex: 0 0 auto;
  width: 52px; height: 52px; border-radius: 6px; overflow: hidden;
}
.loss-cam-shot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.loss-cam-stage { flex: 1 1 auto; min-height: 0; display: flex; background: var(--surface-2); }
/* 组件本身是「提示 + 视频 + 一排按钮」的竖排：外层撑满，视频吃掉剩余高度并 cover 铺满。
   组件里视频原来 min-height 240、object-fit contain（页内那一版），全屏时会留黑边与空隙。 */
.loss-cam-stage :deep(.live-camera) { flex: 1 1 auto; min-height: 0; gap: 0; }
.loss-cam-stage :deep(.live-video) {
  flex: 1 1 auto; min-height: 0; height: auto; width: 100%;
  object-fit: cover; border: 0; border-radius: 0;
}
.loss-cam-stage :deep(.live-actions) {
  flex: 0 0 auto; gap: 9px;
  padding: 10px 14px calc(10px + var(--safe-b));
  background: var(--surface-1);
}
.loss-cam-stage :deep(.live-hint) { padding: 8px 14px 0; }
.loss-cam-stage :deep(.live-alert) { margin: 10px 14px; }
.loss-cam-stage :deep(.live-retry) { margin: 0 14px; }
.loss-edit-photo { margin: -4px 0 12px; }
/* 表单块底部留出吸底条高度（spec 4.1.8）：吸底条浮在视口下沿时，最后一个字段还能被滚到
   它上面，不会被按钮盖住。 */
.loss-form { padding-bottom: calc(var(--tap) + 24px); }
/* 吸底条：提示在上（错误 / 成功都在这）、按钮在下；手机档让开底栏，桌面档只让 8px。 */
.loss-actions {
  position: sticky;
  bottom: calc(8px + var(--safe-b, 0px));
  z-index: var(--z-shell);
  display: flex; flex-direction: column; gap: 8px;
  padding: 10px 0 4px;
  background: var(--surface-0);
  border-top: 1px solid var(--line);
}
.loss-feedback { margin: 0; }
/* 吸底条上方淡出：首屏里它正好切在「现场照片 / 备注 / 日期」那行上沿，硬切会露出一条
   半截按钮（实测 390×844）。渐变把这截内容柔化掉，同时暗示「下面还有东西」。 */
.loss-actions::before {
  content: ''; position: absolute; left: 0; right: 0; top: -14px; height: 14px;
  background: linear-gradient(to bottom, transparent, var(--surface-0));
  pointer-events: none;
}
.loss-actions-row { display: flex; gap: 10px; }
/* 触控下限走令牌（--tap = 44px）：手机上登记是常做的事，按钮不该小于一指宽。 */
.loss-actions .btn { min-height: var(--tap); }
.loss-actions .btn-primary { flex: 1 1 auto; justify-content: center; }
@media (max-width: 720px) {
  .loss-actions { bottom: calc(var(--tab-h, 56px) + var(--safe-b, 0px) + 8px); }
}
.loss-list { list-style: none; margin: 0; padding: 0; }
.loss-item { padding: 9px 0; border-top: 1px solid var(--line); }
.loss-item:first-child { border-top: 0; }
/* 刚提交的那一条：员工一眼确认「刚才这笔在」。 */
.loss-item.is-new {
  margin: 0 -6px; padding: 9px 6px; border-radius: 8px;
  background: var(--action-soft);
  box-shadow: inset 3px 0 0 var(--action);
}
/* 正在改的那一条：和表单里的编辑态对上号。 */
.loss-item.is-editing { background: var(--action-soft); box-shadow: inset 3px 0 0 var(--action); }
.loss-item-hd { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.loss-date { font-size: 12px; color: var(--ink-dim); font-variant-numeric: tabular-nums; }
.loss-name { font-size: 14px; }
.loss-qty { font-size: 13px; color: var(--action); font-variant-numeric: tabular-nums; }
.loss-reason-tag {
  font-size: 11px; padding: 1px 8px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--ink-dim);
}
/* 更正入口：小按钮、放在标题行下面一行（390px 宽度下挤进标题行会换行打结）。 */
.loss-item-acts { display: flex; gap: 6px; margin-top: 6px; }
.loss-item-btn {
  min-height: var(--tap); padding: 0 12px;
  font-size: 12px; font-family: inherit;
  color: var(--ink-dim); background: transparent;
  border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.loss-item-btn.is-danger { border-color: var(--danger-bright); color: var(--danger-bright); font-weight: 600; }
.loss-item-btn:disabled { opacity: .5; }
.loss-remark { margin: 4px 0 0; font-size: 12px; color: var(--ink-dim); }
.loss-thumbs { display: flex; gap: 6px; margin-top: 6px; }
.loss-thumbs img { width: 46px; height: 46px; object-fit: cover; border-radius: 6px; display: block; }
.loss-foot { font-size: 11.5px; line-height: 1.7; color: var(--ink-dim); }
</style>
