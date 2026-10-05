import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
const apiPost = vi.fn()
const apiPut = vi.fn()
const apiDelete = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
    put: (...args) => apiPut(...args),
    delete: (...args) => apiDelete(...args),
  },
}))

const { useAdminTable } = await import('../useAdminTable.js')

function schema(names) {
  return { columns: names.map((name) => ({ name, type: 'text' })) }
}

// 20 万行的表进来时没有任何排序：`orders` 实测物理顺序是 7,8,9,10,13,15,…,2,25,27,11,4
// —— 既不是插入序也不是时间序，用户看不出自己在看什么，想找刚发生的事得翻 4189 页。
// 后端 `/rows` 早就支持 sort_field / sort_dir，前端一直没给默认值。
describe('useAdminTable 的默认排序', () => {
  beforeEach(() => {
    apiGet.mockReset()
  })

  async function prepare(tableColumns) {
    apiGet.mockImplementation(async (url) => {
      if (url === '/api/admin/tables') {
        return { tables: ['orders'], groups: [], table_meta: { orders: {} } }
      }
      if (url.endsWith('/schema')) return schema(tableColumns)
      return { rows: [], total: 0 }
    })
    const table = useAdminTable()
    await table.loadTables()
    return table
  }

  it('有 order_time 的表默认按它倒序（最新在前）', async () => {
    const table = await prepare(['id', 'dish_name', 'order_time', 'created_at'])
    await table.switchTable('orders')

    expect(table.sortField.value).toBe('order_time')
    expect(table.sortDir.value).toBe('desc')
    const rowsCall = apiGet.mock.calls.find(([url]) => url.endsWith('/rows'))
    expect(rowsCall[1]).toMatchObject({ sort_field: 'order_time', sort_dir: 'desc' })
  })

  it('没有 order_time 时退回 created_at', async () => {
    const table = await prepare(['id', 'name', 'created_at'])
    await table.switchTable('orders')

    expect(table.sortField.value).toBe('created_at')
    expect(table.sortDir.value).toBe('desc')
  })

  it('两个时间列都没有就不排序（小字典表按主键来更自然）', async () => {
    const table = await prepare(['id', 'name', 'notes'])
    await table.switchTable('orders')

    expect(table.sortField.value).toBe('')
    const rowsCall = apiGet.mock.calls.find(([url]) => url.endsWith('/rows'))
    expect(rowsCall[1].sort_field).toBeUndefined()
  })

  it('点表头之后以用户选的列为准，切表才回到默认序', async () => {
    const table = await prepare(['id', 'dish_name', 'order_time'])
    await table.switchTable('orders')

    table.sortBy('dish_name')
    expect(table.sortField.value).toBe('dish_name')
    expect(table.sortDir.value).toBe('asc')

    await table.switchTable('orders')
    expect(table.sortField.value).toBe('order_time')
    expect(table.sortDir.value).toBe('desc')
  })

  it('排序字段跟着表头点击换向（同一列再点一次反向）', async () => {
    const table = await prepare(['id', 'dish_name', 'order_time'])
    await table.switchTable('orders')

    table.sortBy('order_time')
    expect(table.sortDir.value).toBe('asc')
    table.sortBy('order_time')
    expect(table.sortDir.value).toBe('desc')
  })
})
