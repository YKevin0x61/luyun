import { computed, ref, watch } from 'vue'
import { api } from '../api/client'

export function useAdminTable() {
  const tables = ref([])
  const tableGroups = ref([])
  const tableMeta = ref({})
  const currentTable = ref('')
  const schema = ref([])
  const rows = ref([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(50)
  const sortField = ref('')
  const sortDir = ref('asc')
  const searchField = ref('')
  const searchValue = ref('')
  const loading = ref(false)
  const error = ref('')

  // 内部列：orders 等表的 id 为自增主键、rowid 为 SQLite 内部行号，均不作为业务数据列展示。
  const INTERNAL_COLUMNS = new Set(['id', 'rowid'])

  const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
  const columns = computed(() =>
    schema.value.map((c) => c.name).filter((c) => !INTERNAL_COLUMNS.has(c.toLowerCase())),
  )

  async function loadTables() {
    try {
      const res = await api.get('/api/admin/tables')
      tables.value = res.tables || []
      tableGroups.value = Array.isArray(res.groups) ? res.groups : []
      tableMeta.value = res.table_meta || {}
      if (!currentTable.value && tables.value.length) {
        currentTable.value = tables.value.includes('orders') ? 'orders' : tables.value[0]
      }
    } catch (e) {
      error.value = e.message || '加载表列表失败'
    }
  }

  async function loadSchema() {
    if (!currentTable.value) return
    try {
      const res = await api.get(`/api/admin/tables/${currentTable.value}/schema`)
      schema.value = res.columns || []
    } catch (e) {
      error.value = e.message || '加载表结构失败'
      schema.value = []
    }
  }

  async function loadRows() {
    if (!currentTable.value) return
    loading.value = true
    error.value = ''
    try {
      const res = await api.get(`/api/admin/tables/${currentTable.value}/rows`, {
        page: page.value,
        page_size: pageSize.value,
        search_field: searchField.value || undefined,
        search_value: searchValue.value || undefined,
        sort_field: sortField.value || undefined,
        sort_dir: sortDir.value,
      })
      rows.value = res.rows || []
      total.value = res.total || 0
    } catch (e) {
      error.value = e.message || '加载数据失败'
      rows.value = []
      total.value = 0
    } finally {
      loading.value = false
    }
  }

  /**
   * 表的默认排序：**最新在前**，否则一进来是物理顺序（`orders` 实测是
   * 7,8,9,10,13,15,…,2,25,27,11,4）——既不是插入序也不是时间序，20 万行的表里
   * 用户根本看不出自己在看什么，想找刚发生的事得翻 4189 页。
   *
   * 优先用业务时间列，其次 created_at；两者都没有就不排（小字典表按主键来更自然）。
   * 只在切换表时套用，用户点过表头之后就以他选的为准。
   */
  const DEFAULT_SORT_COLUMNS = ['order_time', 'created_at']

  function defaultSortFor(columnNames) {
    const lower = columnNames.map((name) => String(name).toLowerCase())
    const field = DEFAULT_SORT_COLUMNS.find((name) => lower.includes(name))
    return field ? columnNames[lower.indexOf(field)] : ''
  }

  async function switchTable(table) {
    currentTable.value = table
    page.value = 1
    searchField.value = ''
    searchValue.value = ''
    await loadSchema()
    // schema 到手才知道有没有时间列，所以默认排序在 loadSchema 之后定。
    sortField.value = defaultSortFor(columns.value)
    sortDir.value = 'desc'
    await loadRows()
  }

  function sortBy(field) {
    if (sortField.value === field) {
      sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
    } else {
      sortField.value = field
      sortDir.value = 'asc'
    }
    loadRows()
  }

  function goToPage(p) {
    if (p < 1 || p > pages.value) return
    page.value = p
    loadRows()
  }

  async function createRow(values) {
    await api.post(`/api/admin/tables/${currentTable.value}/rows`, { values })
    await loadRows()
  }

  async function updateRow(rowId, values) {
    await api.put(`/api/admin/tables/${currentTable.value}/rows/${rowId}`, { values })
    await loadRows()
  }

  async function deleteRow(rowId) {
    await api.delete(`/api/admin/tables/${currentTable.value}/rows/${rowId}`)
    await loadRows()
  }

  async function deleteRows(rowIds) {
    const res = await api.post(
      `/api/admin/tables/${currentTable.value}/rows/batch-delete`,
      { row_ids: rowIds },
    )
    await loadRows()
    return res
  }

  async function updateRows(rowIds, column, value) {
    const res = await api.post(
      `/api/admin/tables/${currentTable.value}/rows/batch-update`,
      { row_ids: rowIds, column, value },
    )
    await loadRows()
    return res
  }

  async function addColumn(payload) {
    await api.post(`/api/admin/tables/${currentTable.value}/columns`, payload)
    await loadSchema()
    await loadRows()
  }

  async function dropColumn(columnName) {
    await api.delete(`/api/admin/tables/${currentTable.value}/columns/${columnName}`)
    await loadSchema()
    await loadRows()
  }

  /** Stable row identity for Admin CRUD (SQLite rowid). */
  function rowKey(row) {
    return row == null ? undefined : row.rowid
  }

  return {
    tables, tableGroups, tableMeta, currentTable, schema, columns, rows, total, page, pageSize, pages,
    sortField, sortDir, searchField, searchValue, loading, error,
    loadTables, loadRows, switchTable, sortBy, goToPage,
    createRow, updateRow, deleteRow, deleteRows, updateRows, addColumn, dropColumn,
    rowKey,
  }
}
