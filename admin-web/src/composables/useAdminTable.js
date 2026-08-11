import { computed, ref, watch } from 'vue'
import { api } from '../api/client'

export function useAdminTable() {
  const tables = ref([])
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

  async function switchTable(table) {
    currentTable.value = table
    page.value = 1
    searchField.value = ''
    searchValue.value = ''
    sortField.value = ''
    await loadSchema()
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
    tables, currentTable, schema, columns, rows, total, page, pageSize, pages,
    sortField, sortDir, searchField, searchValue, loading, error,
    loadTables, loadRows, switchTable, sortBy, goToPage,
    createRow, updateRow, deleteRow, deleteRows, updateRows, addColumn, dropColumn,
    rowKey,
  }
}
