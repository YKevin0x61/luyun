<script setup>
/**
 * 员工端的「损耗登记」页（从 worktool2 那个小程序的 `pages/wastage/add` 搬过来）。
 *
 * 一页回答两件事：**记一笔**（物料 / 数量与单位 / 原因 / 日期 / 最多 3 张现场照片 / 备注），
 * 以及**看自己最近记的几笔**（员工最常问的是「我那天记了没有」）。
 *
 * 两个身份都能进（页面清单里是 `audience: both`）：员工用员工会话、店长用管理端会话。
 * 接口按**归属**分——员工只看得到自己记的；全店统计与明细在 `/workbench/kitchen/loss`
 * （管理端，或勾了「损耗分析」能力的员工）。所以这一页上没有任何「看别人的」入口。
 *
 * 三样东西来自服务端，页面不写第二份：`GET /api/wastage/meta`（六个原因、常用单位、
 * 那四个上限、今天）、`/templates`（我存过的 + 我最近常这么记的）、`/suggestions`
 * （填名字时的补全）。日期用站内日期组件（原生 `<input type=date>` 的显示格式跟着浏览器
 * 语言走，页面控制不了 —— 2026-10-13 用户裁定）。
 *
 * 反馈走页内一句话（不是 toast），与「今天」页、加班页同一个写法。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
// 页内取景用卫生那套现成的相机（`getUserMedia` + canvas 出 JPEG）：2026-10-17 用户裁定
// 「像拍卫生照一样，不跳系统相机」。它同时解决了 iPhone 的 HEIC —— 页内相机拍出来直接是
// JPEG；走系统相机拿到的是 HEIC，后端认不出格式会拒（用户真机上撞的就是这个）。
import HygieneLiveCamera from '../../components/hygiene/HygieneLiveCamera.vue'
import { api } from '../../api/client.js'
import { loginRedirectTarget } from '../../utils/loginNext'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { IDENTITY_ADMIN, IDENTITY_STAFF } from '../../utils/workbenchIdentity'

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
const today = ref('')

// 表单。
const itemName = ref('')
const quantity = ref('')
const unitChoice = ref('')
const unitCustom = ref('')
const reason = ref('')
const remark = ref('')
const day = ref('')
const images = ref([])
const uploading = ref(false)
/** 页内相机是否展开：点「拍一张」就地打开取景框（不跳系统相机），拍满 3 张自动收起。 */
const cameraOpen = ref(false)

const templates = ref([])
const suggestions = ref([])
const records = ref([])
const total = ref(0)

const busy = ref(false)
const formError = ref('')
const note = ref('')

const reasonsById = computed(() =>
  Object.fromEntries(reasons.value.map((row) => [row.value, row.label])),
)
/** 单位：选单里的是常用候选，`__custom` 那一档给「罐 / 箱」这种没列出来的。 */
const unit = computed(() =>
  unitChoice.value === '__custom' ? unitCustom.value.trim() : unitChoice.value,
)
const isOther = computed(() => reason.value === 'other')
const canSubmit = computed(
  () => !!itemName.value.trim() && !!quantity.value && !!unit.value && !!reason.value && !busy.value,
)
const maxImageMb = computed(() => Math.max(1, Math.round(maxImageBytes.value / (1024 * 1024))))

function redirectLogin() {
  const target = loginRedirectTarget(router.currentRoute.value)
  if (target) router.replace(target)
}

/** 这一轮请求按哪套凭据解析：员工档声明 staff、超管档声明 admin。
 *
 *  共用电脑上两套 cookie 都在（ADR 0091），而后端默认「管理端优先」—— 员工在员工页记的
 *  一笔会归属成管理端、明细还能看到全店。所以页面按**此刻的工作台身份**显式声明一次。
 *  声明只选凭据、不放宽校验（同 WS 的 `?identity=`）：那一支没有有效会话就是 401，
 *  不回落到另一支。
 *
 *  身份探针还没给出结论（`null`）时**不声明**：退回旧的 cookie 优先级（顶多还是旧行为），
 *  绝不自作主张猜一个声明去提权；探针回来后下面的 watch 会按新身份重读一次。
 */
function identityParams() {
  const identity = identityStore.identity
  if (identity === IDENTITY_STAFF) return { identity: 'staff' }
  if (identity === IDENTITY_ADMIN) return { identity: 'admin' }
  return {}
}

/** 给 `api.upload` 用：它只收 path（没有 params 参数），所以查询串直接拼在路径上。 */
function withIdentity(path) {
  const { identity } = identityParams()
  return identity ? `${path}?identity=${identity}` : path
}

// 身份探针晚于本页第一轮请求时（切换器先发起探针、或两套会话都在的共用电脑），拿到结论
// 后按新身份重读：否则员工页上还是按管理端拿到的全店视图。
watch(
  () => identityStore.identity,
  (next, prev) => {
    if (next && next !== prev && state.value === 'ready') load(true)
  },
)

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
    const [meta, tpl, sug, list] = await Promise.all([
      api.get('/api/wastage/meta', params),
      api.get('/api/wastage/templates', params),
      api.get('/api/wastage/suggestions', params),
      api.get('/api/wastage/records', { ...params, limit: 8 }),
    ])
    reasons.value = meta.meta.reasons
    units.value = meta.meta.units
    maxImages.value = meta.meta.max_images
    maxItemName.value = meta.meta.max_item_name
    maxRemark.value = meta.meta.max_remark
    maxTemplates.value = meta.meta.max_templates
    maxImageBytes.value = meta.meta.max_image_bytes
    today.value = meta.today
    templates.value = tpl.templates || []
    suggestions.value = sug.suggestions || []
    records.value = list.records || []
    total.value = list.total || 0
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
  } catch (err) {
    if (err.status !== 401) formError.value = err.message || '读不到最近的记录'
  }
}

function pickTemplate(tpl) {
  if (tpl.id && tpl.item_name === itemName.value && tpl.unit === unit.value && tpl.reason === reason.value) {
    // 再点一下就是取消（同源项目）。
    itemName.value = ''
    reason.value = ''
    unitChoice.value = ''
    unitCustom.value = ''
    return
  }
  itemName.value = tpl.item_name
  reason.value = tpl.reason
  if (units.value.includes(tpl.unit)) {
    unitChoice.value = tpl.unit
  } else {
    unitChoice.value = '__custom'
    unitCustom.value = tpl.unit
  }
}

async function saveAsTemplate() {
  formError.value = ''
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
    templates.value = (await api.get('/api/wastage/templates', identityParams())).templates || []
  } catch (err) {
    if (err.status === 401) return redirectLogin()
    formError.value = err.message || '存模板失败'
  }
}

async function removeTemplate(tpl) {
  if (!tpl.id) return
  formError.value = ''
  try {
    await api.delete(`/api/wastage/templates/${tpl.id}`, identityParams())
    templates.value = (await api.get('/api/wastage/templates', identityParams())).templates || []
  } catch (err) {
    if (err.status === 401) return redirectLogin()
    formError.value = err.message || '删模板失败'
  }
}

/** 点「拍一张」：就地展开取景框（页内相机，不跳系统相机）。 */
function openCamera() {
  if (images.value.length >= maxImages.value) {
    formError.value = `现场照片最多 ${maxImages.value} 张`
    return
  }
  formError.value = ''
  cameraOpen.value = true
}

function closeCamera() {
  cameraOpen.value = false
}

/** 页内相机交回一张 JPEG（长边 1600、q0.75）：直接传，传完缩略图就出现在首位那块里。
 *
 *  拍满 3 张自动收起相机 —— 否则取景框还开着、按钮已经点不动，看着像卡住了。 */
async function onCaptured(blob) {
  await uploadOne(blob, 'wastage.jpg')
  if (images.value.length >= maxImages.value) cameraOpen.value = false
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
  reason.value = ''
  remark.value = ''
  images.value = []
  // 日期与单位留着：连着记几笔时那两样通常是一样的。
  day.value = today.value || day.value
}

async function submit() {
  if (busy.value) return
  busy.value = true
  formError.value = ''
  note.value = ''
  try {
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
      },
      identityParams(),
    )
    const record = data.record
    note.value = `已记下：${record.item_name} ${formatQuantity(record.quantity)}${record.unit} · ${record.reason_text}`
    resetForm()
    await loadRecords()
    // 名字建议要跟着长：这一笔的物料名下次该能补全出来。
    suggestions.value =
      (await api.get('/api/wastage/suggestions', identityParams())).suggestions || []
  } catch (err) {
    if (err.status === 401) {
      redirectLogin()
      return
    }
    formError.value = err.message || '提交失败'
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  document.title = workbenchDocumentTitle('损耗登记')
  // 共用电脑上两套 cookie 都在：先等身份探针给出结论，别让员工那一档先按管理端
  // 拿到一次全店明细。探针由壳 / 切换器发起；这里只在它还没开始时补一次（在飞时
  // `refresh()` 直接返回，由上面的 watch 在结论回来后重读）。
  if (!identityStore.probed) await identityStore.refresh()
  load()
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
      <p v-if="formError" class="loss-error" role="alert">{{ formError }}</p>
      <p v-if="note" class="loss-note" role="status">{{ note }}</p>

      <!-- 常用模板：我存过的（可删）在前，我最近常这么记的在后。 -->
      <section class="loss-block">
        <div class="loss-block-hd">
          <span>常用模板</span>
          <span class="loss-hint">点一下填好</span>
        </div>
        <div v-if="templates.length" class="loss-chips">
          <span v-for="(tpl, index) in templates" :key="`${tpl.item_name}-${tpl.unit}-${tpl.reason}-${index}`" class="loss-chip">
            <button type="button" class="loss-chip-main" @click="pickTemplate(tpl)">
              <b>{{ tpl.item_name }}</b>
              <em>{{ tpl.unit }} · {{ tpl.reason_text }}</em>
            </button>
            <button
              v-if="tpl.is_custom"
              type="button"
              class="loss-chip-x"
              :aria-label="`删除模板 ${tpl.item_name}`"
              @click="removeTemplate(tpl)"
            >×</button>
          </span>
        </div>
        <p v-else class="loss-muted">
          还没有模板：填好下面这一笔后点「存为模板」，下次记同样的东西点一下就行（最多 {{ maxTemplates }} 个）。
        </p>
      </section>

      <form class="loss-block" @submit.prevent="submit">
        <div class="loss-block-hd"><span>这一笔损耗</span></div>

        <!-- 现场照片是**这一笔损耗的一部分**（2026-10-30 用户裁定）：拍的就是眼前这一笔，
             别让它浮在卡片外面自成一块。位置仍在表单第一项 —— 先把这一堆拍下来，再往下
             填物料与数量。点「拍一张」**就地展开取景框**（页内相机，不跳系统相机）：
             拍出来是 JPEG，也没有「从相册选」这条路 —— 登记的是此刻现场。一次一张、
             最多三张，拍满自动收起。 -->
        <div class="loss-photo">
          <div class="loss-photo-hd">
            <span class="loss-label">现场照片</span>
            <span class="loss-hint">最多 {{ maxImages }} 张，每张 {{ maxImageMb }} MB 以内</span>
          </div>
          <div class="loss-shots">
            <figure v-for="(shot, index) in images" :key="shot.capture_id" class="loss-shot">
              <img :src="shot.url" alt="现场照片" />
              <button type="button" class="loss-shot-x" :aria-label="`删掉第 ${index + 1} 张`" @click="removeImage(index)">×</button>
            </figure>
            <button
              v-if="images.length < maxImages && !cameraOpen"
              type="button"
              class="loss-shot-add loss-shot-camera"
              @click="openCamera"
            >
              <span class="loss-shot-plus" aria-hidden="true">＋</span>
              <span>{{ uploading ? '上传中…' : '拍一张' }}</span>
            </button>
          </div>

          <!-- 页内相机：点「拍一张」**就地**展开取景框，拍完立刻上传 —— 不是跳系统相机，
               所以拍出来是 JPEG（iPhone 系统相机给的 HEIC 后端认不出）。相机组件是卫生
               那套（`components/hygiene/HygieneLiveCamera.vue`）：后置镜头、可切广角，
               拍满 3 张自动收起。 -->
          <div v-if="cameraOpen" class="loss-camera">
            <HygieneLiveCamera @captured="onCaptured" />
            <div class="loss-camera-actions">
              <button type="button" class="btn" @click="closeCamera">收起相机</button>
              <span class="loss-hint">
                {{ uploading ? '正在上传…' : `拍完自动上传，最多 ${maxImages} 张` }}
              </span>
            </div>
          </div>

          <p class="loss-hint loss-photo-tip">点「拍一张」就地打开相机；拍完先搁着，接着填物料与数量。</p>
        </div>

        <label class="loss-field">
          <span class="loss-label">物料名称</span>
          <input
            v-model="itemName"
            class="loss-input"
            type="text"
            :maxlength="maxItemName"
            list="wastage-item-names"
            placeholder="例如：五花肉"
            required
          />
          <datalist id="wastage-item-names">
            <option v-for="name in suggestions" :key="name" :value="name" />
          </datalist>
        </label>

        <div class="loss-row">
          <label class="loss-field loss-grow">
            <span class="loss-label">损耗数量</span>
            <input
              v-model="quantity"
              class="loss-input"
              type="number"
              inputmode="decimal"
              step="0.001"
              min="0"
              placeholder="例如：1.5"
              required
            />
          </label>
          <label class="loss-field loss-unit">
            <span class="loss-label">单位</span>
            <select v-model="unitChoice" class="loss-input" required>
              <option value="" disabled>选择</option>
              <option v-for="item in units" :key="item" :value="item">{{ item }}</option>
              <option value="__custom">其他…</option>
            </select>
          </label>
        </div>
        <label v-if="unitChoice === '__custom'" class="loss-field">
          <span class="loss-label">填单位</span>
          <input v-model="unitCustom" class="loss-input" type="text" maxlength="10" placeholder="例如：罐" />
        </label>

        <div class="loss-field">
          <span class="loss-label">损耗原因</span>
          <div class="loss-reasons">
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

        <label class="loss-field">
          <span class="loss-label">哪一天</span>
          <LuyunDatePicker v-model="day" :max-date="today" placeholder="选择日期" aria-label="损耗日期" />
        </label>

        <label class="loss-field">
          <span class="loss-label">
            备注<span v-if="isOther" class="loss-required">必填（选了「其他」要说明）</span>
            <span v-else class="loss-hint">选填</span>
          </span>
          <textarea
            v-model="remark"
            class="loss-input loss-textarea"
            :maxlength="maxRemark"
            rows="2"
            :placeholder="isOther ? '具体是什么情况…' : '补充说明…'"
          />
        </label>

        <div class="loss-actions">
          <button class="btn btn-primary" type="submit" :disabled="!canSubmit">
            {{ busy ? '提交中…' : '确认登记' }}
          </button>
          <button
            class="btn"
            type="button"
            :disabled="!itemName.trim() || !unit || !reason"
            @click="saveAsTemplate"
          >存为模板</button>
        </div>
      </form>

      <section class="loss-block">
        <div class="loss-block-hd">
          <span>我最近的登记</span>
          <span v-if="total" class="loss-hint">共 {{ total }} 笔</span>
        </div>
        <p v-if="!records.length" class="loss-muted">还没有登记过。上面这一笔记完就会出现在这里。</p>
        <ul v-else class="loss-list">
          <li v-for="row in records" :key="row.id" class="loss-item">
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
          </li>
        </ul>
      </section>

      <p class="loss-foot">
        损耗记的是已经发生的事：日期最晚到今天。数量按实际报损填（1.5 kg、半盒都行），
        「其他」那一档要写一句说明 —— 不然月底的统计里看不出是什么情况。
      </p>
    </template>
  </div>
</template>

<style scoped>
.loss-page {
  padding: 12px 14px 28px;
  /* 登记是**一列表单**的事：桌面档拉到 1400px 宽只会让输入框长得没法看，所以给内容
     一个宽度上限并居中（手机档本来就比它窄，这条不生效）。 */
  max-width: 720px;
  margin: 0 auto;
}
.loss-muted { font-size: 13px; color: var(--ink-dim); }
.loss-error { font-size: 13px; color: var(--danger-bright); }
.loss-note { font-size: 13px; color: var(--action); }
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
.loss-input:focus { border-color: var(--action); }
.loss-textarea { resize: vertical; }
.loss-row { display: flex; gap: 10px; }
.loss-grow { flex: 1 1 auto; }
.loss-unit { flex: 0 0 108px; }
.loss-reasons { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.loss-reason {
  padding: 9px 10px; font-size: 13px; font-family: inherit; text-align: left;
  color: var(--ink); background: transparent;
  border: 1px solid var(--line); border-radius: 8px; cursor: pointer;
}
.loss-reason.on { border-color: var(--action); color: var(--action); font-weight: 600; }
.loss-chips { display: flex; flex-wrap: wrap; gap: 8px; }
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
.loss-shots { display: flex; flex-wrap: wrap; gap: 8px; }
/* 现场照片是「这一笔损耗」里的第一项（2026-10-30 用户裁定）：标题走字段字号（区别于
   卡片标题），末尾一条淡分隔线把它与下面的物料 / 数量分开 —— 是表单里的一节，不是一张卡。 */
.loss-photo {
  margin-bottom: 14px; padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
}
.loss-photo-hd {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  margin-bottom: 8px;
}
.loss-photo-hd .loss-label { margin-bottom: 0; }
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
/* 页内相机：取景框 + 一条「收起相机」。视频尺寸由组件自己定（宽 100%、最小 240px 高）。 */
.loss-camera { display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
.loss-camera-actions { display: flex; align-items: center; gap: 10px; }
.loss-photo-tip { margin: 8px 0 0; }
.loss-actions { display: flex; gap: 10px; }
/* 触控下限走令牌（`--tap` = 44px）：手机上登记是常做的事，按钮不该小于一指宽。 */
.loss-actions .btn { min-height: var(--tap); }
.loss-actions .btn-primary { flex: 1 1 auto; justify-content: center; }
.loss-list { list-style: none; margin: 0; padding: 0; }
.loss-item { padding: 9px 0; border-top: 1px solid var(--line); }
.loss-item:first-child { border-top: 0; }
.loss-item-hd { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.loss-date { font-size: 12px; color: var(--ink-dim); font-variant-numeric: tabular-nums; }
.loss-name { font-size: 14px; }
.loss-qty { font-size: 13px; color: var(--action); font-variant-numeric: tabular-nums; }
.loss-reason-tag {
  font-size: 11px; padding: 1px 8px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--ink-dim);
}
.loss-remark { margin: 4px 0 0; font-size: 12px; color: var(--ink-dim); }
.loss-thumbs { display: flex; gap: 6px; margin-top: 6px; }
.loss-thumbs img { width: 46px; height: 46px; object-fit: cover; border-radius: 6px; display: block; }
.loss-foot { font-size: 11.5px; line-height: 1.7; color: var(--ink-dim); }
</style>
