<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { resolveTablePlugin } from '../../admin/tablePlugins'
import { useAdminTable } from '../../composables/useAdminTable'
import { useNudgePull } from '../../composables/useNudgePull'
import { getCellLabel, getColumnLabel, getTableIcon, getTableLabel } from '../../utils/adminLabels'
import SvgIcon from '../SvgIcon.vue'
import RowEditModal from './RowEditModal.vue'
import ColumnManageModal from './ColumnManageModal.vue'
import DataQualityPanel from './DataQualityPanel.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import BatchEditModal from './BatchEditModal.vue'
import LuyunNumberInput from '../ui/LuyunNumberInput.vue'
import TableIcon from './TableIcon.vue'

const {
  tables, currentTable, schema, columns, rows, total, page, pageSize, pages,
  sortField, sortDir, searchField, searchValue, loading, error,
  loadTables, loadRows, switchTable, sortBy, goToPage, createRow, updateRow, deleteRow, deleteRows, updateRows,
  addColumn, dropColumn, rowKey,
} = useAdminTable()

const activePlugin = computed(() => resolveTablePlugin(currentTable.value))
const tableReadOnly = computed(() => Boolean(activePlugin.value?.readOnly))

const router = useRouter()

const modalMode = ref(null) // 'create' | 'edit' | null
const editingRow = ref(null)
const showColumnModal = ref(false)
const showDataQualityModal = ref(false)
const toastMsg = ref('')
const toastType = ref('success') // 'success' | 'error'
const confirmDeleteRow = ref(null)
const showBatchDeleteConfirm = ref(false)
const showBatchEditModal = ref(false)
const selectedRowIds = ref(new Set())

const selectedCount = computed(() => selectedRowIds.value.size)
const allPageSelected = computed(() =>
  rows.value.length > 0 && rows.value.every((row) => selectedRowIds.value.has(rowKey(row))),
)

function clearSelection() {
  selectedRowIds.value = new Set()
}

function toggleRowSelection(id) {
  const next = new Set(selectedRowIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedRowIds.value = next
}

function toggleSelectAllPage() {
  if (allPageSelected.value) {
    clearSelection()
    return
  }
  selectedRowIds.value = new Set(rows.value.map((row) => rowKey(row)))
}

watch(currentTable, clearSelection)
watch(page, clearSelection)

// 移动端表列表抽屉，对齐旧页 public/index.html 的 toggleAdminSidebar（899px 断点）。
const MOBILE_SIDEBAR_QUERY = '(max-width: 899px)'
const sidebarOpen = ref(false)
let mobileSidebarMql = null

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value
}
function closeSidebar() {
  sidebarOpen.value = false
}
async function selectTable(t) {
  if (t !== currentTable.value) await switchTable(t)
  closeSidebar()
}

useNudgePull({
  id: 'admin-data-table',
  topics: ['admin'],
  pull: loadRows,
  match: (ev) => {
    if (ev.type !== 'nudge' || ev.topic !== 'admin') return false
    const table = ev.scope?.table
    return !table || table === currentTable.value
  },
})

function flashToast(msg, type = 'success') {
  toastMsg.value = msg
  toastType.value = type
  setTimeout(() => { if (toastMsg.value === msg) toastMsg.value = '' }, 3000)
}

async function handleAddColumn(payload) {
  try {
    await addColumn(payload)
    flashToast(`字段 ${payload.column_name} 添加成功`)
  } catch (e) {
    window.alert(e.message || '添加字段失败')
  }
}

async function handleDropColumn(columnName) {
  try {
    await dropColumn(columnName)
    flashToast(`字段 ${columnName} 已删除`)
  } catch (e) {
    window.alert(e.message || '删除字段失败')
  }
}

onMounted(async () => {
  await loadTables()
  if (currentTable.value) await switchTable(currentTable.value)
  mobileSidebarMql = window.matchMedia(MOBILE_SIDEBAR_QUERY)
  mobileSidebarMql.addEventListener('change', closeSidebar)
})

onBeforeUnmount(() => {
  mobileSidebarMql?.removeEventListener('change', closeSidebar)
})

function openCreate() {
  editingRow.value = {}
  modalMode.value = 'create'
}
function openEdit(row) {
  editingRow.value = row
  modalMode.value = 'edit'
}
function closeModal() {
  modalMode.value = null
  editingRow.value = null
}

async function handleSubmit(values) {
  if (modalMode.value === 'create') {
    await createRow(values)
  } else {
    await updateRow(rowKey(editingRow.value), values)
  }
  closeModal()
}

function handleDelete(row) {
  confirmDeleteRow.value = row
}

async function confirmDelete() {
  const row = confirmDeleteRow.value
  confirmDeleteRow.value = null
  if (!row) return
  await deleteRow(rowKey(row))
}

function handleBatchDelete() {
  if (!selectedCount.value) return
  showBatchDeleteConfirm.value = true
}

function handleBatchEdit() {
  if (!selectedCount.value) return
  showBatchEditModal.value = true
}

async function handleBatchEditSubmit({ column, value }) {
  const ids = [...selectedRowIds.value]
  showBatchEditModal.value = false
  if (!ids.length || !column) return
  try {
    const res = await updateRows(ids, column, value)
    clearSelection()
    const plugin = activePlugin.value
    const msg = plugin?.afterBatchUpdate
      ? plugin.afterBatchUpdate({ column, value, res, ids })
      : (res.message || `已更新 ${res.affected ?? ids.length} 条`)
    flashToast(msg)
  } catch (e) {
    flashToast(e.message || '批量修改失败', 'error')
  }
}

async function confirmBatchDelete() {
  const ids = [...selectedRowIds.value]
  showBatchDeleteConfirm.value = false
  if (!ids.length) return
  try {
    const res = await deleteRows(ids)
    clearSelection()
    flashToast(res.message || `已删除 ${res.affected ?? ids.length} 条`)
  } catch (e) {
    flashToast(e.message || '批量删除失败', 'error')
  }
}

function doSearch() {
  page.value = 1
  // loadRows 由 switchTable/goToPage/sortBy 内部触发；这里直接复用 goToPage(1) 的副作用
  goToPage(1)
}

function clearSearch() {
  searchField.value = ''
  searchValue.value = ''
  doSearch()
}

const pageJumpInput = ref('')

function jumpToPage() {
  const target = parseInt(pageJumpInput.value, 10)
  if (Number.isNaN(target)) return
  goToPage(target)
  pageJumpInput.value = ''
}

function formatCellText(col, val) {
  if (val === null || val === undefined || val === '') return ''
  if (typeof val === 'object') return JSON.stringify(val)
  const mapped = getCellLabel(currentTable.value, col, val)
  return mapped === undefined || mapped === null ? String(val) : String(mapped)
}
</script>

<template>
  <div class="dt-shell" :class="{ 'sidebar-open': sidebarOpen }">
    <div class="dt-sidebar-backdrop" aria-hidden="true" @click="closeSidebar"></div>
    <aside class="dt-sidebar">
      <div class="dt-sidebar-header">数据表</div>
      <div class="dt-table-list luyun-scrollbar">
        <div
          v-for="t in tables"
          :key="t"
          class="dt-table-item"
          :class="{ active: t === currentTable }"
          @click="selectTable(t)"
        >
          <TableIcon :name="getTableIcon(t)" :size="15" />
          <span>{{ getTableLabel(t) }}</span>
        </div>
      </div>
    </aside>

    <div class="dt-main">
      <div class="card" style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
        <button
          type="button"
          class="btn btn-sm dt-sidebar-toggle"
          aria-label="打开数据表列表"
          :aria-expanded="sidebarOpen ? 'true' : 'false'"
          @click="toggleSidebar"
        ><TableIcon name="menu" :size="14" /> {{ getTableLabel(currentTable) }}</button>
        <select class="select" v-model="searchField" style="width:140px">
          <option value="">-- 搜索字段 --</option>
          <option v-for="c in columns" :key="c" :value="c">{{ getColumnLabel(currentTable, c) }}</option>
        </select>
        <input class="input" v-model="searchValue" placeholder="搜索内容" @keyup.enter="doSearch" style="width:180px" />
        <button class="btn" @click="doSearch"><SvgIcon name="search" :size="13" /> 搜索</button>
        <button class="btn btn-sm" @click="clearSearch" :disabled="!searchField && !searchValue">清除</button>
        <button
          v-if="rows.length && !loading && !tableReadOnly"
          class="btn btn-sm"
          @click="toggleSelectAllPage"
        >{{ allPageSelected ? '取消全选' : '全选本页' }}</button>
        <template v-if="selectedCount && !tableReadOnly">
          <span class="dt-batch-hint">已选 {{ selectedCount }} 条</span>
          <button class="btn btn-sm" @click="handleBatchEdit"><SvgIcon name="pencil" :size="12" /> 批量修改</button>
          <button class="btn btn-sm btn-danger" @click="handleBatchDelete"><SvgIcon name="trash-2" :size="12" /> 批量删除</button>
          <button class="btn btn-sm" @click="clearSelection">取消选择</button>
        </template>
        <span v-else-if="tableReadOnly" class="dt-batch-hint">只读表 · 请用「快捷添加 / 批量分类」维护映射</span>
        <component
          :is="activePlugin.Extras"
          v-if="activePlugin?.Extras"
          :flash-toast="flashToast"
          :reload="loadRows"
          :after-quick-add="activePlugin.afterQuickAdd"
        />
        <button class="btn" style="margin-left:auto" @click="showDataQualityModal = true"><SvgIcon name="bar-chart" :size="13" /> 数据质量</button>
        <button class="btn" @click="router.push('/setup?section=backup')"><SvgIcon name="database" :size="13" /> 备份 / 迁移</button>
        <button v-if="!tableReadOnly" class="btn" @click="showColumnModal = true"><SvgIcon name="clipboard" :size="13" /> 表结构</button>
        <button v-if="!tableReadOnly" class="btn btn-primary" @click="openCreate"><SvgIcon name="plus" :size="13" /> 新增记录</button>
      </div>

      <div
        v-if="toastMsg"
        class="card"
        :style="{ padding: '8px 12px', fontSize: '12px', color: toastType === 'error' ? 'var(--red)' : 'var(--green)' }"
      >{{ toastMsg }}</div>

      <div v-if="loading" class="loading-state">加载中...</div>
      <div v-else-if="error" class="empty-state">{{ error }}</div>
      <div v-else-if="!rows.length" class="empty-state">暂无数据</div>
      <div v-else class="data-table-wrap luyun-scrollbar">
        <table class="data-table">
          <thead>
            <tr>
              <th style="width:60px">序号</th>
              <th
                v-for="col in columns"
                :key="col"
                :title="col"
                :aria-sort="sortField !== col ? 'none' : (sortDir === 'asc' ? 'ascending' : 'descending')"
                @click="sortBy(col)"
              >
                {{ getColumnLabel(currentTable, col) }}
                <span v-if="sortField === col">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
              </th>
              <th v-if="!tableReadOnly">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in rows"
              :key="rowKey(row)"
              :class="{
                'dt-row-clickable': !tableReadOnly,
                'dt-row-selected': selectedRowIds.has(rowKey(row)),
              }"
              @click="tableReadOnly ? undefined : toggleRowSelection(rowKey(row))"
            >
              <td style="color:var(--text-dim)">{{ rowKey(row) }}</td>
              <td v-for="col in columns" :key="col" :title="formatCellText(col, row[col])">
                <span v-if="row[col] === null || row[col] === undefined" style="color:var(--text-dim)">NULL</span>
                <span v-else-if="row[col] === ''" style="color:var(--text-dim)">—</span>
                <template v-else>{{ formatCellText(col, row[col]) }}</template>
              </td>
              <td v-if="!tableReadOnly" class="actions" @click.stop>
                <button class="btn btn-sm" @click="openEdit(row)"><SvgIcon name="pencil" :size="12" /> 编辑</button>
                <button class="btn btn-sm btn-danger" @click="handleDelete(row)"><SvgIcon name="trash-2" :size="12" /> 删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="pagination" v-if="rows.length">
        <span>共 {{ total }} 条，第 {{ page }}/{{ pages }} 页</span>
        <button class="btn btn-sm" :disabled="page <= 1" @click="goToPage(1)">首页</button>
        <button class="btn btn-sm" :disabled="page <= 1" @click="goToPage(page - 1)">上一页</button>
        <button class="btn btn-sm" :disabled="page >= pages" @click="goToPage(page + 1)">下一页</button>
        <button class="btn btn-sm" :disabled="page >= pages" @click="goToPage(pages)">末页</button>
        <LuyunNumberInput
          v-model="pageJumpInput"
          compact
          :min="1"
          :max="pages"
          placeholder="页码"
          @enter="jumpToPage"
        />
        <button class="btn btn-sm" @click="jumpToPage">跳转</button>
      </div>
    </div>

    <RowEditModal
      v-if="modalMode"
      :schema="schema"
      :table="currentTable"
      :mode="modalMode"
      :initial-values="modalMode === 'edit' ? editingRow : {}"
      :title="modalMode === 'create' ? '新增记录' : '编辑记录'"
      @close="closeModal"
      @submit="handleSubmit"
    />

    <ColumnManageModal
      v-if="showColumnModal"
      :schema="schema"
      :table="currentTable"
      @close="showColumnModal = false"
      @add="handleAddColumn"
      @drop="handleDropColumn"
    />

    <DataQualityPanel
      v-if="showDataQualityModal"
      @close="showDataQualityModal = false"
    />

    <BatchEditModal
      v-if="showBatchEditModal"
      :schema="schema"
      :table="currentTable"
      :selected-count="selectedCount"
      @close="showBatchEditModal = false"
      @submit="handleBatchEditSubmit"
    />

    <ConfirmDialog
      v-if="showBatchDeleteConfirm"
      title="批量删除确认"
      :message="`确认删除已选的 ${selectedCount} 条记录？此操作不可撤销。`"
      confirm-label="批量删除"
      danger
      @confirm="confirmBatchDelete"
      @cancel="showBatchDeleteConfirm = false"
    />

    <ConfirmDialog
      v-if="confirmDeleteRow"
      title="删除确认"
      :message="`确认删除该行（rowid=${rowKey(confirmDeleteRow)}）？`"
      confirm-label="删除"
      danger
      @confirm="confirmDelete"
      @cancel="confirmDeleteRow = null"
    />
  </div>
</template>

<style scoped>
/* 数据表侧边栏（选表交互），移植自旧页 public/index.html 的 .sidebar / .table-list / .admin-sidebar-toggle。 */
.dt-shell { display: flex; gap: 12px; align-items: flex-start; min-width: 0; }
.dt-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.dt-sidebar-backdrop { display: none; }

.dt-sidebar {
  width: 180px;
  flex-shrink: 0;
  background: var(--sidebar-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  max-height: 70vh;
  overflow: hidden;
}
.dt-sidebar-header {
  padding: 10px 12px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-dim);
  font-weight: 600;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.dt-table-list { flex: 1; overflow-y: auto; padding: 6px; }
.dt-table-item {
  padding: 7px 9px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text);
  user-select: none;
  transition: background .15s;
}
.dt-table-item:hover { background: var(--card2); }
.dt-table-item.active { background: var(--accent); color: #fff; }
.dt-table-item :deep(.dt-svg-icon) { opacity: .75; }
.dt-table-item.active :deep(.dt-svg-icon) { opacity: 1; }

.dt-sidebar-toggle { display: none; }

.dt-batch-hint {
  font-size: 12px;
  color: var(--text-dim);
}
.dt-row-selected {
  background: rgba(56, 189, 248, 0.28);
  box-shadow: inset 4px 0 0 #38bdf8;
}
:deep(table.data-table tbody tr.dt-row-selected) {
  background: rgba(56, 189, 248, 0.28);
  box-shadow: inset 4px 0 0 #38bdf8;
}
:deep(table.data-table tbody tr.dt-row-selected:hover) {
  background: rgba(56, 189, 248, 0.38);
  box-shadow: inset 4px 0 0 #7dd3fc;
}
.dt-row-clickable {
  cursor: pointer;
}

/* 平板/手机：表列表折叠为可切换抽屉（对齐旧页 899px 断点） */
@media (max-width: 899px) {
  .dt-sidebar-toggle { display: inline-flex; }

  .dt-sidebar {
    position: fixed;
    top: clamp(38px, 4.4vh, 46px);
    left: 0;
    bottom: 0;
    width: min(268px, 88vw);
    max-height: none;
    z-index: 60;
    border-radius: 0;
    transform: translateX(-105%);
    transition: transform .25s ease-out, box-shadow .25s ease-out;
    box-shadow: none;
  }
  .dt-shell.sidebar-open .dt-sidebar {
    transform: translateX(0);
    box-shadow: 4px 0 28px rgba(0, 0, 0, 0.45);
  }
  .dt-sidebar-backdrop {
    display: block;
    position: fixed;
    left: 0; right: 0; bottom: 0;
    top: clamp(38px, 4.4vh, 46px);
    background: rgba(0, 0, 0, 0.55);
    z-index: 55;
    opacity: 0;
    pointer-events: none;
    transition: opacity .25s ease-out;
  }
  .dt-shell.sidebar-open .dt-sidebar-backdrop {
    opacity: 1;
    pointer-events: auto;
  }
}
</style>
