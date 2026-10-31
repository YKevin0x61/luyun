<script setup>
/**
 * 备货品换算规则（2026-10-26 从销售报表的「换算规则」tab 搬进后勤组）。
 *
 * 这个页面回答「一道菜用多少备货品」，而它是备货建议量的唯一数据来源
 * （`prep_items` 决定清单里有谁，`semi_finished_rules.factor` 决定卖出去的菜折成多少料）。
 *
 * 左列是「逻辑菜」：同一个菜的堂食 / 外卖 / 规格变体折成一行（库里 143 条规则里有 52 条
 * 是这种变体，按行平铺重复得没法改）。右列是当前这一组的编辑面板。
 *
 * 数据来自 `GET /api/semi-rules/board`（按组返回 + 覆盖率），写走原来的
 * `/api/semi-rules/{id}` 与 `/api/semi-rules/`。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../../api/client'
import { useStationsStore } from '../../stores/stations'
import { useNudgePull } from '../../composables/useNudgePull'

const props = defineProps({
  // 销售报表里嵌的是只读版：同一套列表与详情，但不给改（spec §2 决策 3）
  readonly: { type: Boolean, default: false },
})

const stationsStore = useStationsStore()

const loading = ref(true)
const errorText = ref('')
const coverage = ref(null)
const groups = ref([])
const prepItems = ref([]) // 备货品主数据（岗位/单位/分类都挂在它上面）
const positionOptions = ref([]) // 岗位清单（后端唯一一份，含它映射到哪个档口）
const prepQuery = ref('')   // 备货品手输的关键字（打字即筛，不用在 72 项里翻）
const newItem = reactive({ open: false, name: '', unit: '', position: '', kind: 'made', saving: false, err: '' })
const comboOpen = ref(false)

const tab = ref('covered') // covered | pending
const search = ref('')
const positionFilter = ref('')
const stationFilter = ref('')

const selectedKey = ref('')
const mobileDetail = ref(false)
const draft = ref(null)
const syncVariants = ref(true)
const showAllDormant = ref(false) // 待配里是否带 90 天没卖的菜
const manualMode = ref(false)     // 手输一个 POS 里还没有的菜名
const saving = ref(false)
const saveMsg = ref('')

const coveredGroups = computed(() => groups.value.filter((g) => g.has_rules))
const pendingGroups = computed(() => groups.value.filter((g) => !g.has_rules))

const coverPct = computed(() => {
  const c = coverage.value
  if (!c || !c.active_groups) return 0
  return Math.round((c.covered_groups / c.active_groups) * 100)
})

// 护栏不允许内联 style：宽度落到 10% 档位的 class 上
const coverClass = computed(() => `w${Math.min(100, Math.round(coverPct.value / 10) * 10)}`)

// 岗位切换只对「已配」有意义：待配的菜还没有规则，也就没有岗位。
const positions = computed(() => {
  const counter = new Map()
  for (const g of coveredGroups.value) {
    const pos = g.variants?.[0]?.position || ''
    if (!pos) continue
    counter.set(pos, (counter.get(pos) || 0) + 1)
  }
  return [...counter.entries()]
    .map(([key, count]) => ({ key, count }))
    .sort((a, b) => b.count - a.count)
})

// 待配的菜还没有规则、所以没有岗位；但它们**有档口映射**（dish_stations），
// 所以待配视图按档口筛 —— 「先把熟笼档该配的配了」才是能落地的用法。
const stations = computed(() => {
  const counter = new Map()
  for (const g of pendingGroups.value) {
    const st = g.station || '未知'
    counter.set(st, (counter.get(st) || 0) + 1)
  }
  return [...counter.entries()]
    .map(([key, count]) => ({ key, count }))
    .sort((a, b) => b.count - a.count)
})

const visibleGroups = computed(() => {
  const kw = search.value.trim().toLowerCase()
  const base = tab.value === 'covered' ? coveredGroups.value : pendingGroups.value
  return base.filter((g) => {
    if (positionFilter.value && tab.value === 'covered') {
      if ((g.variants?.[0]?.position || '') !== positionFilter.value) return false
    }
    if (stationFilter.value && tab.value === 'pending') {
      if ((g.station || '未知') !== stationFilter.value) return false
    }
    if (!kw) return true
    const hay = [g.main_name, ...(g.variants || []).map((v) => v.dish_name + ' ' + (v.prep_item_name || ''))]
      .join(' ')
      .toLowerCase()
    return hay.includes(kw)
  })
})

// 切 tab 后，上一条选中可能落在另一边（正在看的已配 vs 切过来的待配）。
// 这里让选中项永远跟着当前列表走：列表空就清空，否则选第一条。
watch([tab, search, positionFilter, stationFilter], () => {
  const first = visibleGroups.value[0]
  if (!first) {
    selectedKey.value = ''
    draft.value = null
    return
  }
  if (first.key !== selectedKey.value) pick(first, false)
})

const selectedGroup = computed(() => visibleGroups.value.find((g) => g.key === selectedKey.value) || null)
const selectedVariants = computed(() => selectedGroup.value?.variants || [])
const activeVariant = computed(() => selectedVariants.value[draft.value?.variantIndex ?? 0] || null)

// 一组里用了几个不同备货品 —— 「多料」（拼盘/套餐）跟「多版本」（外卖渠道）是两件事
function materialCount(group) {
  return new Set((group.variants || []).map((v) => v.prep_item_id)).size
}

function startAddMaterial() {
  prepQuery.value = ''
  draft.value = {
    mode: 'create',
    variantIndex: 0,
    factor: 1,
    unit: '',
    position: '',
    category: '',
    prep_item_id: null,
    prep_item_name: '',
    prep_item_position: '',
    semi_name: '',
  }
  saveMsg.value = ''
}

function startManualCreate() {
  if (props.readonly) return
  manualMode.value = true
  selectedKey.value = ''
  prepQuery.value = ''
  saveMsg.value = ''
  draft.value = {
    mode: 'create',
    variantIndex: 0,
    factor: 1,
    unit: '',
    position: '',
    category: '',
    prep_item_id: null,
    prep_item_name: '',
    prep_item_position: '',
    semi_name: '',
    manualDishName: '',
  }
  mobileDetail.value = true
}

// 手输时没有对应的「组」：造一个空壳给详情面板用，菜名在表单里填
const detailGroup = computed(() => {
  if (manualMode.value) {
    return { key: '__manual__', main_name: '', station: '未知', has_rules: false, qty: 0, variants: [] }
  }
  return selectedGroup.value
})

function stationName(id) {
  return stationsStore.nameOf ? stationsStore.nameOf(id) : id
}

function pick(group, openMobile = true) {
  manualMode.value = false
  selectedKey.value = group.key
  mobileDetail.value = openMobile
  comboOpen.value = false
  saveMsg.value = ''
  if (group.has_rules) {
    const first = group.variants[0]
    prepQuery.value = first.prep_item_name || first.semi_name || ''
    draft.value = {
      mode: 'edit',
      variantIndex: 0,
      factor: first.factor,
      unit: first.unit,
      position: first.position,
      category: first.category,
      prep_item_id: first.prep_item_id,
      prep_item_name: first.prep_item_name || first.semi_name,
      prep_item_position: first.prep_item_position || '',
      semi_name: first.semi_name,
    }
  } else {
    draft.value = {
      mode: 'create',
      variantIndex: 0,
      factor: 1,
      unit: '',
      position: '',
      category: '',
      prep_item_id: null,
      prep_item_name: '',
      prep_item_position: '',
      semi_name: '',
    }
  }
}

function switchVariant(index) {
  const v = selectedVariants.value[index]
  if (!v) return
  prepQuery.value = v.prep_item_name || v.semi_name || ''
  draft.value = {
    mode: 'edit',
    variantIndex: index,
    factor: v.factor,
    unit: v.unit,
    position: v.position,
    category: v.category,
    prep_item_id: v.prep_item_id,
    prep_item_name: v.prep_item_name || v.semi_name,
    prep_item_position: v.prep_item_position || '',
    semi_name: v.semi_name,
  }
  saveMsg.value = ''
}

async function loadPositions() {
  try {
    const data = await api.get('/api/semi-rules/positions')
    if (data.success) positionOptions.value = data.positions || []
  } catch (e) {
    // 拉不到就退化成空下拉，不阻塞看规则
  }
}

async function loadPrepItems() {
  try {
    const data = await api.get('/api/semi-rules/prep-items')
    if (data.success) prepItems.value = data.items || []
  } catch (e) {
    // 选品列表拉不到不影响看规则，新增时再报错
  }
}

function openCreateItem() {
  if (props.readonly) return
  newItem.open = true
  newItem.name = prepQuery.value.trim()
  newItem.unit = ''
  newItem.position = ''
  newItem.kind = 'made'
  newItem.err = ''
  comboOpen.value = false
}

async function submitNewItem() {
  if (newItem.saving) return
  if (!newItem.name.trim()) {
    newItem.err = '名称不能为空'
    return
  }
  newItem.saving = true
  newItem.err = ''
  try {
    const res = await api.post('/api/semi-rules/prep-items', {
      item_name: newItem.name.trim(),
      unit: newItem.unit.trim(),
      position: newItem.position,
      kind: newItem.kind,
    })
    await loadPrepItems()
    const it = (res.item && prepItems.value.find((x) => x.id === res.item.id)) || null
    if (it) chooseItem(it)
    newItem.open = false
  } catch (e) {
    newItem.err = e.message || '新建失败'
  } finally {
    newItem.saving = false
  }
}

// 手写即搜：输入框打名字筛列表，精确命中就直接带出单位/岗位/分类
const filteredPrepItems = computed(() => {
  const kw = prepQuery.value.trim().toLowerCase()
  const pool = prepItems.value
  if (!kw) return pool.slice(0, 10)
  return pool.filter((it) => it.item_name.toLowerCase().includes(kw)).slice(0, 10)
})

// 输入的名字跟清单里某个品**完全一样**时才不用新建（模糊匹配到别的品不算）
const exactNameMatch = computed(() => {
  const q = prepQuery.value.trim()
  return !!q && prepItems.value.some((it) => it.item_name === q)
})

function chooseItem(it) {
  draft.value.prep_item_id = it.id
  draft.value.prep_item_name = it.item_name
  draft.value.semi_name = it.item_name
  draft.value.unit = it.unit || ''
  draft.value.prep_item_position = it.position || ''
  draft.value.position = it.position || ''
  draft.value.category = it.category || ''
  prepQuery.value = it.item_name
  comboOpen.value = false
}

// 岗位 / 单位 / 分类是**备货品**的属性：选完备货品直接带出来，不让手填第二份
function onPrepItemChange() {
  const it = prepItems.value.find((x) => x.id === draft.value?.prep_item_id)
  if (!it) return
  draft.value.semi_name = it.item_name
  draft.value.unit = it.unit || ''
  draft.value.prep_item_position = it.position || ''
  draft.value.position = it.position || ''
  draft.value.category = it.category || ''
}

async function load() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get(`/api/semi-rules/board?include_dormant=${showAllDormant.value ? 1 : 0}`)
    if (data.success) {
      groups.value = data.groups || []
      coverage.value = data.coverage || null
      // 选中的组可能因为保存后从「待配」挪到「已配」而换了 tab，保持选中
      if (!selectedKey.value && groups.value.length) {
        pick(groups.value[0])
        // 手机上详情是盖在列表上的一层：首屏先给列表，点了才进详情
        mobileDetail.value = false
      }
    }
  } catch (e) {
    errorText.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function save() {
  if (props.readonly) return
  const d = draft.value
  if (!d || saving.value) return
  saving.value = true
  saveMsg.value = ''
  try {
    if (d.mode === 'create') {
      const dishName = (d.manualDishName || '').trim() || selectedGroup.value?.main_name || ''
      if (!dishName) throw new Error('先填菜名')
      if (!d.prep_item_id) throw new Error('先打名字从上拉里选一个备货品；清单里没有的，先去「数据管理 → 备货品」新建')
      await api.post('/api/semi-rules/', {
        dish_name: dishName,
        semi_name: d.prep_item_name.trim(),
        position: d.position || '',
        factor: Number(d.factor) || 1,
        unit: d.unit,
        category: d.category,
        prep_item_id: d.prep_item_id,
      })
      manualMode.value = false
      saveMsg.value = '已建规则'
    } else {
      const targets = syncVariants.value ? selectedVariants.value : [activeVariant.value]
      for (const v of targets) {
        const payload = {
          factor: Number(d.factor) || 1,
          unit: d.unit,
          // 岗位以备货品主数据为准；没对上主数据时回落到规则自己的值
          position: d.position || '',
          category: d.category,
        }
        // 这条规则原本没对上备货品（0036 回填没跑 / 名字漂移）：保存时把它接上
        if (d.prep_item_id) {
          payload.prep_item_id = d.prep_item_id
          if (d.prep_item_name) payload.semi_name = d.prep_item_name
        }
        await api.put(`/api/semi-rules/${v.id}`, payload)
      }
      saveMsg.value = syncVariants.value && targets.length > 1 ? `已同步 ${targets.length} 条变体` : '已保存'
    }
    await load()
  } catch (e) {
    saveMsg.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function removeCurrent() {
  if (props.readonly) return
  const v = activeVariant.value
  if (!v) return
  const who = v.prep_item_name || v.semi_name
  const ok = window.confirm(
    `删除「${v.dish_name}」用「${who}」的这条换算？\n删掉后，这道菜（这个渠道）就不会再折成备货量，明天的建议量会跟着变。`
  )
  if (!ok) return
  try {
    await api.delete(`/api/semi-rules/${v.id}`)
    selectedKey.value = ''
    manualMode.value = false
    saveMsg.value = '已删除'
    await load()
  } catch (e) {
    saveMsg.value = e.message || '删除失败'
  }
}

useNudgePull({
  id: 'prep-rules-board',
  topics: ['admin'],
  pull: load,
  debounceMs: 500,
  immediate: false,
})

watch(showAllDormant, () => { load() })

onMounted(async () => {
  await stationsStore.load()
  await Promise.all([load(), loadPrepItems(), loadPositions()])
})
</script>

<template>
  <div class="pr">
    <header class="pr-head">
      <div class="pr-title">
        <h1>备货品换算规则</h1>
        <p>一道菜消耗多少备货品。系数错了，今天的备货建议量就跟着错。</p>
      </div>
      <div v-if="coverage" class="pr-cover">
        <div class="pr-bar"><i :class="coverClass"></i></div>
        <div class="pr-cov-text">
          已配 <b>{{ coverage.covered_groups }} / {{ coverage.active_groups }}</b> 组 ·
          销量覆盖 <b>{{ coverage.qty_covered_pct }}%</b> ·
          近 90 天待配 <b class="warn">{{ coverage.pending_groups }}</b>
        </div>
      </div>
    </header>

    <p v-if="errorText" class="pr-error" role="status">{{ errorText }}</p>
    <p v-if="coverage && coverage.schema_ready === false" class="pr-migrate" role="status">
      数据库还没应用迁移 0035（prep_item_id）：规则现在只按名字匹配，先去「系统更新 → 数据库迁移」跑一下。
    </p>

    <div class="pr-split">
      <aside class="pr-panel">
        <div class="pr-tools">
          <input v-model="search" class="pr-input" placeholder="搜菜品 / 备货品…" aria-label="搜索菜品或备货品">
          <div class="pr-seg">
            <button type="button" :class="{ on: tab === 'covered' }" @click="tab = 'covered'; positionFilter = ''">
              已配 <b>{{ coveredGroups.length }}</b>
            </button>
            <button type="button" :class="{ on: tab === 'pending' }" @click="tab = 'pending'; stationFilter = ''">
              待配 <b>{{ pendingGroups.length }}</b>
            </button>
          </div>
          <div v-if="tab === 'covered'" class="pr-posts">
            <button type="button" :class="{ on: !positionFilter }" @click="positionFilter = ''">全部</button>
            <button
              v-for="p in positions"
              :key="p.key"
              type="button"
              :class="{ on: positionFilter === p.key }"
              @click="positionFilter = p.key"
            >
              {{ p.key }} <b>{{ p.count }}</b>
            </button>
          </div>
          <div v-else class="pr-posts">
            <button type="button" :class="{ on: !stationFilter }" @click="stationFilter = ''">全部</button>
            <button
              v-for="s in stations"
              :key="s.key"
              type="button"
              :class="{ on: stationFilter === s.key }"
              @click="stationFilter = s.key"
            >
              {{ stationName(s.key) }} <b>{{ s.count }}</b>
            </button>
          </div>
          <label v-if="tab === 'pending' && !readonly" class="pr-switch">
            <input v-model="showAllDormant" type="checkbox">
            带上 90 天没卖的菜
          </label>
        </div>

        <p v-if="loading" class="pr-empty">加载中…</p>
        <p v-else-if="!visibleGroups.length" class="pr-empty">
          {{ tab === 'covered' ? '没有匹配的规则' : '没有待配的菜，全部配齐了' }}
        </p>
        <ul v-else class="pr-items">
          <li
            v-for="g in visibleGroups"
            :key="g.key"
            class="pr-item"
            :class="{ on: g.key === selectedKey }"
            @click="pick(g)"
          >
            <span class="pr-rail"></span>
            <div class="pr-item-main">
              <div class="pr-item-name">{{ g.main_name }}</div>
              <div class="pr-item-meta">
                <template v-if="g.has_rules">
                  <span class="pr-semi">{{ g.variants[0].prep_item_name || g.variants[0].semi_name }}</span>
                  <span>× {{ g.variants[0].factor }} {{ g.variants[0].unit }}</span>
                  <span v-if="materialCount(g) > 1" class="pr-badge">+{{ materialCount(g) - 1 }} 料</span>
                  <span v-else-if="g.variants.length > 1" class="pr-badge">+{{ g.variants.length - 1 }} 变体</span>
                </template>
                <span v-else class="pr-miss">还没有系数</span>
              </div>
            </div>
            <span class="pr-qty">{{ Math.round(g.qty).toLocaleString() }}</span>
          </li>
        </ul>
        <button
          v-if="tab === 'pending' && !readonly"
          type="button"
          class="pr-manual"
          @click="startManualCreate"
        >
          ＋ 手输一个菜名（POS 里还没有的）
        </button>
      </aside>

      <section class="pr-panel pr-detail" :class="{ 'is-open': mobileDetail }">
        <p v-if="!detailGroup" class="pr-empty">左边选一道菜，或从待配里点一条 / 手输一个菜名</p>
        <template v-else>
          <div class="pr-detail-head">
            <button type="button" class="pr-back" aria-label="返回列表" @click="mobileDetail = false">‹</button>
            <div>
              <h2 v-if="!manualMode">{{ detailGroup.main_name }}</h2>
              <input
                v-else
                v-model="draft.manualDishName"
                class="pr-input pr-manual-name"
                placeholder="输入菜名（POS 里还没有的菜）"
              >
              <div class="pr-detail-meta">
                <span class="pr-st">{{ stationName(detailGroup.station) }}</span>
                <span v-if="materialCount(detailGroup) > 1">· {{ materialCount(detailGroup) }} 种备货品</span>
                <span v-if="selectedVariants.length > 1">· {{ selectedVariants.length }} 条变体</span>
                <span v-else-if="!readonly">· 近 90 天 {{ Math.round(detailGroup.qty).toLocaleString() }} 份</span>
              </div>
            </div>
            <div v-if="!readonly" class="pr-detail-qty">
              <b>{{ Math.round(detailGroup.qty).toLocaleString() }}</b>
              <span>近 90 天份数</span>
            </div>
          </div>

          <div class="pr-body">
            <div v-if="draft?.mode === 'create'" class="pr-tip">
              <template v-if="detailGroup?.has_rules">
                给这道菜再加一个备货品（拼盘/套餐会用到多个）。选品、填一份用量，保存后就并进这张单子。
              </template>
              <template v-else>
                这道菜还没有换算规则。选一个备货品、填上「一份用多少」，保存后今天的备货建议量就会把它算进去。
              </template>
            </div>

            <div class="pr-grid">
              <label class="pr-f wide">
                <span>备货品 <em>{{ draft?.prep_item_id ? '已在备货品清单里' : '清单里没有 · 选一个' }}</em></span>
                <div class="pr-combo">
                  <input
                    v-model="prepQuery"
                    class="pr-input"
                    :disabled="readonly"
                    :placeholder="draft.mode === 'edit' ? '' : '打名字搜，比如 金莎 / 叉烧包'"
                    @focus="comboOpen = true"
                    @blur="comboOpen = false"
                  >
                  <ul v-if="comboOpen && !readonly" class="pr-combo-list">
                    <li
                      v-for="it in filteredPrepItems"
                      :key="it.id"
                      @mousedown.prevent="chooseItem(it)"
                    >
                      <span class="pr-combo-name">{{ it.item_name }}<em v-if="it.unit">（{{ it.unit }}）</em></span>
                      <span class="pr-combo-tags">
                        <span v-if="it.position" class="pr-tag">{{ it.position }}</span>
                        <span class="pr-tag" :class="{ bought: it.kind === 'bought' }">
                          {{ it.kind === 'made' ? '半成品' : '原料' }}
                        </span>
                      </span>
                    </li>
                    <li v-if="!filteredPrepItems.length" class="pr-combo-empty">
                      清单里没有匹配的备货品
                    </li>
                    <li v-if="prepQuery.trim() && !exactNameMatch" class="pr-combo-create">
                      <button type="button" @mousedown.prevent="openCreateItem">
                        ＋ 新建「{{ prepQuery.trim() }}」
                      </button>
                    </li>
                  </ul>
                </div>
              </label>

              <label class="pr-f">
                <span>系数 <em>一份菜用多少</em></span>
                <input v-model="draft.factor" class="pr-input pr-hot" inputmode="decimal" :disabled="readonly">
              </label>
              <label class="pr-f">
                <span>单位 <em>默认跟备货品</em></span>
                <input v-model="draft.unit" class="pr-input" :disabled="readonly" placeholder="跟备货品">
              </label>
              <label class="pr-f">
                <span>岗位 <em>默认跟备货品</em></span>
                <select v-model="draft.position" class="pr-input" :disabled="readonly">
                  <option value="">— 未定 —</option>
                  <option v-for="p in positionOptions" :key="p.name" :value="p.name">{{ p.name }}</option>
                </select>
              </label>
              <label class="pr-f">
                <span>分类 <em>默认跟备货品</em></span>
                <select v-model="draft.category" class="pr-input" :disabled="readonly">
                  <option value="">— 未补 —</option>
                  <option value="半成品">半成品</option>
                  <option value="原料">原料</option>
                </select>
              </label>
            </div>

            <div v-if="!readonly" class="pr-calc">
              近 90 天 <b>{{ Math.round(detailGroup.qty).toLocaleString() }}</b> 份
              <span class="pr-eq">×</span> 系数 <b>{{ draft?.factor || '—' }}</b>
              <span class="pr-eq">=</span> 备货量
              <b class="pr-res">
                {{ Math.round(detailGroup.qty * (Number(draft?.factor) || 0)).toLocaleString() }}
                {{ draft?.unit || '' }}
              </b>
            </div>

            <div v-if="selectedVariants.length > 1" class="pr-card">
              <h3>这个菜的用料与变体 <span>拼盘/套餐会有多个备货品；外卖渠道各存一条</span></h3>
              <button
                v-for="(v, i) in selectedVariants"
                :key="v.id"
                type="button"
                class="pr-var"
                :class="{ on: i === (draft?.variantIndex ?? 0) }"
                @click="switchVariant(i)"
              >
                <span class="pr-var-name">{{ v.dish_name }}<em v-if="v.prep_item_name"> · {{ v.prep_item_name }}</em></span>
                <span class="pr-var-fac">{{ v.factor }} {{ v.unit }}</span>
              </button>
              <label class="pr-chk">
                <input v-model="syncVariants" type="checkbox">
                保存时同步改所有变体
              </label>
            </div>

            <p
              v-if="activeVariant && activeVariant.prep_item_position
                && activeVariant.position && activeVariant.position !== activeVariant.prep_item_position"
              class="pr-warn"
            >
              这条规则记的岗位是「{{ activeVariant.position }}」，备货品清单里是「{{ activeVariant.prep_item_position }}」。
              两者不一致时以你保存的为准，但备货页会按清单里那份算。
            </p>
            <p v-if="activeVariant?.unit_drift" class="pr-warn">
              这条规则写的是「{{ activeVariant.unit }}」，但备货品清单里是「{{ activeVariant.prep_item_unit }}」——
              数量口径可能对不上，先核对再改。
            </p>
          </div>

          <div v-if="!readonly" class="pr-foot">
            <button type="button" class="pr-btn primary" :disabled="saving" @click="save">
              {{ saving ? '保存中…' : (draft?.mode === 'create' ? '建规则' : '保存') }}
            </button>
            <button
              v-if="draft?.mode === 'edit' && detailGroup?.has_rules"
              type="button"
              class="pr-btn"
              @click="startAddMaterial"
            >
              ＋ 加一个备货品
            </button>
            <button
              v-if="draft?.mode === 'edit' && activeVariant"
              type="button"
              class="pr-btn pr-danger"
              @click="removeCurrent"
            >
              删除这条
            </button>
            <span v-if="saveMsg" class="pr-save-msg">{{ saveMsg }}</span>
          </div>
        </template>
      </section>
    </div>

    <div v-if="newItem.open" class="pr-mask" @click.self="newItem.open = false">
      <div class="pr-modal" role="dialog" aria-label="新建备货品">
        <h3>新建备货品</h3>
        <p class="pr-modal-hint">建好后会直接选上；清单里那 72 个品之外才需要新建。</p>
        <label class="pr-f">
          <span>名称</span>
          <input v-model="newItem.name" class="pr-input" placeholder="比如 虾饺">
        </label>
        <div class="pr-modal-row">
          <label class="pr-f">
            <span>单位</span>
            <input v-model="newItem.unit" class="pr-input" placeholder="个 / 份 / 斤">
          </label>
          <label class="pr-f">
            <span>半成品 / 原料</span>
            <select v-model="newItem.kind" class="pr-input">
              <option value="made">半成品</option>
              <option value="bought">原料</option>
            </select>
          </label>
        </div>
        <div class="pr-modal-row">
          <!-- 只留一个归属控件：`kind` 的二分就是分类，后端按它派生 category。
               以前这里还有一个「分类」下拉（选项一模一样），两个控件填出过互相矛盾的值。 -->
          <label class="pr-f wide">
            <span>岗位</span>
            <select v-model="newItem.position" class="pr-input">
              <option value="">— 未定 —</option>
              <option v-for="p in positionOptions" :key="p.name" :value="p.name">{{ p.name }}</option>
            </select>
          </label>
        </div>
        <p v-if="newItem.err" class="pr-error">{{ newItem.err }}</p>
        <div class="pr-modal-foot">
          <button type="button" class="pr-btn primary" :disabled="newItem.saving" @click="submitNewItem">
            {{ newItem.saving ? '建中…' : '建好并选中' }}
          </button>
          <button type="button" class="pr-btn" @click="newItem.open = false">取消</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pr {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
  color: var(--ink);
}
.pr-head { display: flex; align-items: center; gap: 20px; flex: none; }
.pr-title h1 { font-size: var(--fs-20); font-weight: 600; margin: 0; }
.pr-title p { font-size: var(--fs-12); color: var(--ink-dim); margin: 4px 0 0; }
.pr-cover { flex: 1; display: flex; align-items: center; gap: 10px; }
.pr-bar { flex: 1; height: 5px; border-radius: 99px; background: var(--line); overflow: hidden; }
.pr-bar i { display: block; height: 100%; border-radius: 99px; background: var(--action); }
.pr-bar i.w0 { width: 0%; }
.pr-bar i.w10 { width: 10%; }
.pr-bar i.w20 { width: 20%; }
.pr-bar i.w30 { width: 30%; }
.pr-bar i.w40 { width: 40%; }
.pr-bar i.w50 { width: 50%; }
.pr-bar i.w60 { width: 60%; }
.pr-bar i.w70 { width: 70%; }
.pr-bar i.w80 { width: 80%; }
.pr-bar i.w90 { width: 90%; }
.pr-bar i.w100 { width: 100%; }
.pr-cov-text { font-size: var(--fs-11); color: var(--ink-dim); white-space: nowrap; padding-right: 2px; }
.pr-cov-text b { color: var(--ink); }
.pr-cov-text .warn { color: var(--warn); }
.pr-error { margin: 0; font-size: var(--fs-12); color: var(--danger-bright); }
.pr-migrate { margin: 0; font-size: var(--fs-11); color: var(--warn); background: var(--warn-soft); border: 1px solid var(--warn-soft); border-radius: var(--r-1); padding: 8px 12px; }

/* 外壳 `.wb-main` 是自然流、不持有高度：这里按视口扣掉顶栏/页头/padding 给双栏一个
   确定的高度，否则面板被内容撑高、底部「保存」会掉到视口外（实测踩过）。 */
.pr-split { display: grid; grid-template-columns: 352px 1fr; gap: 12px;
  height: calc(100vh - 210px); min-height: 420px; }
.pr-panel {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.pr-tools { padding: 12px 12px 8px; display: flex; flex-direction: column; gap: 8px; flex: none; }
.pr-input {
  width: 100%;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  padding: 8px 11px;
  color: var(--ink);
  font-size: var(--fs-13);
  font-family: inherit;
  outline: none;
}
.pr-input:focus { border-color: var(--line-control); }
.pr-seg { display: flex; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-1); padding: 2px; }
.pr-seg button {
  flex: 1;
  border: 0;
  background: transparent;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  font-family: inherit;
  padding: 5px 0;
  border-radius: 6px;
  cursor: pointer;
}
.pr-seg button.on { background: var(--action-soft); color: var(--ink); font-weight: 600; }
.pr-seg b { color: var(--ink-faint); margin-left: 3px; }
.pr-seg button.on b { color: var(--action); }
.pr-posts {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
  padding: 1px 0 2px;
  mask-image: linear-gradient(90deg, black calc(100% - 24px), transparent);
}
.pr-posts::-webkit-scrollbar { display: none; }
.pr-posts button {
  flex: none;
  font-size: var(--fs-11);
  padding: 4px 9px;
  border-radius: 99px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink-dim);
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
}
.pr-posts button b { color: var(--ink-faint); margin-left: 3px; }
.pr-posts button.on { background: var(--action-soft); border-color: var(--line-control); color: var(--action); }
.pr-posts button.on b { color: var(--action); }
.pr-hint { margin: 0; font-size: var(--fs-11); color: var(--ink-faint); }

.pr-items { list-style: none; margin: 0; padding: 0 8px 8px; overflow: auto; flex: 1; }
.pr-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px;
  border-radius: var(--r-1);
  cursor: pointer;
  margin-bottom: 1px;
}
.pr-item:hover { background: var(--action-soft); }
.pr-item.on { background: var(--action-soft); box-shadow: inset 3px 0 0 var(--action); }
.pr-rail { width: 3px; height: 26px; border-radius: 99px; flex: none; background: var(--line-control); }
.pr-item-main { flex: 1; min-width: 0; }
.pr-item-name { font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pr-item-meta { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--ink-faint); margin-top: 2px; overflow: hidden; white-space: nowrap; }
.pr-semi { color: var(--action); }
.pr-badge { border: 1px solid var(--line-strong); border-radius: 99px; padding: 0 5px; font-size: 10px; }
.pr-miss { color: var(--warn); }
.pr-qty { font-size: var(--fs-11); color: var(--ink-dim); font-variant-numeric: tabular-nums; }
.pr-empty { padding: 24px 16px; font-size: var(--fs-12); color: var(--ink-faint); }

.pr-detail { flex: 1; }
.pr-back { display: none; border: 0; background: transparent; color: var(--ink-dim); font-size: var(--fs-20); font-family: inherit; padding: 0 4px; cursor: pointer; line-height: 1; }
.pr-var-name em { font-style: normal; color: var(--ink-faint); }
.pr-detail-head { display: flex; align-items: flex-start; gap: 12px; padding: 16px 20px 14px; border-bottom: 1px solid var(--line); flex: none; }
.pr-detail-head h2 { font-size: var(--fs-16); font-weight: 600; margin: 0; }
.pr-detail-meta { display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: var(--fs-11); color: var(--ink-faint); }
.pr-st { background: var(--info-soft); color: var(--info); border-radius: 99px; padding: 2px 8px; }
.pr-detail-qty { margin-left: auto; text-align: right; }
.pr-detail-qty b { display: block; font-size: var(--fs-16); font-variant-numeric: tabular-nums; }
.pr-detail-qty span { font-size: var(--fs-11); color: var(--ink-faint); }
.pr-body { flex: 1; overflow: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 14px; }
.pr-tip { background: var(--warn-soft); border: 1px solid var(--warn-soft); border-radius: var(--r-1); padding: 9px 12px; font-size: var(--fs-11); color: var(--warn); line-height: 1.6; }
.pr-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 14px; }
.pr-f { display: flex; flex-direction: column; gap: 5px; }
.pr-f.wide { grid-column: span 2; }
.pr-f > span { font-size: var(--fs-11); color: var(--ink-dim); display: flex; align-items: center; gap: 5px; }
.pr-f em { font-style: normal; font-size: 10px; color: var(--ink-faint); border: 1px solid var(--line); border-radius: 99px; padding: 0 6px; }
.pr-f .pr-input[disabled] { color: var(--ink-faint); }
.pr-hot { color: var(--action); border-color: var(--line-control); }
/* 选品候选：绝对定位的下拉卡片（之前这段样式被一次替换弄丢了，列表浮在页面上没有框） */
.pr-combo { position: relative; }
.pr-combo-list {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: var(--z-dialog);
  background: var(--surface-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  box-shadow: 0 12px 32px var(--scrim);
  max-height: 240px;
  overflow: auto;
  list-style: none;
  margin: 0;
  padding: 4px;
}
.pr-combo-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 6px;
  font-size: var(--fs-13);
  cursor: pointer;
}
.pr-combo-list li:hover { background: var(--action-soft); }
.pr-combo-name { color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pr-combo-name em { font-style: normal; color: var(--ink-faint); font-size: var(--fs-11); margin-left: 1px; }
.pr-combo-tags { display: flex; align-items: center; gap: 6px; flex: none; }
.pr-tag {
  font-size: var(--fs-11);
  line-height: 1;
  color: var(--ink-dim);
  border: 1px solid var(--line-strong);
  border-radius: 99px;
  padding: 3px 7px;
  white-space: nowrap;
}
.pr-tag.bought { color: var(--warn); border-color: var(--warn-soft); }
.pr-combo-empty { color: var(--warn); font-size: var(--fs-11); cursor: default; }
.pr-combo-empty:hover { background: transparent; }
.pr-combo-create { padding: 2px 4px 4px; cursor: default; }
.pr-combo-create button {
  width: 100%;
  text-align: left;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px dashed var(--line-control);
  background: transparent;
  color: var(--action);
  font-size: var(--fs-12);
  font-family: inherit;
  cursor: pointer;
}
.pr-calc {
  display: flex; align-items: center; gap: 8px;
  background: var(--action-soft);
  border: 1px solid var(--action-soft);
  border-radius: var(--r-1);
  padding: 10px 12px;
  font-size: var(--fs-12);
  color: var(--ink-dim);
}
.pr-calc b { color: var(--ink); font-variant-numeric: tabular-nums; }
.pr-eq { color: var(--ink-faint); }
.pr-res { color: var(--action) !important; }
.pr-card { border: 1px solid var(--line); border-radius: var(--r-1); padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; background: var(--surface-2); }
.pr-card h3 { margin: 0; font-size: var(--fs-12); font-weight: 600; color: var(--ink-dim); display: flex; gap: 8px; align-items: center; }
.pr-card h3 span { font-weight: 500; color: var(--ink-faint); font-size: var(--fs-11); }
.pr-var {
  display: flex; align-items: center; justify-content: space-between;
  border: 1px solid transparent;
  background: transparent;
  color: var(--ink-dim);
  font-family: inherit;
  font-size: var(--fs-12);
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  text-align: left;
}
.pr-var:hover { background: var(--action-soft); }
.pr-var.on { border-color: var(--line-strong); background: var(--action-soft); color: var(--ink); }
.pr-var-fac { color: var(--action); font-variant-numeric: tabular-nums; }
.pr-chk { display: flex; align-items: center; gap: 7px; font-size: var(--fs-11); color: var(--ink-dim); margin-top: 2px; }
.pr-warn { margin: 0; font-size: var(--fs-11); color: var(--warn); line-height: 1.6; }
.pr-foot { flex: none; display: flex; align-items: center; gap: 10px; padding: 12px 20px; border-top: 1px solid var(--line); }
.pr-btn { border: 1px solid var(--line); background: transparent; color: var(--ink); padding: 8px 16px; border-radius: var(--r-1); font-size: var(--fs-13); font-family: inherit; cursor: pointer; }
.pr-btn.primary { background: var(--action); border-color: var(--action); color: var(--action-ink); font-weight: 600; }
.pr-btn[disabled] { opacity: 0.6; cursor: default; }
.pr-save-msg { font-size: var(--fs-11); color: var(--ink-faint); }
.pr-danger { color: var(--danger-bright); border-color: var(--danger-soft); }
.pr-switch { display: flex; align-items: center; gap: 6px; font-size: var(--fs-11); color: var(--ink-dim); cursor: pointer; }
.pr-manual { flex: none; margin: 0 8px 8px; padding: 7px 0; border: 1px dashed var(--line-control);
  border-radius: var(--r-1); background: transparent; color: var(--ink-dim); font-size: var(--fs-11);
  font-family: inherit; cursor: pointer; }
.pr-manual:hover { color: var(--action); border-color: var(--action); }
.pr-manual-name { font-size: var(--fs-16); font-weight: 600; padding: 6px 10px; }
.pr-combo-create { padding: 2px 4px 4px; cursor: default; }
.pr-combo-create button { width: 100%; text-align: left; padding: 8px 10px; border-radius: 6px;
  border: 1px dashed var(--line-control); background: transparent; color: var(--action);
  font-size: var(--fs-12); font-family: inherit; cursor: pointer; }
.pr-mask { position: fixed; inset: 0; z-index: var(--z-dialog); background: var(--scrim);
  display: flex; align-items: center; justify-content: center; }
.pr-modal { width: 420px; max-width: calc(100vw - 32px); background: var(--surface-1);
  border: 1px solid var(--line-strong); border-radius: var(--r-2); padding: 18px 20px;
  display: flex; flex-direction: column; gap: 12px; }
.pr-modal h3 { margin: 0; font-size: var(--fs-16); font-weight: 600; }
.pr-modal-hint { margin: 0; font-size: var(--fs-11); color: var(--ink-faint); }
.pr-modal-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.pr-modal-foot { display: flex; gap: 10px; justify-content: flex-end; }

@media (max-width: 900px) {
  /* 手机上外壳不给内容区左右内边距：自己补，否则标题贴边 */
  .pr { padding: 0 12px; }
  .pr-split { grid-template-columns: 1fr; }
  .pr-detail { display: none; }
  /* 手机上双栏放不下：详情变成盖在列表上的一层，点返回回列表 */
  .pr-detail.is-open {
    display: flex;
    position: fixed;
    inset: 0;
    z-index: var(--z-dialog);
    background: var(--surface-0);
    border-radius: 0;
  }
  .pr-back { display: block; }
  .pr-head { flex-direction: column; align-items: stretch; }
}
</style>
