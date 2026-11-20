// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { shallowMount } from '@vue/test-utils'

// DataTable 是 27 列 / 20 万行那张表的渲染面。这里固定三件"看着能跑、用户却看不见"
// 的契约（都来自 UI 审查，改动理由见组件里的注释）：
//   A4  每个数据列都必须有排序标记 —— 只在当前排序列画 ▲▼ 时，其余 26 个头与纯文本
//       表头毫无区别，"这张表能排序"这件事本身看不见；
//   A3  序号列与操作列带钉住用的类名（样式靠它 sticky），否则 1440 下「操作」也在屏幕外；
//   A9  删除确认要说清"删的是哪一条"且写明不可撤销，而不是只报一个 rowid。

// `useAdminTable` 被整体替身：这里要验的是渲染契约，不是取数逻辑。
const tableState = {
  tables: ref([{ name: 'orders' }]),
  tableGroups: ref([]),
  tableMeta: ref({ orders: {} }),
  currentTable: ref('orders'),
  schema: ref([]),
  columns: ref(['dish_name', 'table_number', 'quantity']),
  rows: ref([]),
  total: ref(0),
  page: ref(1),
  pageSize: ref(50),
  pages: ref(1),
  sortField: ref(''),
  sortDir: ref('desc'),
  searchField: ref(''),
  searchValue: ref(''),
  loading: ref(false),
  error: ref(''),
  loadTables: vi.fn(),
  loadRows: vi.fn(),
  switchTable: vi.fn(),
  sortBy: vi.fn(),
  goToPage: vi.fn(),
  createRow: vi.fn(),
  updateRow: vi.fn(),
  deleteRow: vi.fn(),
  deleteRows: vi.fn(),
  updateRows: vi.fn(),
  addColumn: vi.fn(),
  dropColumn: vi.fn(),
  rowKey: (row) => row?.rowid,
}

vi.mock('../../../composables/useAdminTable', () => ({ useAdminTable: () => tableState }))
vi.mock('../../../composables/useNudgePull', () => ({ useNudgePull: () => {} }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

const { default: DataTable } = await import('../DataTable.vue')

const SAMPLE_ROW = {
  rowid: 7,
  dish_name: '宫保鸡丁',
  table_number: 'A12',
  quantity: 2,
  created_at: '2026-10-05T12:03:44+08:00',
}

// jsdom 没有 matchMedia，而 DataTable 在 onMounted 里用它监听移动端抽屉断点。
if (typeof window.matchMedia !== 'function') {
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
}

function mountTable({
  rows = [SAMPLE_ROW],
  columns = ['dish_name', 'table_number', 'quantity'],
  sortField = '',
  readOnly = false,
} = {}) {
  tableState.rows.value = rows
  tableState.columns.value = columns
  tableState.sortField.value = sortField
  tableState.tableMeta.value = { orders: readOnly ? { read_only: true } : {} }
  return shallowMount(DataTable, {
    global: {
      stubs: {
        SvgIcon: true,
        TableIcon: true,
        RowEditModal: true,
        ColumnManageModal: true,
        DataQualityPanel: true,
        BatchEditModal: true,
        LuyunNumberInput: true,
      },
    },
  })
}

describe('DataTable 排序入口（A4）', () => {
  it('每个数据列表头都带排序标记，未排序列显示 ⇅', () => {
    const headers = mountTable().findAll('.dt-col-sortable')

    expect(headers).toHaveLength(3)
    for (const header of headers) {
      expect(header.find('.dt-sort-mark').text()).toBe('⇅')
    }
    expect(headers.some((h) => h.classes().includes('is-sorted'))).toBe(false)
  })

  it('当前排序列显示方向箭头并标 is-sorted（表头状态读得出来）', () => {
    const headers = mountTable({
      columns: ['created_at', 'dish_name'],
      sortField: 'created_at',
    }).findAll('.dt-col-sortable')
    const active = headers.find((h) => h.classes().includes('is-sorted'))

    expect(active).toBeTruthy()
    // sortDir 是 desc
    expect(active.find('.dt-sort-mark').text()).toBe('▼')
    expect(active.attributes('aria-sort')).toBe('descending')
    expect(headers.find((h) => !h.classes().includes('is-sorted')).attributes('aria-sort')).toBe('none')
  })

  it('序号列不是可排序列（它只是行标识，不是业务字段）', () => {
    const keyTh = mountTable().find('th.dt-col-key')

    expect(keyTh.exists()).toBe(true)
    expect(keyTh.classes()).not.toContain('dt-col-sortable')
  })

  it('点表头按该列排序', async () => {
    const wrapper = mountTable()
    tableState.sortBy.mockClear()

    await wrapper.findAll('.dt-col-sortable')[1].trigger('click')
    expect(tableState.sortBy).toHaveBeenCalledWith('table_number')
  })

  // 默认排序用的是业务时间列（`order_time` 这类内部列不进表头），表头被横向滚出视野
  // 时也一样 —— 两处都看不到排序状态。工具栏上这条常驻说明补的就是这个缺口。
  it('工具栏常驻显示当前排序，点了能换向', async () => {
    const wrapper = mountTable({ sortField: 'order_time' })
    const chip = wrapper.find('.dt-sort-state')

    expect(chip.exists()).toBe(true)
    expect(chip.text()).toContain('下单时间')
    expect(chip.text()).toContain('▼')

    tableState.sortBy.mockClear()
    await chip.trigger('click')
    expect(tableState.sortBy).toHaveBeenCalledWith('order_time')
  })

  it('没有排序时不显示这条说明（不占工具栏的宽度）', () => {
    expect(mountTable({ sortField: '' }).find('.dt-sort-state').exists()).toBe(false)
  })
})

describe('DataTable 钉住的列与窄屏卡片（A3）', () => {
  it('序号列与操作列带 sticky 用的类名，操作列不随横向滚动离开视野', () => {
    const row = mountTable().find('tbody tr')

    expect(row.find('td.dt-col-key').exists()).toBe(true)
    expect(row.find('td.dt-col-actions').exists()).toBe(true)
    // 每个数据格都带字段名，窄屏卡片的文案从它来
    expect(row.find('td[data-col="菜品名称"]').exists()).toBe(true)
    expect(row.find('td[data-col="桌号"]').exists()).toBe(true)
  })

  it('只读表不渲染操作列（没有可点的编辑 / 删除）', () => {
    expect(mountTable({ readOnly: true }).find('td.dt-col-actions').exists()).toBe(false)
  })

  it('每行带一句可读的行标识，卡片与读屏不会只剩一串无归属的值', () => {
    const label = mountTable().find('tbody tr').attributes('aria-label')

    expect(label).toContain('第 7 行')
    expect(label).toContain('宫保鸡丁')
  })
})

describe('DataTable 删除确认（A9）', () => {
  async function openDeleteConfirm() {
    const wrapper = mountTable()
    const deleteBtn = wrapper
      .findAll('td.dt-col-actions button')
      .find((btn) => btn.text().includes('删除'))
    expect(deleteBtn).toBeTruthy()
    await deleteBtn.trigger('click')
    return wrapper
  }

  it('说清删的是哪一条、写明不可撤销，按钮不再与触发按钮同名', async () => {
    const dialog = (await openDeleteConfirm()).findComponent({ name: 'ConfirmDialog' })

    expect(dialog.exists()).toBe(true)
    const message = dialog.props('message')
    expect(message).toContain('宫保鸡丁')
    expect(message).toContain('桌号 A12')
    expect(message).toContain('序号 7')
    expect(message).toContain('无法撤销')
    // 触发按钮叫「删除」，确认按钮必须换个名字，否则用户分不清自己点的是哪一个
    expect(dialog.props('confirmLabel')).toBe('确认删除')
    expect(dialog.props('danger')).toBe(true)
  })

  it('确认后才真的删，取消则什么都不发生', async () => {
    tableState.deleteRow.mockClear()
    const wrapper = await openDeleteConfirm()

    await wrapper.findComponent({ name: 'ConfirmDialog' }).vm.$emit('cancel')
    expect(tableState.deleteRow).not.toHaveBeenCalled()

    await wrapper
      .findAll('td.dt-col-actions button')
      .find((btn) => btn.text().includes('删除'))
      .trigger('click')
    await wrapper.findComponent({ name: 'ConfirmDialog' }).vm.$emit('confirm')
    expect(tableState.deleteRow).toHaveBeenCalledWith(7)
  })
})

// 真机截图（2026-11-20）里的四个问题：时间列显示原始 ISO 串、tenant_id 占一整列全是 1、
// 流水号长串把列撑到约 400px、每行一个红框实心「删除」。
describe('DataTable 时间列格式化', () => {
  it('ISO 时间显示为 YYYY-MM-DD HH:mm，title 也是格式化后的值', () => {
    const wrapper = mountTable({
      columns: ['order_time'],
      rows: [{ rowid: 9, order_time: '2026-11-20T16:00:00+08:00' }],
    })
    const cell = wrapper.find('td[data-col="下单时间"]')

    expect(cell.text()).toBe('2026-11-20 16:00')
    expect(cell.attributes('title')).toBe('2026-11-20 16:00')
    expect(cell.text()).not.toContain('T')
  })

  it('非时间列原样显示，不误伤普通文本', () => {
    const cell = mountTable().find('td[data-col="菜品名称"]')

    expect(cell.text()).toBe('宫保鸡丁')
  })
})

describe('DataTable 冗余的租户ID列', () => {
  const rows = [
    { rowid: 1, tenant_id: 1, dish_name: '宫保鸡丁' },
    { rowid: 2, tenant_id: 1, dish_name: '鱼香肉丝' },
  ]

  it('当前页该列全为同一个值时，表头与单元格都不渲染', () => {
    const wrapper = mountTable({ columns: ['tenant_id', 'dish_name'], rows })

    expect(wrapper.findAll('th.dt-col-sortable')).toHaveLength(1)
    expect(wrapper.findAll('td[data-col="租户ID"]')).toHaveLength(0)
    expect(wrapper.find('td[data-col="菜品名称"]').exists()).toBe(true)
  })

  it('出现多个租户时照常显示这一列', () => {
    const wrapper = mountTable({
      columns: ['tenant_id', 'dish_name'],
      rows: [rows[0], { rowid: 2, tenant_id: 2, dish_name: '鱼香肉丝' }],
    })

    expect(wrapper.findAll('td[data-col="租户ID"]')).toHaveLength(2)
  })
})

describe('DataTable 流水号长串', () => {
  it('单行省略，完整值放在单元格 title 里', () => {
    const serial = 'YY001301-261120-0202_乌榄角酱香捞面_002'
    const wrapper = mountTable({
      columns: ['business_flow_id'],
      rows: [{ rowid: 11, business_flow_id: serial }],
    })
    const cell = wrapper.find('td[data-col="流水号"]')
    const span = cell.find('span.dt-cell-ellipsis')

    expect(span.exists()).toBe(true)
    expect(span.text()).toBe(serial)
    expect(cell.attributes('title')).toBe(serial)
  })
})

describe('DataTable 行内删除按钮', () => {
  it('降为文字按钮：保留危险色，但不带实心红框的 btn-danger', () => {
    const table = mountTable()
    const deleteBtn = table
      .findAll('td.dt-col-actions button')
      .find((btn) => btn.text().includes('删除'))

    expect(deleteBtn.classes()).toContain('dt-delete-btn')
    expect(deleteBtn.classes()).not.toContain('btn-danger')
  })
})

// 「全选本页」原来没有对应的复选框（多选状态本来就有，只是没渲染）。现在补上表头与
// 每行的勾选框，并保持只读表没有选择列。
describe('DataTable 全选本页复选框', () => {
  it('表头勾选框联动选中本页所有行', async () => {
    const wrapper = mountTable({
      rows: [
        { rowid: 1, dish_name: '宫保鸡丁' },
        { rowid: 2, dish_name: '鱼香肉丝' },
      ],
    })
    const headerBox = wrapper.find('thead input.dt-checkbox')

    expect(headerBox.exists()).toBe(true)
    headerBox.element.checked = true
    await headerBox.trigger('change')

    expect(wrapper.findAll('tbody tr').every((row) => row.classes().includes('dt-row-selected'))).toBe(true)
    expect(wrapper.find('thead input.dt-checkbox').element.checked).toBe(true)
  })

  it('点行首勾选框只切换那一行，并显示已选条数', async () => {
    const wrapper = mountTable({
      rows: [
        { rowid: 1, dish_name: '宫保鸡丁' },
        { rowid: 2, dish_name: '鱼香肉丝' },
      ],
    })
    const rowBox = wrapper.findAll('tbody input.dt-checkbox')[0]

    rowBox.element.checked = true
    await rowBox.trigger('change')

    const rows = wrapper.findAll('tbody tr')
    expect(rows[0].classes()).toContain('dt-row-selected')
    expect(rows[1].classes()).not.toContain('dt-row-selected')
    expect(wrapper.text()).toContain('已选 1 条')
  })

  it('只读表没有选择列，也没有表头勾选框', () => {
    const wrapper = mountTable({ readOnly: true })

    expect(wrapper.find('thead input.dt-checkbox').exists()).toBe(false)
    expect(wrapper.find('td.dt-col-select').exists()).toBe(false)
    expect(wrapper.find('table.dt-has-select').exists()).toBe(false)
  })
})
