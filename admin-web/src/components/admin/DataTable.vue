<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { resolveTablePlugin } from '../../admin/tablePlugins'
import { useAdminTable } from '../../composables/useAdminTable'
import { useNudgePull } from '../../composables/useNudgePull'
import { getCellLabel, getColumnLabel, getTableIcon, getTableLabel } from '../../utils/adminLabels'
import { describeRow } from '../../utils/adminRowSummary'
import SvgIcon from '../SvgIcon.vue'
import RowEditModal from './RowEditModal.vue'
import ColumnManageModal from './ColumnManageModal.vue'
import DataQualityPanel from './DataQualityPanel.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import BatchEditModal from './BatchEditModal.vue'
import LuyunNumberInput from '../ui/LuyunNumberInput.vue'
import TableIcon from './TableIcon.vue'

const {
  tables, tableGroups, tableMeta, currentTable, schema, columns, rows, total, page, pageSize, pages,
  sortField, sortDir, searchField, searchValue, loading, error,
  loadTables, loadRows, switchTable, sortBy, goToPage, createRow, updateRow, deleteRow, deleteRows, updateRows,
  addColumn, dropColumn, rowKey,
} = useAdminTable()

const activePlugin = computed(() => resolveTablePlugin(currentTable.value))
const activeTableMeta = computed(() => tableMeta.value[currentTable.value] || {})
const tableReadOnly = computed(() =>
  Boolean(activePlugin.value?.readOnly || activeTableMeta.value.read_only),
)
const tableReadOnlyHint = computed(() => {
  if (activePlugin.value?.readOnly) {
    return '请用「快捷添加 / 批量分类」维护映射'
  }
  return activeTableMeta.value.read_only_reason || '仅供查看'
})
const sidebarGroups = computed(() => {
  if (tableGroups.value.length) return tableGroups.value
  return [{ key: 'all', label: '数据表', tables: tables.value }]
})

function tableBadge(table) {
  const meta = tableMeta.value[table]
  if (!meta) return ''
  if (meta.route) return '入口'
  return meta.read_only ? '只读' : ''
}

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

// 删除确认必须能核对"删的是哪一条"：以前只写 rowid（数据库主键），既不是人认得出的
// 标识，又完全没说不可撤销。（见 utils/adminRowSummary.js 里为什么按列挑要点。）
const deleteRowMessage = computed(() => {
  const row = confirmDeleteRow.value
  if (!row) return ''
  const summary = describeRow(row)
  const target = summary ? `这条记录（${summary}）` : '这条记录'
  return `确认删除${target}？删除后无法撤销，也无法恢复。`
})

// 当前排序列的说明。两种情况下表头上看不到排序状态：默认排序用的是业务时间列
// （`order_time` 等内部列不进表头），或者表宽到表头被滚出视野——那时"这张表是按什么
// 排的"就完全不可见了。这条常驻说明补上那个缺口。
const sortLabel = computed(() => {
  if (!sortField.value) return ''
  return getColumnLabel(currentTable.value, sortField.value)
})
const sortDirectionLabel = computed(() => (sortDir.value === 'asc' ? '升序' : '降序'))

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
  const meta = tableMeta.value[t]
  if (meta?.route) {
    closeSidebar()
    router.push(meta.route)
    return
  }
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

// 窄屏下表格变成卡片（列名从 CSS 的 ::before 出来），行与行的分界也随之消失，
// 读屏只念一串没有归属的值。给每一行一句可读的名字，让卡片和读屏都知道"这是哪一条"。
function rowLabel(row) {
  const summary = describeRow(row, { max: 2 })
  return summary ? `第 ${rowKey(row)} 行：${summary}` : `第 ${rowKey(row)} 行`
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
          v-for="group in sidebarGroups"
          :key="group.key"
          class="dt-table-group"
        >
          <div class="dt-table-group-label">{{ group.label }}</div>
          <div
            v-for="t in group.tables"
            :key="t"
            class="dt-table-item"
            :class="{ active: t === currentTable }"
            :title="tableMeta[t]?.read_only_reason || getTableLabel(t)"
            @click="selectTable(t)"
          >
            <TableIcon :name="getTableIcon(t)" :size="15" />
            <span class="dt-table-name">{{ getTableLabel(t) }}</span>
            <span v-if="tableBadge(t)" class="dt-table-badge">{{ tableBadge(t) }}</span>
          </div>
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
          v-if="sortLabel"
          type="button"
          class="dt-sort-state"
          :title="`当前排序：${sortLabel} ${sortDirectionLabel}（原字段 ${sortField}）。点一下切换升降序。`"
          @click="sortBy(sortField)"
        >排序：{{ sortLabel }} {{ sortDir === 'asc' ? '▲' : '▼' }}</button>
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
        <span v-else-if="tableReadOnly" class="dt-batch-hint">只读表 · {{ tableReadOnlyHint }}</span>
        <component
          :is="activePlugin.Extras"
          v-if="activePlugin?.Extras"
          :flash-toast="flashToast"
          :reload="loadRows"
          :after-quick-add="activePlugin.afterQuickAdd"
        />
        <button class="btn" style="margin-left:auto" @click="showDataQualityModal = true"><SvgIcon name="bar-chart" :size="13" /> 数据质量</button>
        <button class="btn" @click="router.push('/settings?section=backup')"><SvgIcon name="database" :size="13" /> 备份 / 迁移</button>
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
              <th class="dt-col-key">序号</th>
              <th
                v-for="col in columns"
                :key="col"
                class="dt-col-sortable"
                :class="{ 'is-sorted': sortField === col }"
                :title="`按「${getColumnLabel(currentTable, col)}」排序（原字段 ${col}）`"
                :aria-sort="sortField !== col ? 'none' : (sortDir === 'asc' ? 'ascending' : 'descending')"
                @click="sortBy(col)"
              >
                {{ getColumnLabel(currentTable, col) }}
                <!-- 常显的排序箭头：只在当前排序列上画 ▲▼ 时，另外 26 个头看起来
                     和纯文本表头一模一样——这一页此前就被判定为"没有任何排序入口"。
                     未排序列给一个低对比度的双箭头，鼠标悬停时才提亮。 -->
                <span
                  class="dt-sort-mark"
                  :class="{ 'is-active': sortField === col }"
                  aria-hidden="true"
                >{{ sortField === col ? (sortDir === 'asc' ? '▲' : '▼') : '⇅' }}</span>
              </th>
              <th v-if="!tableReadOnly" class="dt-col-actions">操作</th>
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
              :aria-label="rowLabel(row)"
              @click="tableReadOnly ? undefined : toggleRowSelection(rowKey(row))"
            >
              <td class="dt-col-key" style="color:var(--text-dim)">{{ rowKey(row) }}</td>
              <!-- data-col 是窄屏卡片布局的字段名来源（见样式里的 ::before）。 -->
              <td
                v-for="col in columns"
                :key="col"
                :data-col="getColumnLabel(currentTable, col)"
                :title="formatCellText(col, row[col])"
              >
                <span v-if="row[col] === null || row[col] === undefined" style="color:var(--text-dim)">NULL</span>
                <span v-else-if="row[col] === ''" style="color:var(--text-dim)">—</span>
                <template v-else>{{ formatCellText(col, row[col]) }}</template>
              </td>
              <td v-if="!tableReadOnly" class="actions dt-col-actions" @click.stop>
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
      :message="`确认删除已选的 ${selectedCount} 条记录？删除后无法撤销，也无法恢复。`"
      confirm-label="确认批量删除"
      danger
      @confirm="confirmBatchDelete"
      @cancel="showBatchDeleteConfirm = false"
    />

    <ConfirmDialog
      v-if="confirmDeleteRow"
      title="删除确认"
      :message="deleteRowMessage"
      confirm-label="确认删除"
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
.dt-table-group + .dt-table-group {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}
.dt-table-group-label {
  padding: 2px 9px 6px;
  font-size: 10px;
  font-weight: 600;
  color: var(--text-dim);
  letter-spacing: 0.04em;
}
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
.dt-table-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dt-table-badge {
  flex-shrink: 0;
  padding: 1px 4px;
  border: 1px solid currentColor;
  border-radius: 4px;
  font-size: 9px;
  line-height: 1.35;
  opacity: .72;
}

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

/* ===== A3：27 列的表在 1440 下也要横滚才能碰到「操作」列（容器 1206px / 内容 2595px），
   390 下更要滚 2227px，滚过去以后行标识（流水号 / 菜品名）又全在屏幕外 —— 想删一行
   得先记住序号、滚到底、按序号猜是哪一行。两处一起收：
   - 桌面 / 平板：序号列钉在左边、操作列钉在右边，中间那些列怎么滚，行标识与操作
     始终在视野里；
   - 窄屏（≤700px）：连"横向滚动"这个交互本身都不成立（一屏只够看一两列），改成
     一行一张卡片、字段名在值前面，编辑 / 删除落在每张卡底部。 */
@media (min-width: 701px) {
  /* sticky 的 th/td 需要不透明底色，否则横向滚动时下面的列会从它底下透出来。
     th 的底色跟 theme.css 的 var(--card2) 对齐，td 用卡片底色。 */
  :deep(table.data-table) .dt-col-key,
  :deep(table.data-table) .dt-col-actions {
    position: sticky;
    background: var(--card);
    z-index: 2;
  }
  :deep(table.data-table) thead .dt-col-key,
  :deep(table.data-table) thead .dt-col-actions {
    background: var(--card2);
    /* 表头本身也是 sticky 的（theme.css），钉住的列要在它之上才能盖住滚过来的列 */
    z-index: 3;
  }
  /* 宽度原来写在模板的 inline style 上，挪到这里是因为钉住的列必须能算准偏移 */
  :deep(table.data-table) .dt-col-key { left: 0; width: 60px; }
  :deep(table.data-table) .dt-col-actions {
    right: 0;
    /* 操作列本身是 flex（theme.css 的 td.actions），sticky 后仍要排一行 */
    display: flex;
    gap: 6px;
  }
  /* 钉住的列不许折行：折行会让行高在滚动时跳动 */
  :deep(table.data-table) .dt-col-key,
  :deep(table.data-table) .dt-col-actions { white-space: nowrap; }
  /* 钉住的一侧加一道阴影，表达"这一列浮在内容之上"，而不是内容到此为止 */
  :deep(table.data-table) .dt-col-key { box-shadow: 6px 0 8px -6px rgba(0, 0, 0, 0.55); }
  :deep(table.data-table) .dt-col-actions { box-shadow: -6px 0 8px -6px rgba(0, 0, 0, 0.55); }
}

/* 工具栏里的排序状态：表头滚出视野、或默认排序用的列不在表头里时，
   "这张表按什么排的"必须还有地方说。 */
.dt-sort-state {
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--card2);
  color: var(--text-dim);
  font-size: 11px;
  font-family: inherit;
  line-height: 1.5;
  cursor: pointer;
}
.dt-sort-state:hover { color: var(--text); border-color: var(--accent); }

/* 排序箭头：未排序列也给一个低对比度的 ⇅，让"这个表头可以点"这件事看得出来。
   此前只有当前排序列画 ▲▼，其余 26 个头与纯文本表头毫无区别。 */
:deep(table.data-table) th.dt-col-sortable .dt-sort-mark {
  margin-left: 4px;
  font-size: 9px;
  opacity: 0.45;
}
:deep(table.data-table) th.dt-col-sortable:hover .dt-sort-mark { opacity: 0.85; }
:deep(table.data-table) th.dt-col-sortable.is-sorted .dt-sort-mark {
  opacity: 1;
  color: var(--accent);
}

/* 窄屏卡片：一行一张卡 —— 列名走 data-col，值排在后面。
   这里一律用 `:deep(table.data-table ...)` 写法，是为了让每条规则的优先级都是
   (0, 4, 1) 同级、靠源码顺序生效：theme.css 里 `table.data-table td.actions { display:flex }`
   这类规则若是同级就会跟下来打架，而 `td.dt-col-actions` 单独写又只有 (0,3,1)、
   压不住桌面那段 `display: flex`。 */
@media (max-width: 700px) {
  :deep(table.data-table) { display: block; }
  :deep(table.data-table thead) { display: none; }
  :deep(table.data-table tbody) { display: block; }
  :deep(table.data-table tr) {
    display: block;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }
  :deep(table.data-table tr:last-child) { border-bottom: none; }
  /* 序号做卡片标题：它是核对时唯一能对上表格的值，也是行标识。 */
  :deep(table.data-table td.dt-col-key) {
    display: block;
    padding: 0 0 6px;
    border-bottom: 1px dashed var(--border);
    margin-bottom: 6px;
    font-weight: 700;
  }
  :deep(table.data-table td.dt-col-key)::before {
    content: '序号 ';
    font-weight: 400;
  }
  :deep(table.data-table td) {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 2px 0;
    border-bottom: none;
    /* 桌面档用 nowrap 让长值撑开横向滚动；卡片里必须允许折行 */
    white-space: normal;
    overflow-wrap: anywhere;
  }
  :deep(table.data-table td[data-col])::before {
    content: attr(data-col);
    flex: 0 0 74px;
    color: var(--text-dim);
    font-size: 11px;
  }
  /* 操作列单独一行放底部，宽度占满，符合手指点击的落点习惯。 */
  :deep(table.data-table td.dt-col-actions) {
    display: flex;
    gap: 8px;
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px dashed var(--border);
  }
  :deep(table.data-table td.dt-col-actions .btn) { flex: 1 1 0; justify-content: center; }
}
</style>
