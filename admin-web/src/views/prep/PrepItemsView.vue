<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../../api/client'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'

/**
 * 备货品主数据（2026-10-27）。
 *
 * 备货计划按「备货品」出清单、换算规则按「备货品」算系数，但主数据本身一直只能从
 * 数据管理那张裸表里改。这一页补上维护面：列表（含停用的品）+ 就地编辑
 * 名称 / 单位 / 岗位 / 半成品·原料 / 备注 / 启用。
 *
 * 两条字段都是派生的、不让人填第二遍：
 *   · 归属（半成品 / 原料）就是 `kind`，`category` 由后端跟着派生；
 *   · 岗位决定档口（`POSITION_STATION_MAP`），档口也不单独填。
 */

workbenchDocumentTitle('备货品')

const KIND_LABEL = { made: '半成品', bought: '原料' }

const items = ref([])
const loading = ref(true)
const errorText = ref('')
const keyword = ref('')
const positionFilter = ref('')
const kindFilter = ref('')
const statusFilter = ref('')

const editing = ref(null)
const form = reactive({
  item_name: '',
  unit: '',
  position: '',
  kind: 'made',
  active: true,
  notes: '',
})
const saving = ref(false)
const formError = ref('')

const positions = computed(() => {
  const seen = new Set()
  for (const row of items.value) {
    if (row.position) seen.add(row.position)
  }
  return [...seen].sort((a, b) => a.localeCompare(b, 'zh-CN'))
})

const counts = computed(() => ({
  total: items.value.length,
  active: items.value.filter((row) => row.active).length,
  unused: items.value.filter((row) => row.active && !row.rule_count).length,
}))

const filtered = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  return items.value.filter((row) => {
    if (text && !row.item_name.toLowerCase().includes(text)) return false
    if (positionFilter.value && (row.position || '') !== positionFilter.value) return false
    if (kindFilter.value && row.kind !== kindFilter.value) return false
    if (statusFilter.value === 'active' && !row.active) return false
    if (statusFilter.value === 'inactive' && row.active) return false
    return true
  })
})

async function load() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/semi-rules/prep-items/manage')
    items.value = data.items || []
  } catch (err) {
    errorText.value = err.message || '备货品清单读不出来'
  } finally {
    loading.value = false
  }
}

function startEdit(row) {
  editing.value = row
  form.item_name = row.item_name
  form.unit = row.unit
  form.position = row.position
  form.kind = row.kind
  form.active = row.active
  form.notes = row.notes
  formError.value = ''
}

function closeEdit() {
  if (saving.value) return
  editing.value = null
  formError.value = ''
}

async function removeItem() {
  if (saving.value || !editing.value) return
  const row = editing.value
  const impact = row.rule_count
    ? `它被 ${row.rule_count} 条换算规则引用：删掉后那些规则会变成「未对准备货品」，不再产生备货建议。\n已经算过的备货记录会保留。`
    : '目前没有换算规则引用它。'
  const ok = window.confirm(`删除备货品「${row.item_name}」？\n${impact}`)
  if (!ok) return
  saving.value = true
  formError.value = ''
  try {
    await api.delete(`/api/semi-rules/prep-items/${row.id}`)
    items.value = items.value.filter((item) => item.id !== row.id)
    editing.value = null
  } catch (err) {
    formError.value = err.message || '删除失败'
  } finally {
    saving.value = false
  }
}

async function save() {
  if (saving.value || !editing.value) return
  const name = form.item_name.trim()
  if (!name) {
    formError.value = '名称不能为空'
    return
  }
  saving.value = true
  formError.value = ''
  try {
    const data = await api.patch(`/api/semi-rules/prep-items/${editing.value.id}`, {
      item_name: name,
      unit: form.unit.trim(),
      position: form.position,
      kind: form.kind,
      active: form.active,
      notes: form.notes.trim(),
    })
    const next = data.item
    items.value = items.value.map((row) => (row.id === next.id ? { ...row, ...next } : row))
    editing.value = null
  } catch (err) {
    formError.value = err.message || '保存失败'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="pi">
    <header class="pi-head">
      <div>
        <p class="pi-kicker">备货品 · 主数据</p>
        <h1 class="pi-title">
          备货品清单 <b>{{ counts.total }}</b> 个
        </h1>
        <p class="pi-sub">
          在用 {{ counts.active }} 个 · 没有换算规则 {{ counts.unused }} 个 ·
          名称、单位、岗位改在这一页，备货计划和换算规则都读它
        </p>
      </div>
      <button type="button" class="pi-refresh" @click="load">刷新</button>
    </header>

    <div class="pi-toolbar">
      <input
        v-model="keyword"
        class="pi-search"
        type="search"
        placeholder="搜备货品名…"
        aria-label="搜备货品名"
      >
      <select v-model="positionFilter" class="pi-filter" aria-label="按岗位筛选">
        <option value="">全部岗位</option>
        <option v-for="position in positions" :key="position" :value="position">
          {{ position }}
        </option>
      </select>
      <select v-model="kindFilter" class="pi-filter" aria-label="按归属筛选">
        <option value="">全部归属</option>
        <option value="made">半成品</option>
        <option value="bought">原料</option>
      </select>
      <select v-model="statusFilter" class="pi-filter" aria-label="按状态筛选">
        <option value="">全部状态</option>
        <option value="active">在用</option>
        <option value="inactive">已停用</option>
      </select>
    </div>

    <p v-if="errorText" class="pi-error" role="status">{{ errorText }}</p>
    <p v-else-if="loading" class="pi-empty">正在读备货品清单…</p>
    <p v-else-if="!filtered.length" class="pi-empty">没有符合条件的备货品。</p>

    <ul v-else class="pi-rows">
      <li v-for="row in filtered" :key="row.id" class="pi-row" :class="{ 'is-off': !row.active }">
        <button type="button" class="pi-row-main" @click="startEdit(row)">
          <span class="pi-name">{{ row.item_name }}</span>
          <span class="pi-meta">
            {{ row.position || '未定岗位' }} · {{ KIND_LABEL[row.kind] || row.kind }}
          </span>
        </button>
        <span class="pi-unit">{{ row.unit || '—' }}</span>
        <span class="pi-rules" :class="{ 'is-none': !row.rule_count }">
          {{ row.rule_count ? `${row.rule_count} 条规则` : '没有规则' }}
        </span>
        <span class="pi-status" :class="{ 'is-off': !row.active }">
          {{ row.active ? '在用' : '已停用' }}
        </span>
        <button type="button" class="pi-edit" @click="startEdit(row)">编辑</button>
      </li>
    </ul>

    <div v-if="editing" class="pi-mask" @click.self="closeEdit">
      <form class="pi-sheet" role="dialog" aria-label="编辑备货品" @submit.prevent="save">
        <header class="pi-sheet-head">
          <h2>编辑备货品</h2>
          <button type="button" class="pi-close" aria-label="关闭" @click="closeEdit">×</button>
        </header>

        <label class="pi-field">
          <span>名称</span>
          <input v-model="form.item_name" class="pi-input" type="text">
        </label>
        <div class="pi-field-row">
          <label class="pi-field">
            <span>单位</span>
            <input v-model="form.unit" class="pi-input" type="text" placeholder="个 / 份 / 斤">
          </label>
          <label class="pi-field">
            <span>岗位</span>
            <select v-model="form.position" class="pi-input">
              <option value="">— 未定 —</option>
              <option v-for="position in positions" :key="position" :value="position">
                {{ position }}
              </option>
            </select>
          </label>
        </div>
        <div class="pi-field-row">
          <label class="pi-field">
            <span>半成品 / 原料</span>
            <select v-model="form.kind" class="pi-input">
              <option value="made">半成品</option>
              <option value="bought">原料</option>
            </select>
          </label>
          <label class="pi-field pi-check">
            <input v-model="form.active" type="checkbox">
            <span>在用</span>
          </label>
        </div>
        <label class="pi-field">
          <span>备注</span>
          <input v-model="form.notes" class="pi-input" type="text" placeholder="选填">
        </label>

        <p v-if="formError" class="pi-error" role="status">{{ formError }}</p>

        <footer class="pi-sheet-foot">
          <button type="button" class="pi-delete" :disabled="saving" @click="removeItem">
            删除
          </button>
          <button type="submit" class="pi-save" :disabled="saving">
            {{ saving ? '保存中…' : '保存' }}
          </button>
          <button type="button" class="pi-cancel" @click="closeEdit">取消</button>
        </footer>
      </form>
    </div>
  </div>
</template>

<style scoped>
.pi {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3, 12px);
  width: 100%;
  max-width: 1080px;
  min-width: 0;
  margin: 0 auto;
  padding: var(--sp-2, 8px) var(--sp-3, 12px) var(--sp-8, 32px);
  box-sizing: border-box;
  color: var(--ink);
}

.pi-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--sp-3, 12px);
  flex-wrap: wrap;
}

.pi-kicker {
  margin: 0 0 2px;
  font-size: var(--fs-12, 12px);
  color: var(--ink-dim);
  letter-spacing: var(--ls-2, 0.08em);
}

.pi-title {
  margin: 0;
  font-size: var(--fs-20, 20px);
  font-weight: 600;
}

.pi-title b {
  font-size: var(--fs-24, 24px);
  color: var(--action);
}

.pi-sub {
  margin: 4px 0 0;
  font-size: var(--fs-12, 12px);
  color: var(--ink-faint);
  font-variant-numeric: tabular-nums;
}

.pi-refresh {
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--line);
  border-radius: var(--r-1, 8px);
  background: var(--surface-1);
  color: var(--ink-dim);
  font-size: var(--fs-12, 12px);
}

.pi-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2, 8px);
}

.pi-search {
  flex: 1 1 220px;
  min-width: 0;
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1, 8px);
  background: var(--surface-2);
  color: var(--ink);
  font-size: var(--fs-13, 13px);
}

.pi-filter {
  min-height: 44px;
  padding: 0 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1, 8px);
  background: var(--surface-2);
  color: var(--ink-dim);
  font-size: var(--fs-12, 12px);
}

.pi-rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.pi-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 64px 92px 72px 56px;
  align-items: center;
  gap: var(--sp-2, 8px);
  min-height: 60px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line-control);
}

.pi-row-main {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 0;
  min-height: 44px;
  border: 0;
  background: none;
  color: inherit;
  text-align: left;
}

.pi-name {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-14, 14px);
}

.pi-meta {
  font-size: var(--fs-11, 11px);
  color: var(--ink-faint);
}

.pi-unit {
  font-size: var(--fs-13, 13px);
  color: var(--ink-dim);
  text-align: right;
}

.pi-rules {
  font-size: var(--fs-11, 11px);
  color: var(--ink-dim);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.pi-rules.is-none {
  color: var(--warn);
}

.pi-status {
  justify-self: end;
  padding: 2px 8px;
  border-radius: var(--r-full, 999px);
  background: var(--action-soft);
  color: var(--action);
  font-size: var(--fs-11, 11px);
}

.pi-status.is-off {
  background: none;
  border: 1px solid var(--line);
  color: var(--ink-faint);
}

.pi-edit {
  min-height: 44px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1, 8px);
  background: var(--surface-2);
  color: var(--ink-dim);
  font-size: var(--fs-12, 12px);
}

.pi-row.is-off .pi-name {
  color: var(--ink-faint);
}

.pi-empty {
  padding: var(--sp-6, 24px) var(--sp-4, 16px);
  border: 1px solid var(--line);
  border-radius: var(--r-2, 12px);
  background: var(--surface-1);
  color: var(--ink-dim);
  font-size: var(--fs-13, 13px);
  text-align: center;
}

.pi-error {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--danger);
  border-radius: var(--r-1, 8px);
  color: var(--danger-bright);
  font-size: var(--fs-12, 12px);
}

.pi-mask {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: var(--wb-scrim);
}

.pi-sheet {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3, 12px);
  width: 100%;
  max-width: 520px;
  max-height: 92vh;
  overflow-y: auto;
  padding: var(--sp-4, 16px);
  border: 1px solid var(--line);
  border-radius: var(--r-2, 12px) var(--r-2, 12px) 0 0;
  background: var(--surface-0);
}

.pi-sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.pi-sheet-head h2 {
  margin: 0;
  font-size: var(--fs-16, 16px);
}

.pi-close {
  width: 44px;
  height: 44px;
  border: 0;
  background: none;
  color: var(--ink-dim);
  font-size: var(--fs-20, 20px);
}

.pi-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.pi-field > span {
  font-size: var(--fs-11, 11px);
  color: var(--ink-dim);
}

.pi-field-row {
  display: flex;
  gap: var(--sp-3, 12px);
}

.pi-check {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  min-height: 44px;
}

.pi-check input {
  width: 22px;
  height: 22px;
}

.pi-check span {
  font-size: var(--fs-13, 13px);
  color: var(--ink);
}

.pi-input {
  min-height: 44px;
  padding: 0 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1, 8px);
  background: var(--surface-2);
  color: var(--ink);
  font-size: var(--fs-13, 13px);
}

.pi-sheet-foot {
  display: flex;
  gap: var(--sp-2, 8px);
  justify-content: flex-end;
  padding-top: 4px;
}

.pi-delete {
  margin-right: auto;
  min-height: 44px;
  padding: 0 16px;
  border: 1px solid var(--wb-danger-a45);
  border-radius: var(--r-1, 8px);
  background: none;
  color: var(--danger-bright);
  font-size: var(--fs-13, 13px);
}

.pi-delete[disabled] {
  opacity: 0.6;
}

.pi-save,
.pi-cancel {
  min-height: 44px;
  padding: 0 18px;
  border-radius: var(--r-1, 8px);
  font-size: var(--fs-13, 13px);
}

.pi-save {
  border: 1px solid var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}

.pi-save[disabled] {
  opacity: 0.6;
}

.pi-cancel {
  border: 1px solid var(--line-strong);
  background: none;
  color: var(--ink-dim);
}

@media (min-width: 720px) {
  .pi-mask {
    align-items: center;
  }

  .pi-sheet {
    border-radius: var(--r-2, 12px);
  }
}

@media (max-width: 720px) {
  /* 手机上收掉「规则数」这一列（它是维护参考，不是日常读数），
     单位与状态留着 —— 备货按个 / 份 / 斤，单位丢掉就看不懂数量。 */
  .pi-row {
    grid-template-columns: minmax(0, 1fr) 44px 60px 52px;
    gap: 6px;
  }

  .pi-rules {
    display: none;
  }
}
</style>
