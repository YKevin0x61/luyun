<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import HygieneImageLightbox from '../../components/hygiene/HygieneImageLightbox.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { formatHygieneStamp } from '../../utils/hygieneTime'

const KINDS = [
  { value: 'daily', label: '日常实拍' },
  { value: 'deep', label: '专项前后' },
  { value: 'fix', label: '整改原图' },
  { value: 'fix_reshoot', label: '整改回拍' },
  { value: 'teaching', label: '卫生教材' },
  { value: 'standard', label: '标准图版本' },
  { value: 'attire', label: '仪容仪表' },
]
// 范围清理不含整改原图：整单删除是整改页的能力（ADR-0087），
// 免得「清旧照片」顺手删掉还没做完的工作单。
const PURGE_KINDS = KINDS.filter((kind) => kind.value !== 'fix')

const PAGE_SIZE = 24

const dateFrom = ref('')
const dateTo = ref('')
const selectedKinds = ref(KINDS.map((kind) => kind.value))
const zones = ref([])
const zoneId = ref('')

const records = ref([])
const total = ref(0)
const page = ref(1)
const truncated = ref(false)
const rangeLabel = ref('')
const loading = ref(true)
/** 动作类错误（删除 / 清理 / 导出）：显示在页头下方 */
const errorText = ref('')
/** 列表加载失败：列表区内给「重试」，与动作错误分开 */
const loadError = ref('')

// 筛选条默认收起：折叠态是一行摘要 + 查询按钮；展开才是日期/工作区/类型。
const filtersOpen = ref(false)
// 上一次「已查询」的参数快照：参数变了查询按钮才高亮
const appliedSnapshot = ref('')

/** 「近一年」：勾上后按 今天-365 天 ~ 今天查（后端 MAX_RANGE_DAYS = 366）。
 *  为什么需要它：存储概况是全量统计，而列表默认只查最近 7 天 —— 老记录（比如 9 月的
 *  整改原图）在默认区间里必然 0 条，用户看不出「概况 2 张、列表 0 条」是日期造成的。
 *  点类型 chip 时会自动打开它（见 toggleKindFilter），保证「点整改原图」能看到那 2 条。 */
const allTime = ref(false)

const storage = ref(null)
const storageHint = ref('')

const lightbox = ref(null)
// 取图 404 的 capture_id（库里还有记录、文件已经不在磁盘上）。只记 id，不改 record，
// 列表刷新后按新数据重新判。
const missingPhotos = ref(new Set())
function photoMissing(captureId) {
  return missingPhotos.value.has(captureId)
}
function markPhotoMissing(captureId) {
  const next = new Set(missingPhotos.value)
  next.add(captureId)
  missingPhotos.value = next
}
const deleteTarget = ref(null)
const busy = ref(false)

const purgeOpen = ref(false)
/** 「按区间清理」共用列表那份筛选（v3 布局纪律）：不再维护第二套类型复选，
   点清理时把当前筛中的类型直接带进确认框。 */
function startPurge() {
  purgeKinds.value = [...selectedKinds.value]
  purgeOpen.value = true
}
/** 页头「清理…」：把当前筛选直接带进确认弹窗（弹窗里要求输入「清理」两字才可确认）。 */
function openPurge() {
  if (loading.value || busy.value) return
  startPurge()
}
const purgeKinds = ref(PURGE_KINDS.map((kind) => kind.value))

const exportJob = ref(null)
const exportToday = ref(false)
const exportError = ref('')
const exportHint = ref('')
let pollTimer = null

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const canPurge = computed(() => purgeKinds.value.length > 0 && !busy.value)

function isoDate(date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function defaultRange() {
  const to = new Date()
  const from = new Date(to.getTime() - 6 * 86400000)
  return { from: isoDate(from), to: isoDate(to) }
}

onMounted(async () => {
  const range = defaultRange()
  dateFrom.value = range.from
  dateTo.value = range.to
  await Promise.all([loadZones(), loadRecords(), loadStorage()])
})

onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer)
})

useHygieneRealtime({
  id: 'hygiene-admin-data',
  resources: ['daily', 'deep', 'fix', 'teaching', 'zones'],
  // 只刷列表：存储概况要扫一遍照片目录，不该被别人的提交牵着反复扫。
  pull: loadRecords,
})

/** 今天往前 n 天的 YYYY-MM-DD（本地日期，不走 toISOString 以免 UTC 偏移一天）。 */
function daysAgoIso(days) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 「近一年」的最大跨度：后端 `MAX_RANGE_DAYS = 366`，再多就 400 `range_too_wide`
 *  （写死 2000-01-01 试过，接口直接 400）。366 天覆盖得住卫生数据的保留期。 */
const ALL_TIME_DAYS = 365

function queryParams() {
  // 「近一年」给明确下界：后端 parse_archive_range 收到 None 只回最近 30 天，
  // 那正是老记录（9 月的整改原图）看不到的原因。上限卡在 365 天（见上）。
  const date_from = allTime.value ? daysAgoIso(ALL_TIME_DAYS) : (dateFrom.value || undefined)
  const date_to = allTime.value ? daysAgoIso(0) : (dateTo.value || undefined)
  return {
    kinds: selectedKinds.value.join(','),
    date_from,
    date_to,
    zone_id: zoneId.value === '' ? undefined : zoneId.value,
  }
}

async function loadZones() {
  try {
    const data = await api.get('/api/hygiene/admin/zones')
    zones.value = data.zones || []
  } catch (err) {
    zones.value = []
  }
}

async function loadRecords() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await api.get('/api/hygiene/admin/data/records', {
      ...queryParams(),
      page: page.value,
      page_size: PAGE_SIZE,
    })
    records.value = data.items || []
    total.value = data.total || 0
    truncated.value = Boolean(data.truncated)
    rangeLabel.value = `${data.date_from} ~ ${data.date_to}`
    appliedSnapshot.value = JSON.stringify(queryParams())
    if (page.value > pageCount.value) {
      page.value = pageCount.value
      await loadRecords()
    }
  } catch (err) {
    records.value = []
    total.value = 0
    loadError.value = err.message || '无法加载卫生数据'
  } finally {
    loading.value = false
  }
}

async function applyFilters() {
  if (!selectedKinds.value.length) {
    // 空类型在查询里等于"全部"，那是用户没表达清楚，不是他要全选。
    errorText.value = '请至少选一个类型再查询。'
    return
  }
  errorText.value = ''
  page.value = 1
  await loadRecords()
  filtersOpen.value = false
}

async function loadStorage() {
  try {
    storage.value = await api.get('/api/hygiene/admin/data/storage')
  } catch (err) {
    storage.value = null
  }
}

function photoUrl(captureId, variant = 'thumb') {
  return `/api/hygiene/admin/data/photo/${encodeURIComponent(captureId)}?variant=${variant}`
}

function openPhoto(record, photo) {
  lightbox.value = {
    src: photoUrl(photo.capture_id, 'preview'),
    alt: `${record.kind_label} · ${record.title}`,
  }
}

function formatBytes(bytes) {
  const value = Number(bytes || 0)
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

async function confirmDelete() {
  const target = deleteTarget.value
  deleteTarget.value = null
  if (!target) return
  busy.value = true
  errorText.value = ''
  storageHint.value = ''
  try {
    const result = await api.delete(
      `/api/hygiene/admin/data/records/${target.kind}/${target.record_id}`,
    )
    storageHint.value = result.status_reset
      ? '已删除，这一项回到待拍，员工可以重拍。'
      : `已删除这条记录和它的 ${result.photos} 个照片文件。`
    await Promise.all([loadRecords(), loadStorage()])
  } catch (err) {
    errorText.value = err.message || '删除失败'
  } finally {
    busy.value = false
  }
}

async function confirmPurge() {
  purgeOpen.value = false
  busy.value = true
  errorText.value = ''
  storageHint.value = ''
  try {
    const result = await api.post('/api/hygiene/admin/data/purge', {
      kinds: purgeKinds.value,
      date_from: dateFrom.value || null,
      date_to: dateTo.value || null,
    })
    storageHint.value = `已清理 ${result.records} 条记录、${result.photos} 个照片文件（${result.date_from} ~ ${result.date_to}）。`
    await Promise.all([loadRecords(), loadStorage()])
  } catch (err) {
    errorText.value = err.message || '清理失败'
  } finally {
    busy.value = false
  }
}

async function startExport() {
  if (exportJob.value && exportJob.value.state === 'running') return
  exportError.value = ''
  exportHint.value = ''
  try {
    const job = await api.post(
      `/api/hygiene/admin/data/export/jobs?${new URLSearchParams(
        Object.entries(queryParams()).filter(([, value]) => value !== undefined),
      ).toString()}`,
    )
    exportJob.value = { ...job, done: 0, total: 0 }
    pollExport(job.job_id)
  } catch (err) {
    exportError.value = err.message || '无法开始导出'
  }
}

async function pollExport(jobId, filename = '卫生数据.zip') {
  try {
    const job = await api.get(`/api/hygiene/admin/data/export/jobs/${jobId}`)
    exportJob.value = job
    if (job.state === 'running') {
      pollTimer = setTimeout(() => pollExport(jobId), 800)
      return
    }
    if (job.state === 'failed') {
      exportError.value = job.error || '导出失败'
      return
    }
    await api.download(
      `/api/hygiene/admin/data/export/jobs/${jobId}/download`,
      job.filename || filename,
    )
    exportHint.value = `已打包 ${job.records || 0} 条记录、${job.count || 0} 张照片${
      job.skipped ? `（跳过 ${job.skipped} 张缺失文件）` : ''
    }。`
  } catch (err) {
    exportError.value = err.message || '导出失败'
  }
}

/** 一键保存**今天**的全部照片（日常、专项、整改、仪容仪表……）。
 *
 *  故意不看当前筛选：这个按钮就是「今天的都在这里」。也不让前端算「今天」——
 *  营业日 06:00 切日，只有服务端那个口径跟照片上的日期是同一个（前端算会在凌晨
 *  那一段差一天）。
 */
async function saveTodayPhotos() {
  if (exportJob.value && exportJob.value.state === 'running') return
  exportError.value = ''
  exportHint.value = ''
  exportToday.value = true
  try {
    const job = await api.post('/api/hygiene/admin/data/export/jobs?scope=today')
    exportJob.value = { ...job, done: 0, total: 0 }
    pollExport(job.job_id, '今天的卫生照片.zip')
  } catch (err) {
    exportError.value = err.message || '无法开始导出'
  } finally {
    exportToday.value = false
  }
}

/** 当前筛选的短摘要：`2026-10-13 ~ 10-19 · 全部工作区 · 7 类` */
const dateRangeText = computed(() => {
  if (allTime.value) return '近一年'
  const from = dateFrom.value || '最早'
  const to = dateTo.value ? dateTo.value.slice(5) : '今天'
  return `${from} ~ ${to}`
})
const zoneText = computed(() => {
  if (zoneId.value === '') return '全部工作区'
  const zone = zones.value.find((item) => String(item.id) === String(zoneId.value))
  return zone ? zone.name : '全部工作区'
})
const filterSummary = computed(() => `${dateRangeText.value} · ${zoneText.value} · ${selectedKinds.value.length} 类`)
const filtersDirty = computed(() => JSON.stringify(queryParams()) !== appliedSnapshot.value)

/** 7 类 chip：张数在签名里，占用放进 title（hover 可看）。 */
function kindBucket(kind) {
  return (storage.value && storage.value.kinds || []).find((item) => item.kind === kind) || null
}
function kindCount(kind) {
  const bucket = kindBucket(kind)
  return bucket ? bucket.count : 0
}
function kindBytes(kind) {
  const bucket = kindBucket(kind)
  return bucket ? formatBytes(bucket.bytes) : '—'
}
function isKindActive(kind) {
  return selectedKinds.value.length === 1 && selectedKinds.value[0] === kind
}

const recordsEl = ref(null)
/** 点类型 chip：设为**唯一**筛选并滚到列表；再点一次回到「全部」。 */
async function toggleKindFilter(kind) {
  if (isKindActive(kind)) {
    // 再点一次 = 回到「全部类型」：**时间范围保持用户当前的选择**（不偷偷改日期）。
    selectedKinds.value = KINDS.map((item) => item.value)
  } else {
    // 设为唯一类型 = 看这一类**近一年**：chip 上的张数是全量，按默认 7 天筛会看不到
    // 老记录（9 月的整改原图就是这么消失的）。
    selectedKinds.value = [kind]
    allTime.value = true
  }
  page.value = 1
  await loadRecords()
  filtersOpen.value = false
  const el = recordsEl.value
  if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/** 空态「看近一年」：把区间放到最近 366 天（类型/区保持不变）并重新查询。 */
async function showAllTime() {
  allTime.value = true
  page.value = 1
  filtersOpen.value = false
  await loadRecords()
}

/** 空态「清除筛选」：日期回最近 7 天、区回全部、类型回全选。 */
async function clearFilters() {
  const range = defaultRange()
  allTime.value = false
  dateFrom.value = range.from
  dateTo.value = range.to
  zoneId.value = ''
  selectedKinds.value = KINDS.map((kind) => kind.value)
  page.value = 1
  filtersOpen.value = false
  await loadRecords()
}

const pageStart = computed(() => (total.value ? (page.value - 1) * PAGE_SIZE + 1 : 0))
const pageEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))

/** 行尾「⋯」菜单：查看全部照片 / 删除这条。 */
const photoSheet = ref(null)
function openAllPhotos(record) {
  photoSheet.value = record
}

function gotoPage(next) {
  if (next < 1 || next > pageCount.value) return
  page.value = next
  return loadRecords()
}
</script>

<template>
  <div class="data-page">
    <header class="wbx-head">
      <div class="wbx-head-main">
        <p class="wbx-kicker">ARCHIVE · 数据与照片</p>
        <h1 class="wbx-title">数据与照片</h1>
      </div>
      <!-- 桌面：刷新 / 导出 ▾ / 清理…；手机：刷新 + ⋯ 菜单（三样收进去） -->
      <div class="wbx-pills head-actions-desktop">
        <button type="button" class="btn" :disabled="loading" @click="loadRecords">刷新</button>
        <details class="menu">
          <summary class="btn">导出 ▾</summary>
          <div class="menu-list">
            <button type="button" class="menu-item" :disabled="loading || !selectedKinds.length || !records.length || (exportJob && exportJob.state === 'running')" @click="startExport">导出台账</button>
            <button type="button" class="menu-item" :disabled="exportToday || (exportJob && exportJob.state === 'running')" @click="saveTodayPhotos">一键保存今天的照片</button>
          </div>
        </details>
        <button type="button" class="btn btn-danger" :disabled="loading || busy" @click="openPurge">清理…</button>
      </div>
      <div class="wbx-pills head-actions-mobile">
        <button type="button" class="btn" :disabled="loading" @click="loadRecords">刷新</button>
        <details class="menu">
          <summary class="menu-trigger" aria-label="更多操作">⋯</summary>
          <div class="menu-list">
            <button type="button" class="menu-item" :disabled="loading || !selectedKinds.length || !records.length || (exportJob && exportJob.state === 'running')" @click="startExport">导出台账</button>
            <button type="button" class="menu-item" :disabled="exportToday || (exportJob && exportJob.state === 'running')" @click="saveTodayPhotos">一键保存今天的照片</button>
            <button type="button" class="menu-item is-danger" :disabled="loading || busy" @click="openPurge">清理…</button>
          </div>
        </details>
      </div>
      <p class="wbx-sub">按营业日翻看日常实拍、专项前后、整改原图与回拍、教材和标准图版本；导出台账、清理旧照片都在页头。</p>
      <details class="rule-help">
        <summary>规则说明</summary>
        <p>营业日按 06:00 切，与员工端一致。删除是硬删除：记录和它的照片一起消失，红黑榜的历史计数不回滚。</p>
        <p>当前标准图、以及被任何一次提交当作对照用过的版本都不能删。</p>
      </details>
    </header>

    <p v-if="errorText" class="wbx-error roster-error" role="alert">{{ errorText }}</p>
    <p v-if="storageHint" class="wbx-hintline" role="status">{{ storageHint }}</p>
    <p v-if="exportJob && exportJob.state === 'running'" class="wbx-hintline" role="status">
      正在打包 {{ exportJob.done || 0 }} / {{ exportJob.total || '…' }} 张…
    </p>
    <p v-if="exportHint" class="wbx-hintline" role="status">{{ exportHint }}</p>
    <p v-if="exportError" class="roster-error wbx-error" role="alert">{{ exportError }}</p>

    <!-- ① 存储概况：一行大读数 + 7 类可点 chip -->
    <section v-if="storage" class="wbx-panel">
      <header class="wbx-ph">
        <i class="led sig"></i>
        <h2>存储概况</h2>
        <span class="wbx-hint">{{ storage.kinds.length }} 类</span>
      </header>
      <div class="storage-body">
        <p class="storage-total">
          <b>{{ storage.total.count }}</b> 张 · <b>{{ formatBytes(storage.total.bytes) }}</b>
        </p>
        <div class="kind-chips" role="group" aria-label="按类型筛选">
          <button
            v-for="kind in KINDS"
            :key="kind.value"
            type="button"
            class="kind-chip"
            :class="{ 'is-on': isKindActive(kind.value) }"
            :aria-pressed="isKindActive(kind.value) ? 'true' : 'false'"
            :title="`${kind.label} · ${kindCount(kind.value)} 张 · ${kindBytes(kind.value)}`"
            @click="toggleKindFilter(kind.value)"
          >{{ kind.label }} <b>{{ kindCount(kind.value) }}</b></button>
        </div>
        <p v-if="storage.missing_files || storage.orphan_files.count" class="wbx-hintline">
          <template v-if="storage.missing_files">有 {{ storage.missing_files }} 张照片在库里但没有文件，取图会显示占位。</template>
          <template v-if="storage.missing_files && storage.orphan_files.count"> · </template>
          <template v-if="storage.orphan_files.count">另有 {{ storage.orphan_files.count }} 个无主文件（{{ formatBytes(storage.orphan_files.bytes) }}），后台会自动清掉。</template>
        </p>
      </div>
    </section>

    <!-- ② 记录：筛选条默认收起，折叠态一行摘要 + 查询；点摘要才展开 -->
    <section ref="recordsEl" class="wbx-panel">
      <header class="wbx-ph">
        <i class="led ok"></i>
        <h2>记录</h2>
        <span class="wbx-hint">{{ total }} 条</span>
        <span v-if="rangeLabel" class="wbx-hint data-range">{{ allTime ? '近一年' : rangeLabel }}</span>
      </header>

      <div class="filter-bar">
        <button
          type="button"
          class="filter-summary"
          :aria-expanded="filtersOpen ? 'true' : 'false'"
          @click="filtersOpen = !filtersOpen"
        >
          <span>{{ filterSummary }}</span>
          <span class="filter-caret" aria-hidden="true">{{ filtersOpen ? '▴' : '▾' }}</span>
        </button>
        <button
          type="button"
          class="btn"
          :class="{ 'btn-primary': filtersDirty }"
          :disabled="loading"
          @click="applyFilters"
        >查询</button>
      </div>

      <div v-if="filtersOpen" class="filter-panel">
        <label class="wbx-field">
          <span>从</span>
          <LuyunDatePicker v-model="dateFrom" dark :disabled="allTime" placeholder="开始日期" aria-label="数据查询开始日期" />
        </label>
        <label class="wbx-field">
          <span>到</span>
          <LuyunDatePicker v-model="dateTo" dark :disabled="allTime" placeholder="结束日期" aria-label="数据查询结束日期" />
        </label>
        <label class="all-time-check">
          <input v-model="allTime" type="checkbox">
          <span>近一年</span>
        </label>
        <label class="wbx-field">
          <span>工作区</span>
          <select v-model="zoneId" class="input" aria-label="按工作区筛选">
            <option value="">全部</option>
            <option v-for="zone in zones" :key="zone.id" :value="zone.id">{{ zone.name }}</option>
          </select>
        </label>
        <details class="kind-fold">
          <summary>类型 · {{ selectedKinds.length }} / {{ KINDS.length }}</summary>
          <fieldset class="kind-field">
            <legend>类型</legend>
            <label v-for="kind in KINDS" :key="kind.value" class="kind-option">
              <input v-model="selectedKinds" type="checkbox" :value="kind.value">
              <span>{{ kind.label }}</span>
            </label>
          </fieldset>
        </details>
        <div class="filter-actions">
          <button type="button" class="btn btn-primary" :disabled="loading" @click="applyFilters">查询</button>
          <button type="button" class="btn" :disabled="loading" @click="clearFilters">重置</button>
        </div>
      </div>

      <div v-if="loading" class="wbx-empty-bar">正在加载…</div>
      <div v-else-if="loadError" class="wbx-empty-bar">
        记录没读出来 · {{ loadError }}
        <button type="button" class="btn" @click="loadRecords">重试</button>
      </div>
      <!-- 0 条分两种：区间内没有（芯片数字是全量的，可能在这段之外）vs 真的没有记录 -->
      <div v-else-if="!records.length" class="wbx-empty-bar is-empty">
        <template v-if="!allTime">
          <span>这个日期范围里没有记录。</span>
          <span class="empty-hint">上方类型 chip 上的数字是近一年的；这类记录可能在这个区间之外。</span>
          <button type="button" class="btn btn-primary" @click="showAllTime">看近一年</button>
          <button type="button" class="btn" @click="clearFilters">清除筛选</button>
        </template>
        <template v-else>
          <span>一次记录都没有：员工还没交过照片，或者已经被清理。</span>
          <button type="button" class="btn" @click="clearFilters">清除筛选</button>
          <button type="button" class="btn" @click="loadRecords">刷新</button>
        </template>
      </div>
      <template v-else>
        <p v-if="truncated" class="wbx-hintline">这一段记录太多，只显示了每个类型最近的部分，请收窄日期区间。</p>
        <ul class="data-list">
          <li v-for="record in records" :key="`${record.kind}-${record.record_id}`" class="data-row">
            <div class="data-photos">
              <button
                v-for="photo in record.photos.slice(0, 3)"
                :key="photo.capture_id"
                type="button"
                class="data-thumb"
                :aria-label="`查看 ${record.title} 的${photo.role}`"
                @click="openPhoto(record, photo)"
              >
                <img
                  v-if="!photoMissing(photo.capture_id)"
                  :src="photoUrl(photo.capture_id)"
                  :alt="`${record.title} ${photo.role}`"
                  loading="lazy"
                  @error="markPhotoMissing(photo.capture_id)"
                >
                <span v-else class="data-thumb-missing" :title="`${photo.role}的文件不在了（库里还有记录）`">无文件</span>
              </button>
              <button
                v-if="record.photos.length > 3"
                type="button"
                class="data-thumb-more"
                :aria-label="`查看 ${record.title} 的全部 ${record.photos.length} 张照片`"
                @click="openAllPhotos(record)"
              >+{{ record.photos.length - 3 }}</button>
              <span v-if="!record.photos.length" class="data-nophoto">没有照片</span>
            </div>
            <div class="data-body">
              <strong>{{ record.title || '（无说明）' }}</strong>
              <span class="data-meta">
                {{ record.kind_label }}
                <template v-if="record.zone_name"> · {{ record.zone_name }}</template>
                <template v-if="record.business_date"> · 营业日 {{ record.business_date }}</template>
                <template v-if="record.subtitle"> · {{ record.subtitle }}</template>
                · {{ record.submitter_name || record.submitter_phone || '—' }}
                · {{ record.status }}
              </span>
              <span class="data-meta">{{ formatHygieneStamp(record.occurred_at) }}</span>
            </div>
            <details class="row-menu">
              <summary class="row-menu-trigger" :aria-label="`${record.title || record.kind_label} 的更多操作`">⋯</summary>
              <div class="row-menu-list">
                <button type="button" class="menu-item" @click="openAllPhotos(record)">查看全部照片</button>
                <button type="button" class="menu-item is-danger" :disabled="busy" @click="deleteTarget = record">删除这条</button>
              </div>
            </details>
          </li>
        </ul>
        <div class="data-pager">
          <span class="pager-text">第 {{ pageStart }}–{{ pageEnd }} 条 / 共 {{ total }} 条</span>
          <button type="button" class="btn" :disabled="page <= 1 || loading" @click="gotoPage(page - 1)">上一页</button>
          <button type="button" class="btn" :disabled="page >= pageCount || loading" @click="gotoPage(page + 1)">下一页</button>
        </div>
      </template>
    </section>

    <!-- 行尾「+N」/菜单打开的「全部照片」浮层：先看全，再点开单张进灯箱 -->
    <div
      v-if="photoSheet"
      class="modal-overlay photo-sheet"
      role="dialog"
      aria-modal="true"
      aria-label="全部照片"
      @click.self="photoSheet = null"
    >
      <div class="photo-sheet-box">
        <div class="photo-sheet-head">
          <h3>{{ photoSheet.title || photoSheet.kind_label }}</h3>
          <button type="button" class="btn" aria-label="关闭" @click="photoSheet = null">关闭</button>
        </div>
        <div class="photo-sheet-grid">
          <button
            v-for="photo in photoSheet.photos"
            :key="photo.capture_id"
            type="button"
            class="photo-sheet-thumb"
            :aria-label="`查看 ${photoSheet.title} 的${photo.role}`"
            @click="openPhoto(photoSheet, photo)"
          >
            <img
              v-if="!photoMissing(photo.capture_id)"
              :src="photoUrl(photo.capture_id)"
              :alt="`${photoSheet.title} ${photo.role}`"
              @error="markPhotoMissing(photo.capture_id)"
            >
            <span v-else class="data-thumb-missing">无文件</span>
            <span>{{ photo.role }}</span>
          </button>
        </div>
      </div>
    </div>

    <ConfirmDialog
      v-if="deleteTarget"
      title="删除这条记录"
      :message="`删除「${deleteTarget.title || deleteTarget.kind_label}」（${deleteTarget.business_date}）以及它的照片。删了不能恢复；如果它还在等验收，这一项会回到待拍。`"
      confirm-label="删除"
      danger
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />

    <!-- 清理是硬删除，不再常驻页面：从页头「清理…」打开，必须输入「清理」两个字才可确认 -->
    <ConfirmDialog
      v-if="purgeOpen"
      title="按区间清理"
      :message="`将删除 ${dateFrom || '最早'} ~ ${dateTo || '今天'} · ${zoneText} · ${purgeKinds.length} 类 · 共 ${total} 条记录（照片一起删）。这是硬删除，不能恢复；红黑榜的历史计数不回滚。`"
      confirm-label="确认清理"
      danger
      :prompt="{ label: '输入「清理」两个字确认', placeholder: '清理', hint: '硬删除 · 不可恢复', required: true, equals: '清理', rows: 1, maxlength: 10 }"
      @confirm="confirmPurge"
      @cancel="purgeOpen = false"
    />

    <HygieneImageLightbox
      v-if="lightbox"
      :src="lightbox.src"
      :alt="lightbox.alt"
      @close="lightbox = null"
    />
  </div>
</template>

<style scoped>
/* B 案 v2/v3 + 本轮（2026-10-19）：三块 = 存储概况（读数 + 可点 chip）/ 记录（筛选默认收起、
   行内「⋯」菜单、全部照片浮层）/ 页头动作（导出菜单 + 清理弹窗）。危险动作不再常驻。 */
.data-page {
  display: flex; flex-direction: column; gap: 14px;
  padding-bottom: calc(40px + var(--safe-b));
}
.wbx-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 16px; align-items: end; }
.wbx-head-main { grid-column: 1; grid-row: 1; }
.wbx-kicker { margin: 0 0 7px; font: 500 9.5px/1 var(--font-sans); letter-spacing: .26em; color: var(--ink-faint); }
.wbx-title { margin: 0; font: 600 26px/1.1 var(--font-sans); letter-spacing: .01em; color: var(--ink); }
.wbx-pills { grid-column: 2; grid-row: 1; justify-self: end; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wbx-sub { grid-column: 1 / -1; grid-row: 2; margin: 8px 0 0; font-size: 11.5px; line-height: 1.6; color: var(--ink-dim); }
.wbx-head .rule-help { grid-column: 1 / -1; grid-row: 3; margin-top: 6px; }
.wbx-error { margin: 0; font-size: 11px; color: var(--danger-bright); }
.wbx-hintline { margin: 0; font-size: 10.5px; color: var(--ink-dim); }
.led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; background: var(--line-strong); }
.led.ok { background: var(--ok); }
.led.bad { background: var(--danger); }
.led.sig { background: var(--action); }

/* 页头动作：桌面三件；手机只留刷新 + ⋯（三样收进菜单） */
.head-actions-mobile { display: none; }
.menu { position: relative; }
.menu > summary { list-style: none; }
.menu > summary::-webkit-details-marker { display: none; }
.menu-trigger {
  display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px;
  border: 1px solid var(--line-strong); border-radius: var(--r-1);
  background: var(--surface-1); color: var(--ink);
  font: 600 18px/1 var(--font-sans); cursor: pointer;
}
.menu-list {
  position: absolute; right: 0; top: calc(100% + 6px); z-index: var(--z-dialog);
  min-width: 200px; padding: 6px;
  background: var(--surface-1); border: 1px solid var(--line-strong);
  border-radius: var(--r-2); box-shadow: 0 6px 18px var(--wb-ink-a14);
  display: flex; flex-direction: column; gap: 2px;
}
.menu-item {
  display: flex; align-items: center; min-height: 44px; padding: 0 10px;
  border: 0; border-radius: var(--r-1); background: transparent;
  color: var(--ink); font: 500 11.5px/1.3 var(--font-sans); text-align: left; cursor: pointer;
}
.menu-item:hover { background: var(--surface-2); }
.menu-item:disabled { opacity: .45; cursor: not-allowed; }
.menu-item.is-danger { color: var(--danger-bright); }

.wbx-panel {
  display: flex; flex-direction: column;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08); overflow: hidden;
}
.wbx-ph { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.wbx-ph h2 { margin: 0; font: 600 12.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.wbx-hint { margin-left: auto; font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.wbx-empty-bar {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  min-height: 44px; margin: 0; padding: 10px 14px;
  font-size: 10.5px; color: var(--ink-faint);
}
.wbx-empty-bar.is-empty { gap: 10px; padding-bottom: 12px; }
.empty-hint { flex: 1 1 100%; font-size: 9.5px; color: var(--ink-faint); }
.all-time-check {
  display: inline-flex; align-items: center; gap: 6px;
  min-height: 44px; font-size: 10.5px; color: var(--ink-dim); cursor: pointer;
}
.all-time-check input { accent-color: var(--action); }

/* 存储概况：大读数 + 可点类型 chip */
.storage-body { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px 14px; }
.storage-total { margin: 0; display: flex; align-items: baseline; gap: 6px; font-size: 11px; color: var(--ink-faint); }
.storage-total b { font: 500 24px/1 var(--font-mono); color: var(--ink); }
.storage-total b + b { font-size: 18px; }
.kind-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.kind-chip {
  display: inline-flex; align-items: center; gap: 6px;
  min-height: 44px; padding: 0 10px;
  border: 1px solid var(--line-strong); border-radius: var(--r-full);
  background: var(--surface-2); color: var(--ink-dim);
  font: 500 10.5px/1 var(--font-sans); cursor: pointer;
}
.kind-chip b { font: 500 10px/1 var(--font-mono); color: var(--ink); }
.kind-chip:hover { border-color: var(--action); color: var(--action); }
.kind-chip.is-on { background: var(--action-soft); border-color: var(--action); color: var(--action); }
.kind-chip.is-on b { color: var(--action); }

/* 筛选条：折叠态一行摘要 + 查询；展开才是表单 */
.filter-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 14px; border-bottom: 1px solid var(--wb-ink-a08);
}
.filter-summary {
  flex: 1 1 auto; min-width: 0;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  min-height: 44px; padding: 0 12px;
  border: 1px solid var(--line); border-radius: var(--r-1);
  background: var(--surface-2); color: var(--ink-dim);
  font: 500 10.5px/1 var(--font-mono); text-align: left; cursor: pointer;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.filter-summary > span:first-child { overflow: hidden; text-overflow: ellipsis; }
.filter-caret { flex: 0 0 auto; color: var(--ink-faint); }
.filter-panel {
  display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px 12px;
  padding: 10px 14px; border-bottom: 1px solid var(--wb-ink-a08);
}
.filter-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.wbx-field { display: flex; flex-direction: column; gap: 5px; font-size: 10px; color: var(--ink-dim); }
.wbx-field :deep(.luyun-date-picker) { width: 150px; }
.kind-fold { font-size: 10px; color: var(--ink-dim); }
.kind-fold summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; }
.kind-field {
  border: 1px solid var(--line); border-radius: var(--r-1);
  padding: 6px 10px 8px; margin: 0; display: flex; flex-wrap: wrap; gap: 8px;
}
.kind-field legend { padding: 0 4px; font: 500 9.5px/1 var(--font-sans); letter-spacing: .12em; color: var(--ink-faint); }
.kind-option { display: flex; align-items: center; gap: 4px; min-height: 44px; font-size: 10.5px; color: var(--ink-dim); }
.kind-option input { accent-color: var(--action); }

/* 记录行：缩略图 ≤3 + +N，正文两行，行尾「⋯」 */
.data-list { list-style: none; margin: 0; padding: 0; }
.data-row {
  display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: center;
  min-height: 72px; padding: 8px 14px; border-bottom: 1px solid var(--wb-ink-a08);
}
.data-row:last-child { border-bottom: 0; }
.data-row:hover { background: var(--surface-2); }
.data-photos { display: flex; gap: 4px; align-items: center; }
.data-thumb, .data-thumb-more {
  position: relative; display: block; padding: 0;
  border: 1px solid var(--line); border-radius: var(--r-1);
  background: none; cursor: pointer; overflow: hidden;
}
.data-thumb img { width: 56px; height: 42px; object-fit: cover; display: block; }
/* 文件不在了：占位块而不是破图（存储概况那条提示说的就是这种）。 */
.data-thumb-missing {
  display: grid; place-items: center;
  width: 56px; height: 42px;
  font-size: 9px; letter-spacing: .06em; color: var(--ink-faint);
  background: repeating-linear-gradient(45deg, var(--surface-2) 0 6px, var(--line) 6px 12px);
  border-radius: 2px;
}
.data-thumb-more {
  display: inline-flex; align-items: center; justify-content: center;
  width: 42px; height: 42px;
  font: 500 10px/1 var(--font-mono); color: var(--ink-dim);
}
.data-thumb-more:hover { border-color: var(--action); color: var(--action); }
.data-nophoto { font-size: 9.5px; color: var(--ink-faint); }
.data-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.data-body strong { font-size: 11.5px; font-weight: 600; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.data-meta { font: 500 9.5px/1.45 var(--font-mono); color: var(--ink-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.data-range { margin-left: 8px; }
.row-menu { position: relative; }
.row-menu-trigger {
  display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; list-style: none;
  border: 1px solid var(--line); border-radius: var(--r-1);
  background: var(--surface-1); color: var(--ink-dim);
  font: 600 16px/1 var(--font-sans); cursor: pointer;
}
.row-menu-trigger::-webkit-details-marker { display: none; }
.row-menu[open] .row-menu-trigger { border-color: var(--action); color: var(--action); }
.row-menu-list {
  position: absolute; right: 0; top: calc(100% + 6px); z-index: var(--z-dialog);
  min-width: 170px; padding: 6px;
  background: var(--surface-1); border: 1px solid var(--line-strong);
  border-radius: var(--r-2); box-shadow: 0 6px 18px var(--wb-ink-a14);
  display: flex; flex-direction: column; gap: 2px;
}
.data-pager {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 10px;
  padding: 10px 14px; border-top: 1px solid var(--wb-ink-a08);
  font: 500 10px/1 var(--font-mono); color: var(--ink-dim);
}
.pager-text { margin-right: auto; }

/* 全部照片浮层：缩略图网格（点一张进灯箱） */
.photo-sheet { align-items: center; justify-content: center; padding: 16px; }
.photo-sheet-box {
  width: min(720px, 100%); max-height: 80vh; overflow: auto;
  background: var(--surface-1); border: 1px solid var(--line-strong);
  border-radius: var(--r-2); padding: 14px;
}
.photo-sheet-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.photo-sheet-head h3 { margin: 0; font: 600 13px/1.3 var(--font-sans); color: var(--ink); }
.photo-sheet-head .btn { margin-left: auto; }
.photo-sheet-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.photo-sheet-thumb {
  display: flex; flex-direction: column; gap: 4px; padding: 0;
  border: 1px solid var(--line); border-radius: var(--r-1);
  background: var(--surface-2); cursor: pointer; overflow: hidden;
}
.photo-sheet-thumb img { width: 100%; aspect-ratio: 4 / 3; object-fit: cover; display: block; }
.photo-sheet-thumb span { font: 500 9px/1 var(--font-mono); color: var(--ink-faint); padding: 2px 0 4px; }

.data-page :deep(.btn) { min-height: 44px !important; border-radius: var(--r-1); }
@media (max-width: 900px) {
  .data-page .data-row { grid-template-columns: auto minmax(0, 1fr) auto; }
}
@media (max-width: 720px) {
  .wbx-head { grid-template-columns: minmax(0, 1fr); }
  .wbx-pills { grid-column: 1; grid-row: 2; justify-self: start; margin-top: 10px; }
  .head-actions-desktop { display: none; }
  .head-actions-mobile { display: flex; }
  /* 手机：⋯ 靠页头左侧，菜单向左对齐触发器、向右展开（右对齐会溢出屏幕） */
  .head-actions-mobile .menu-list { left: 0; right: auto; max-width: calc(100vw - 32px); }
  .wbx-sub { grid-row: 3; }
  .wbx-head .rule-help { grid-row: 4; }
  .wbx-title { font-size: 22px; }
  .data-thumb img { width: 56px; height: 56px; }
  .data-thumb-more { width: 56px; height: 56px; }
  .filter-summary { font-size: 10px; }
  /* 手机：缩略图占一整行、正文占满下一行，⋯ 留在右上（否则正文被挤成「日…」） */
  .data-row {
    grid-template-columns: minmax(0, 1fr) auto;
    row-gap: 6px;
    padding: 8px 12px;
  }
  .data-photos { grid-column: 1 / -1; }
  .data-body { grid-column: 1; }
  .row-menu { grid-column: 2; grid-row: 1; align-self: start; }
  .data-pager { justify-content: center; }
  .pager-text { width: 100%; text-align: center; margin: 0 0 4px; }
}
</style>
