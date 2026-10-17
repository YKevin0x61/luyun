<script setup>
/**
 * 员工端的「损耗登记」页（从 worktool2 那个小程序的 `pages/wastage/add` 搬过来）。
 *
 * 一页回答两件事：**记一笔**（物料 / 数量与单位 / 原因 / 日期 / 最多 3 张现场照片 / 备注），
 * 以及**看自己最近记的几笔**（员工最常问的是「我那天记了没有」）。
 *
 * 两个身份都能进（页面清单里是 `audience: both`）：员工用员工会话、店长用管理端会话。
 * 接口按**归属**分——员工只看得到自己记的，全店数据在管理端的 `/workbench/kitchen/loss`。
 * 所以这一页上没有任何「看别人的」入口。
 *
 * 三样东西来自服务端，页面不写第二份：`GET /api/wastage/meta`（六个原因、常用单位、
 * 那四个上限、今天）、`/templates`（我存过的 + 我最近常这么记的）、`/suggestions`
 * （填名字时的补全）。日期用站内日期组件（原生 `<input type=date>` 的显示格式跟着浏览器
 * 语言走，页面控制不了 —— 2026-10-13 用户裁定）。
 *
 * 反馈走页内一句话（不是 toast），与「今天」页、加班页同一个写法。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import { api } from '../../api/client.js'
import { loginRedirectTarget } from '../../utils/loginNext'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'

const router = useRouter()

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

function formatQuantity(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  // 整数不拖一串 0（1 而不是 1.000），小数最多三位（与库里的刻度一致）。
  return String(Math.round(number * 1000) / 1000)
}

async function load(quiet = false) {
  if (!quiet) state.value = 'loading'
  try {
    const [meta, tpl, sug, list] = await Promise.all([
      api.get('/api/wastage/meta'),
      api.get('/api/wastage/templates'),
      api.get('/api/wastage/suggestions'),
      api.get('/api/wastage/records', { limit: 8 }),
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
    const list = await api.get('/api/wastage/records', { limit: 8 })
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
    await api.post('/api/wastage/templates', {
      item_name: itemName.value.trim(),
      unit: unit.value,
      reason: reason.value,
    })
    note.value = '已存为常用模板，下次点一下就能填好。'
    templates.value = (await api.get('/api/wastage/templates')).templates || []
  } catch (err) {
    if (err.status === 401) return redirectLogin()
    formError.value = err.message || '存模板失败'
  }
}

async function removeTemplate(tpl) {
  if (!tpl.id) return
  formError.value = ''
  try {
    await api.delete(`/api/wastage/templates/${tpl.id}`)
    templates.value = (await api.get('/api/wastage/templates')).templates || []
  } catch (err) {
    if (err.status === 401) return redirectLogin()
    formError.value = err.message || '删模板失败'
  }
}

/** 拍照回来的那一张（系统相机一次交一张；循环留着是因为桌面档的 file input 仍然可能
 *  一次给出多张，那条路不该因为这次改动就坏掉）。 */
async function onPickFiles(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  formError.value = ''
  for (const file of files) {
    if (images.value.length >= maxImages.value) {
      formError.value = `现场照片最多 ${maxImages.value} 张`
      break
    }
    await uploadOne(file)
  }
}

async function uploadOne(file) {
  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const data = await api.upload('/api/wastage/images', form)
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
    const data = await api.post('/api/wastage/records', {
      item_name: itemName.value.trim(),
      quantity: Number(quantity.value),
      unit: unit.value,
      reason: reason.value,
      remark: remark.value.trim(),
      wastage_date: day.value,
      image_ids: images.value.map((row) => row.capture_id),
    })
    const record = data.record
    note.value = `已记下：${record.item_name} ${formatQuantity(record.quantity)}${record.unit} · ${record.reason_text}`
    resetForm()
    await loadRecords()
    // 名字建议要跟着长：这一笔的物料名下次该能补全出来。
    suggestions.value = (await api.get('/api/wastage/suggestions')).suggestions || []
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

onMounted(() => {
  document.title = workbenchDocumentTitle('损耗登记')
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

      <!-- 现场照片**排在最前**（2026-10-17 用户裁定）：损耗是当场发生的事 —— 先把眼前
           这一堆拍下来，再慢慢填物料与数量。点一下就**直接调起系统相机**
           （`capture="environment"` 让手机浏览器开相机而不是弹「拍照 / 相册」菜单），
           所以这里没有「从相册选」这条路：登记的是此刻现场，不是相册里的旧图。
           一次拍一张（系统相机就是一张一交），最多三张。 -->
      <section class="loss-block">
        <div class="loss-block-hd">
          <span>现场照片</span>
          <span class="loss-hint">最多 {{ maxImages }} 张，每张 {{ maxImageMb }} MB 以内</span>
        </div>
        <div class="loss-shots">
          <figure v-for="(shot, index) in images" :key="shot.capture_id" class="loss-shot">
            <img :src="shot.url" alt="现场照片" />
            <button type="button" class="loss-shot-x" :aria-label="`删掉第 ${index + 1} 张`" @click="removeImage(index)">×</button>
          </figure>
          <label v-if="images.length < maxImages" class="loss-shot-add loss-shot-camera">
            <input
              type="file"
              accept="image/*"
              capture="environment"
              class="loss-file"
              @change="onPickFiles"
            />
            <span class="loss-shot-plus" aria-hidden="true">＋</span>
            <span>{{ uploading ? '上传中…' : '拍一张' }}</span>
          </label>
        </div>
        <p class="loss-hint loss-photo-tip">点一下打开相机；拍完先搁着，接着填下面这一笔。</p>
      </section>

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
.loss-shot { position: relative; margin: 0; width: 84px; height: 84px; border-radius: 8px; overflow: hidden; }
.loss-shot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.loss-shot-x {
  position: absolute; top: 2px; right: 2px; width: 20px; height: 20px; line-height: 1;
  border: 0; border-radius: 50%; background: var(--scrim); color: var(--ink); cursor: pointer;
}
.loss-shot-add {
  display: flex; align-items: center; justify-content: center; width: 84px; height: 84px;
  border: 1px dashed var(--line-strong); border-radius: 8px;
  font-size: 12px; color: var(--ink-dim); cursor: pointer; text-align: center;
}
/* 首位那颗「拍一张」：竖排（加号在上、字在下），触控面积就是一整格。 */
.loss-shot-camera { flex-direction: column; gap: 2px; }
.loss-shot-plus { font-size: 20px; line-height: 1; }
.loss-photo-tip { margin: 8px 0 0; }
.loss-file { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
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
