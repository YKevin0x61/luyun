<script setup>
/**
 * 厨房采购 · 商品与供应商。
 *
 * 四块目录：商品、供应商与分类、岗位、日期标签。只有管理端能进这一页，
 * 服务端也会再判一次（员工拿到 403）。
 *
 * 编码规则：供应商 2 位 + 分类 2 位 + 序号 3 位。新建可只填序号，也可以
 * 完全留空让服务端取下一序号；编辑时只要没手动改编码、又换了供应商或分类，
 * 就提交空 product_code，让服务端重算并查重（见 update_product）。
 *
 * 目录没有物理删除接口，统一用 active 启停；被商品占用的岗位更不能删。
 */
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client.js'
import { formatMonthDay, pad2, todayText, weekdayText } from './purchaseUi.js'
import { PURCHASE_MIGRATION_PATH } from '../../utils/purchasePaths.js'
import PurchaseNav from './PurchaseNav.vue'

const router = useRouter()

const TABS = [
  { key: 'products', label: '商品管理' },
  { key: 'suppliers', label: '供应商管理' },
  { key: 'categories', label: '分类管理' },
  { key: 'tags', label: '日期标签' },
]
const TAG_VALUES = ['2万', '3万', '4万', '5万', '6万', '7万', '8万']
const CALENDAR_HEAD = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const WEEKDAY_KEYS = [0, 1, 2, 3, 4, 5, 6]
const PAGE_SIZE = 10
const TODAY = todayText()

const loading = ref(true)
const errorText = ref('')
const notice = ref('')
const activeTab = ref('products')

const meta = ref({ positions: [], suppliers: [], categories: [], units: [], tags: [] })
const products = ref([])
const tagData = ref({ date_tags: [], weekday_defaults: {} })
const tagsLoading = ref(false)

const keyword = ref('')
const supplierFilter = ref(0)
const positionFilter = ref(0)
const categorySupplierFilter = ref(0)
const page = ref(1)

const dialogKind = ref('')
const dialogError = ref('')
const dialogSaving = ref(false)

const productBusy = ref(0)
const supplierBusy = ref(0)
const categoryBusy = ref(0)
const positionBusy = ref(0)

const productForm = reactive({
  id: 0,
  supplier_id: 0,
  category_id: 0,
  seq: '',
  seq_touched: false,
  code_manual: false,
  manual_code: '',
  name: '',
  specification: '',
  unit: '',
  remark: '',
  order_step: 1,
  safety_factor: 1.2,
  need_stock_check: false,
  is_long_cycle: false,
  active: true,
  position_ids: [],
  original_supplier_id: 0,
  original_category_id: 0,
})

const supplierForm = reactive({ id: 0, name: '', code: '', active: true })
const categoryForm = reactive({ id: 0, supplier_id: 0, supplier_name: '', name: '', code: '', active: true })
const positionForm = reactive({ id: 0, name: '', sort_order: 0, show_in_order: true, active: true })

// Excel：先 dry_run 预览再确认写入，失败行只汇报、不阻断其他行。
const importDialog = ref(false)
const importFileInput = ref(null)
const importFile = ref(null)
const importMode = ref('upsert')
const importPreview = ref(null)
const importLoading = ref(false)
const importApplying = ref(false)
const importError = ref('')

const monthCursor = ref(TODAY.slice(0, 7))
const selectedDates = ref([])
const bulkTag = ref('6万')
const tagBusy = ref(false)
const clearArmed = ref(false)
let clearTimer = 0

function pad3(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  return String(Math.max(0, Math.min(999, Math.trunc(number)))).padStart(3, '0')
}

function dateText(date) {
  return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate())
}

function dateWeekday(value) {
  const date = new Date(String(value) + 'T00:00:00')
  return Number.isNaN(date.getTime()) ? 0 : date.getDay()
}

const suppliers = computed(() => meta.value.suppliers || [])
const categories = computed(() => meta.value.categories || [])

function supplierById(id) {
  return suppliers.value.find((row) => Number(row.id) === Number(id)) || null
}

function categoryFullCode(row) {
  const supplier = supplierById(row && row.supplier_id)
  return (supplier ? pad2(supplier.code) : '--') + pad2(row && row.code)
}

const filteredCategories = computed(() =>
  categories.value.filter(
    (row) => !categorySupplierFilter.value || Number(row.supplier_id) === Number(categorySupplierFilter.value),
  ),
)

const positionsSorted = computed(() =>
  [...(meta.value.positions || [])].sort(
    (left, right) =>
      (Number(left.sort_order) || 0) - (Number(right.sort_order) || 0) ||
      (Number(left.id) || 0) - (Number(right.id) || 0),
  ),
)

const filteredProducts = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return products.value.filter((product) => {
    if (supplierFilter.value && Number(product.supplier_id) !== Number(supplierFilter.value)) return false
    if (positionFilter.value) {
      const ids = (product.positions || []).map((row) => Number(row.id))
      if (!ids.includes(Number(positionFilter.value))) return false
    }
    if (query) {
      const haystack = [
        product.product_code,
        product.name,
        product.supplier_name,
        product.category_name,
        product.specification,
        product.remark,
      ]
        .map((part) => String(part || '').toLowerCase())
        .join(' ')
      if (!haystack.includes(query)) return false
    }
    return true
  })
})

const pageCount = computed(() => Math.max(1, Math.ceil(filteredProducts.value.length / PAGE_SIZE)))
const currentPage = computed(() => Math.min(page.value, pageCount.value))
const pagedProducts = computed(() => {
  const start = (currentPage.value - 1) * PAGE_SIZE
  return filteredProducts.value.slice(start, start + PAGE_SIZE)
})

watch([keyword, supplierFilter, positionFilter], () => {
  page.value = 1
})
watch(pageCount, (count) => {
  if (page.value > count) page.value = count
})

const categoryProductCount = computed(() => {
  const counts = {}
  for (const product of products.value) {
    const key = Number(product.category_id)
    counts[key] = (counts[key] || 0) + 1
  }
  return counts
})

const positionUsage = computed(() => {
  const counts = {}
  for (const product of products.value) {
    for (const row of product.positions || []) {
      const key = Number(row.id)
      counts[key] = (counts[key] || 0) + 1
    }
  }
  return counts
})

function categoryProductCountOf(categoryId) {
  return categoryProductCount.value[Number(categoryId)] || 0
}

function positionUsageCount(positionId) {
  return positionUsage.value[Number(positionId)] || 0
}

function supplierCategories(supplierId) {
  return categories.value.filter((row) => Number(row.supplier_id) === Number(supplierId))
}

function clearProductFilters() {
  keyword.value = ''
  supplierFilter.value = 0
  positionFilter.value = 0
}

function selectableCategories(supplierId, keepId) {
  return categories.value.filter(
    (row) => Number(row.supplier_id) === Number(supplierId) && (row.active || Number(row.id) === Number(keepId)),
  )
}

const productCategoryOptions = computed(() =>
  selectableCategories(productForm.supplier_id, productForm.category_id),
)

const productPositionOptions = computed(() =>
  positionsSorted.value.filter(
    (row) => row.active || productForm.position_ids.map(Number).includes(Number(row.id)),
  ),
)

const productSupplier = computed(() =>
  suppliers.value.find((row) => Number(row.id) === Number(productForm.supplier_id)),
)

const productCategory = computed(() =>
  categories.value.find((row) => Number(row.id) === Number(productForm.category_id)),
)

const productNextSeq = computed(() => {
  const seq = Number(productCategory.value && productCategory.value.next_product_seq)
  return Number.isFinite(seq) && seq > 0 ? Math.min(seq, 999) : 1
})

const productSupplierCodeText = computed(() => (productSupplier.value ? pad2(productSupplier.value.code) : '--'))
const productCategoryCodeText = computed(() => (productCategory.value ? pad2(productCategory.value.code) : '--'))

function autoProductCode() {
  if (!productSupplier.value || !productCategory.value) return ''
  const raw = String(productForm.seq || '').trim()
  if (raw && !/^\d{1,3}$/.test(raw)) return ''
  const seq = raw ? Number(raw) : productNextSeq.value
  return pad2(productSupplier.value.code) + pad2(productCategory.value.code) + pad3(seq)
}

const productPreviewCode = computed(() =>
  productForm.code_manual ? String(productForm.manual_code || '').trim() : autoProductCode(),
)

const productPreviewHint = computed(() => {
  if (productForm.code_manual) return '手动编码按 7 位数字提交，不再跟随供应商/分类变化。'
  if (!productSupplier.value || !productCategory.value) return '选好供应商和分类后显示编码。'
  const raw = String(productForm.seq || '').trim()
  if (raw) return '供应商 ' + productSupplierCodeText.value + ' + 分类 ' + productCategoryCodeText.value + ' + 序号 ' + pad3(raw)
  return '留空按下一序号 ' + pad3(productNextSeq.value) + ' 自动生成'
})

function composeProductCode(supplierId, categoryId, seq) {
  const supplier = suppliers.value.find((row) => Number(row.id) === Number(supplierId))
  const category = categories.value.find((row) => Number(row.id) === Number(categoryId))
  if (!supplier || !category) return ''
  return pad2(supplier.code) + pad2(category.code) + pad3(seq)
}

const dialogTitle = computed(() => {
  const labels = { product: '商品', supplier: '供应商', category: '分类', position: '岗位' }
  const label = labels[dialogKind.value]
  if (!label) return ''
  const editing =
    dialogKind.value === 'product'
      ? productForm.id
      : dialogKind.value === 'supplier'
        ? supplierForm.id
        : dialogKind.value === 'category'
          ? categoryForm.id
          : positionForm.id
  return (editing ? '编辑' : '新建') + label
})

const monthLabel = computed(() => {
  const parts = monthCursor.value.split('-')
  return Number(parts[0]) + ' 年 ' + Number(parts[1]) + ' 月'
})

const weekdayDefaults = computed(() => tagData.value.weekday_defaults || {})

const tagMap = computed(() => {
  const map = {}
  for (const row of tagData.value.date_tags || []) {
    if (row && row.tag_date) map[row.tag_date] = row.tag
  }
  return map
})

const monthTags = computed(() =>
  [...(tagData.value.date_tags || [])]
    .filter((row) => row && row.tag_date)
    .sort((left, right) => String(left.tag_date).localeCompare(String(right.tag_date))),
)

const selectedSet = computed(() => new Set(selectedDates.value))

const monthCells = computed(() => {
  const parts = monthCursor.value.split('-')
  const year = Number(parts[0])
  const month = Number(parts[1])
  const first = new Date(year, month - 1, 1)
  const offset = (first.getDay() + 6) % 7
  const cells = []
  for (let index = 0; index < 42; index += 1) {
    const date = new Date(year, month - 1, 1 - offset + index)
    cells.push({
      date: dateText(date),
      day: date.getDate(),
      weekday: date.getDay(),
      inMonth: date.getMonth() === month - 1,
    })
  }
  return cells
})

function monthRange() {
  const parts = monthCursor.value.split('-')
  const year = Number(parts[0])
  const month = Number(parts[1])
  const last = new Date(year, month, 0).getDate()
  return [monthCursor.value + '-01', monthCursor.value + '-' + pad2(last)]
}

function monthDateList() {
  const parts = monthCursor.value.split('-')
  const year = Number(parts[0])
  const month = Number(parts[1])
  const last = new Date(year, month, 0).getDate()
  const list = []
  for (let day = 1; day <= last; day += 1) list.push(dateText(new Date(year, month - 1, day)))
  return list
}

function calendarCellLabel(cell) {
  if (!cell.inMonth) return cell.date
  const tag = tagMap.value[cell.date]
  const fallback = weekdayDefaults.value[cell.weekday]
  return (
    cell.date +
    ' ' +
    weekdayText(cell.weekday) +
    (tag ? '，标签 ' + tag : fallback ? '，星期默认 ' + fallback : '')
  )
}

function toggleDate(date) {
  const index = selectedDates.value.indexOf(date)
  if (index >= 0) selectedDates.value.splice(index, 1)
  else selectedDates.value = [...selectedDates.value, date].sort()
}

function clearSelection() {
  selectedDates.value = []
}

function goPage(delta) {
  page.value = Math.min(pageCount.value, Math.max(1, currentPage.value + delta))
}

async function loadAll() {
  loading.value = true
  errorText.value = ''
  try {
    const [metaData, productData] = await Promise.all([
      api.get('/api/purchase/meta'),
      api.get('/api/purchase/admin/products', { include_inactive: true }),
    ])
    meta.value = metaData || meta.value
    products.value = (productData && productData.products) || []
    await loadTags()
  } catch (err) {
    errorText.value = err.message || '目录没加载出来'
  } finally {
    loading.value = false
  }
}

async function loadTags() {
  tagsLoading.value = true
  try {
    const [start, end] = monthRange()
    const data = await api.get('/api/purchase/admin/date-tags', { start, end })
    tagData.value = {
      date_tags: (data && data.date_tags) || [],
      weekday_defaults: (data && data.weekday_defaults) || {},
    }
  } catch (err) {
    errorText.value = err.message || '日期标签没加载出来'
  } finally {
    tagsLoading.value = false
  }
}

async function shiftMonth(delta) {
  const parts = monthCursor.value.split('-')
  const year = Number(parts[0])
  const month = Number(parts[1])
  const date = new Date(year, month - 1 + delta, 1)
  monthCursor.value = date.getFullYear() + '-' + pad2(date.getMonth() + 1)
  clearSelection()
  clearArmed.value = false
  await loadTags()
}

async function goToday() {
  monthCursor.value = TODAY.slice(0, 7)
  clearSelection()
  clearArmed.value = false
  await loadTags()
}

function resetProductForm() {
  Object.assign(productForm, {
    id: 0,
    supplier_id: 0,
    category_id: 0,
    seq: '',
    seq_touched: false,
    code_manual: false,
    manual_code: '',
    name: '',
    specification: '',
    unit: '',
    remark: '',
    order_step: 1,
    safety_factor: 1.2,
    need_stock_check: false,
    is_long_cycle: false,
    active: true,
    position_ids: [],
    original_supplier_id: 0,
    original_category_id: 0,
  })
}

function openProductDialog(row) {
  dialogKind.value = 'product'
  dialogError.value = ''
  resetProductForm()
  if (row) {
    Object.assign(productForm, {
      id: Number(row.id),
      supplier_id: Number(row.supplier_id),
      category_id: Number(row.category_id),
      seq: String(row.product_code || '').slice(-3),
      seq_touched: false,
      code_manual: false,
      manual_code: '',
      name: row.name || '',
      specification: row.specification || '',
      unit: row.unit || '',
      remark: row.remark || '',
      order_step: Number(row.order_step) || 1,
      safety_factor: Number(row.safety_factor) || 1.2,
      need_stock_check: Boolean(row.need_stock_check),
      is_long_cycle: Boolean(row.is_long_cycle),
      active: Boolean(row.active),
      position_ids: (row.positions || []).map((item) => Number(item.id)).filter((id) => id > 0),
      original_supplier_id: Number(row.supplier_id),
      original_category_id: Number(row.category_id),
    })
    return
  }
  const supplier = suppliers.value.find((item) => item.active) || suppliers.value[0]
  productForm.supplier_id = supplier ? Number(supplier.id) : 0
  const category = selectableCategories(productForm.supplier_id, 0)[0]
  productForm.category_id = category ? Number(category.id) : 0
  const firstPosition = positionsSorted.value.find((item) => item.active)
  productForm.position_ids = firstPosition ? [Number(firstPosition.id)] : []
}

function openSupplierDialog(row) {
  dialogKind.value = 'supplier'
  dialogError.value = ''
  Object.assign(supplierForm, {
    id: row ? Number(row.id) : 0,
    name: row ? row.name : '',
    code: row ? String(row.code || '') : '',
    active: row ? Boolean(row.active) : true,
  })
}

function openCategoryDialog(row, supplier) {
  dialogKind.value = 'category'
  dialogError.value = ''
  const owner = row ? suppliers.value.find((item) => Number(item.id) === Number(row.supplier_id)) : supplier
  Object.assign(categoryForm, {
    id: row ? Number(row.id) : 0,
    supplier_id: row ? Number(row.supplier_id) : supplier ? Number(supplier.id) : 0,
    supplier_name: owner ? owner.name : '',
    name: row ? row.name : '',
    code: row ? String(row.code || '') : '',
    active: row ? Boolean(row.active) : true,
  })
}

function openPositionDialog(row) {
  dialogKind.value = 'position'
  dialogError.value = ''
  if (row) {
    Object.assign(positionForm, {
      id: Number(row.id),
      name: row.name,
      sort_order: Number(row.sort_order) || 0,
      show_in_order: Boolean(row.show_in_order),
      active: Boolean(row.active),
    })
    return
  }
  const maxOrder = positionsSorted.value.reduce((max, item) => Math.max(max, Number(item.sort_order) || 0), 0)
  Object.assign(positionForm, {
    id: 0,
    name: '',
    sort_order: maxOrder + 10,
    show_in_order: true,
    active: true,
  })
}

function closeDialog() {
  if (dialogSaving.value) return
  dialogKind.value = ''
  dialogError.value = ''
}

function onProductSupplierChange() {
  const options = selectableCategories(productForm.supplier_id, 0)
  productForm.category_id = options.length ? Number(options[0].id) : 0
  productForm.seq = ''
  productForm.seq_touched = false
}

function onProductCategoryChange() {
  productForm.seq = ''
  productForm.seq_touched = false
}

function onSeqInput(event) {
  const digits = String(event.target.value || '').replace(/\D/g, '').slice(0, 3)
  productForm.seq = digits
  productForm.seq_touched = true
  event.target.value = digits
}

function onManualCodeInput(event) {
  const digits = String(event.target.value || '').replace(/\D/g, '').slice(0, 7)
  productForm.manual_code = digits
  event.target.value = digits
}

function onCodeManualChange(event) {
  const checked = Boolean(event.target.checked)
  const auto = autoProductCode()
  productForm.code_manual = checked
  productForm.manual_code = checked ? auto : ''
}

function onSupplierCodeInput(event) {
  const digits = String(event.target.value || '').replace(/\D/g, '').slice(0, 2)
  supplierForm.code = digits
  event.target.value = digits
}

function onCategoryCodeInput(event) {
  const digits = String(event.target.value || '').replace(/\D/g, '').slice(0, 2)
  categoryForm.code = digits
  event.target.value = digits
}

async function toggleProductActive(row) {
  if (productBusy.value) return
  productBusy.value = Number(row.id)
  errorText.value = ''
  try {
    await api.patch('/api/purchase/admin/products/' + row.id, { active: !row.active })
    notice.value = '「' + row.name + '」已' + (row.active ? '停用' : '启用') + '。'
    await loadAll()
  } catch (err) {
    errorText.value = err.message || '商品的启用状态没保存成功'
  } finally {
    productBusy.value = 0
  }
}

async function toggleSupplierActive(row) {
  if (supplierBusy.value) return
  supplierBusy.value = Number(row.id)
  errorText.value = ''
  try {
    await api.patch('/api/purchase/admin/suppliers/' + row.id, { active: !row.active })
    notice.value = '供应商「' + row.name + '」已' + (row.active ? '停用' : '启用') + '。'
    await loadAll()
  } catch (err) {
    errorText.value = err.message || '供应商的启用状态没保存成功'
  } finally {
    supplierBusy.value = 0
  }
}

async function toggleCategoryActive(row) {
  if (categoryBusy.value) return
  categoryBusy.value = Number(row.id)
  errorText.value = ''
  try {
    await api.patch('/api/purchase/admin/categories/' + row.id, { active: !row.active })
    notice.value = '分类「' + row.name + '」已' + (row.active ? '停用' : '启用') + '。'
    await loadAll()
  } catch (err) {
    errorText.value = err.message || '分类的启用状态没保存成功'
  } finally {
    categoryBusy.value = 0
  }
}

async function togglePositionActive(row) {
  if (positionBusy.value) return
  positionBusy.value = Number(row.id)
  errorText.value = ''
  try {
    await api.patch('/api/purchase/admin/positions/' + row.id, { active: !row.active })
    notice.value = '岗位「' + row.name + '」已' + (row.active ? '停用' : '启用') + '。'
    await loadAll()
  } catch (err) {
    errorText.value = err.message || '岗位的启用状态没保存成功'
  } finally {
    positionBusy.value = 0
  }
}

async function toggleShowInOrder(row) {
  if (positionBusy.value) return
  positionBusy.value = Number(row.id)
  errorText.value = ''
  try {
    await api.patch('/api/purchase/admin/positions/' + row.id, { show_in_order: !row.show_in_order })
    await loadAll()
  } catch (err) {
    errorText.value = err.message || '下单可见开关没保存成功'
    await loadAll()
  } finally {
    positionBusy.value = 0
  }
}

async function movePosition(index, delta) {
  if (positionBusy.value) return
  const list = positionsSorted.value
  const targetIndex = index + delta
  if (targetIndex < 0 || targetIndex >= list.length) return
  const row = list[index]
  const other = list[targetIndex]
  positionBusy.value = Number(row.id)
  errorText.value = ''
  try {
    const currentOrder = Number(row.sort_order) || 0
    const otherOrder = Number(other.sort_order) || 0
    let nextCurrent = otherOrder
    let nextOther = currentOrder
    if (currentOrder === otherOrder) {
      // 两条序号相同（历史数据）时交换数字不会改变排序，改用相邻序号把位置让出来。
      if (delta < 0) {
        nextCurrent = otherOrder - 1
        nextOther = otherOrder
      } else {
        nextCurrent = otherOrder + 1
        nextOther = otherOrder
      }
    }
    await api.patch('/api/purchase/admin/positions/' + row.id, { sort_order: nextCurrent })
    await api.patch('/api/purchase/admin/positions/' + other.id, { sort_order: nextOther })
    notice.value = '岗位顺序已更新。'
    await loadAll()
  } catch (err) {
    errorText.value = err.message || '岗位顺序没保存成功'
    await loadAll()
  } finally {
    positionBusy.value = 0
  }
}

async function saveDialog() {
  if (dialogSaving.value) return
  if (dialogKind.value === 'product') return saveProduct()
  if (dialogKind.value === 'supplier') return saveSupplier()
  if (dialogKind.value === 'category') return saveCategory()
  if (dialogKind.value === 'position') return savePosition()
}

async function saveProduct() {
  dialogError.value = ''
  if (!productForm.supplier_id) {
    dialogError.value = '请先选供应商'
    return
  }
  if (!productForm.category_id) {
    dialogError.value = '请先选分类'
    return
  }
  const name = productForm.name.trim()
  if (!name) {
    dialogError.value = '商品名称不能为空'
    return
  }
  if (!productForm.position_ids.length) {
    dialogError.value = '商品至少要挂一个岗位'
    return
  }
  const step = Number(productForm.order_step)
  if (!Number.isFinite(step) || step <= 0) {
    dialogError.value = '下单步长要大于 0'
    return
  }
  const factor = Number(productForm.safety_factor)
  if (!Number.isFinite(factor) || factor <= 0) {
    dialogError.value = '安全系数要大于 0'
    return
  }

  let productCode = ''
  if (productForm.code_manual) {
    productCode = String(productForm.manual_code || '').trim()
    if (!/^\d{7}$/.test(productCode)) {
      dialogError.value = '完整编码必须是 7 位数字'
      return
    }
  } else if (productForm.seq_touched && String(productForm.seq || '').trim()) {
    const seq = String(productForm.seq).trim()
    if (!/^\d{1,3}$/.test(seq) || Number(seq) < 1) {
      dialogError.value = '序号要填 1–999 的数字'
      return
    }
    productCode = composeProductCode(productForm.supplier_id, productForm.category_id, Number(seq))
    if (!productCode) {
      dialogError.value = '供应商或分类还没选好，生成不了编码'
      return
    }
  }

  // 编辑时 product_code 传空：只要换了供应商/分类，服务端就会重算并查重；
  // 没换就沿用旧编码。只有用户手动填了序号或完整编码才提交具体编码。
  const payload = {
    supplier_id: Number(productForm.supplier_id),
    category_id: Number(productForm.category_id),
    product_code: productCode,
    name,
    specification: productForm.specification.trim(),
    unit: productForm.unit.trim(),
    remark: productForm.remark.trim(),
    order_step: step,
    safety_factor: factor,
    need_stock_check: Boolean(productForm.need_stock_check),
    is_long_cycle: Boolean(productForm.is_long_cycle),
    position_ids: productForm.position_ids.map(Number),
  }
  if (productForm.id) payload.active = Boolean(productForm.active)

  const editing = Boolean(productForm.id)
  dialogSaving.value = true
  try {
    if (editing) await api.patch('/api/purchase/admin/products/' + productForm.id, payload)
    else await api.post('/api/purchase/admin/products', payload)
    dialogKind.value = ''
    notice.value = editing ? '商品已保存，列表已刷新。' : '商品已新建。'
    await loadAll()
  } catch (err) {
    dialogError.value = err.message || '商品没保存成功'
  } finally {
    dialogSaving.value = false
  }
}

async function saveSupplier() {
  dialogError.value = ''
  const name = supplierForm.name.trim()
  if (!name) {
    dialogError.value = '供应商名称不能为空'
    return
  }
  const code = supplierForm.code.trim()
  if (code && !/^\d{2}$/.test(code)) {
    dialogError.value = '供应商编码必须是 2 位数字'
    return
  }
  const payload = { name }
  if (code) payload.code = code
  if (supplierForm.id) payload.active = Boolean(supplierForm.active)
  const editing = Boolean(supplierForm.id)
  dialogSaving.value = true
  try {
    if (editing) await api.patch('/api/purchase/admin/suppliers/' + supplierForm.id, payload)
    else await api.post('/api/purchase/admin/suppliers', payload)
    dialogKind.value = ''
    notice.value = editing ? '供应商已保存。' : '供应商已新建。'
    await loadAll()
  } catch (err) {
    dialogError.value = err.message || '供应商没保存成功'
  } finally {
    dialogSaving.value = false
  }
}

async function saveCategory() {
  dialogError.value = ''
  if (!categoryForm.supplier_id) {
    dialogError.value = '请先选供应商'
    return
  }
  const name = categoryForm.name.trim()
  if (!name) {
    dialogError.value = '分类名称不能为空'
    return
  }
  const code = categoryForm.code.trim()
  if (code && !/^\d{2}$/.test(code)) {
    dialogError.value = '分类编码必须是 2 位数字'
    return
  }
  const payload = { supplier_id: Number(categoryForm.supplier_id), name }
  if (code) payload.code = code
  if (categoryForm.id) payload.active = Boolean(categoryForm.active)
  const editing = Boolean(categoryForm.id)
  dialogSaving.value = true
  try {
    if (editing) await api.patch('/api/purchase/admin/categories/' + categoryForm.id, payload)
    else await api.post('/api/purchase/admin/categories', payload)
    dialogKind.value = ''
    notice.value = editing ? '分类已保存。' : '分类已新建。'
    await loadAll()
  } catch (err) {
    dialogError.value = err.message || '分类没保存成功'
  } finally {
    dialogSaving.value = false
  }
}

async function savePosition() {
  dialogError.value = ''
  const name = positionForm.name.trim()
  if (!name) {
    dialogError.value = '岗位名称不能为空'
    return
  }
  const order = Number(positionForm.sort_order)
  if (!Number.isFinite(order)) {
    dialogError.value = '序号要填数字'
    return
  }
  const payload = {
    name,
    sort_order: Math.trunc(order),
    show_in_order: Boolean(positionForm.show_in_order),
  }
  if (positionForm.id) payload.active = Boolean(positionForm.active)
  const editing = Boolean(positionForm.id)
  dialogSaving.value = true
  try {
    if (editing) await api.patch('/api/purchase/admin/positions/' + positionForm.id, payload)
    else await api.post('/api/purchase/admin/positions', payload)
    dialogKind.value = ''
    notice.value = editing ? '岗位已保存。' : '岗位已新建。'
    await loadAll()
  } catch (err) {
    dialogError.value = err.message || '岗位没保存成功'
  } finally {
    dialogSaving.value = false
  }
}

async function bulkSet(dates, tag) {
  if (!dates.length) {
    errorText.value = '先在月历里选日期'
    return
  }
  if (tagBusy.value) return
  tagBusy.value = true
  errorText.value = ''
  try {
    const result = await api.put('/api/purchase/admin/date-tags/bulk', { dates, tag })
    const count = result && Number(result.count) ? Number(result.count) : dates.length
    notice.value = '已给 ' + count + ' 天设为 ' + tag + '。'
    clearSelection()
    await loadTags()
  } catch (err) {
    errorText.value = err.message || '批量设置日期标签没成功'
  } finally {
    tagBusy.value = false
  }
}

async function setSelectedTag() {
  await bulkSet(selectedDates.value.slice(), bulkTag.value)
}

async function deleteSelectedTags() {
  const dates = selectedDates.value.slice()
  if (!dates.length) {
    errorText.value = '先在月历里选日期'
    return
  }
  if (tagBusy.value) return
  tagBusy.value = true
  errorText.value = ''
  let deleted = 0
  try {
    // 区间 DELETE 会把区间里没选中的日期也清掉，所以选中日期的删除逐日发。
    for (const date of dates) {
      await api.delete('/api/purchase/admin/date-tags/' + encodeURIComponent(date))
      deleted += 1
    }
    notice.value = '已删除 ' + deleted + ' 天的指定标签，改回按星期/默认。'
    clearSelection()
    await loadTags()
  } catch (err) {
    errorText.value = err.message || '删除日期标签没成功'
    if (deleted) {
      notice.value = '已删除 ' + deleted + ' 天，剩下的没删完。'
      await loadTags()
    }
  } finally {
    tagBusy.value = false
  }
}

async function removeDateTag(value) {
  if (tagBusy.value) return
  tagBusy.value = true
  errorText.value = ''
  try {
    await api.delete('/api/purchase/admin/date-tags/' + encodeURIComponent(value))
    notice.value = value + ' 的指定标签已取消，改回按星期/默认。'
    selectedDates.value = selectedDates.value.filter((date) => date !== value)
    await loadTags()
  } catch (err) {
    errorText.value = err.message || '日期标签没删掉'
  } finally {
    tagBusy.value = false
  }
}

async function setWeekendTags() {
  const dates = monthDateList().filter((value) => {
    const weekday = dateWeekday(value)
    return weekday === 0 || weekday === 6
  })
  await bulkSet(dates, '6万')
}

async function setMondayTags() {
  const dates = monthDateList().filter((value) => dateWeekday(value) === 1)
  await bulkSet(dates, '4万')
}

async function clearMonthTags() {
  if (tagBusy.value) return
  if (!clearArmed.value) {
    clearArmed.value = true
    if (clearTimer) window.clearTimeout(clearTimer)
    clearTimer = window.setTimeout(() => {
      clearArmed.value = false
      clearTimer = 0
    }, 5000)
    return
  }
  clearArmed.value = false
  if (clearTimer) {
    window.clearTimeout(clearTimer)
    clearTimer = 0
  }
  const [start, end] = monthRange()
  tagBusy.value = true
  errorText.value = ''
  try {
    const result = await api.delete('/api/purchase/admin/date-tags', { start, end })
    const deleted = result && Number(result.deleted) ? Number(result.deleted) : 0
    notice.value = '已清空本月 ' + deleted + ' 天的指定标签；星期默认没动。'
    clearSelection()
    await loadTags()
  } catch (err) {
    errorText.value = err.message || '清空本月标签没成功'
  } finally {
    tagBusy.value = false
  }
}

async function saveWeekdayDefault(weekday, tag) {
  if (!tag) return
  errorText.value = ''
  try {
    await api.put('/api/purchase/admin/weekday-defaults', { weekday: Number(weekday), tag })
    tagData.value = {
      ...tagData.value,
      weekday_defaults: { ...(tagData.value.weekday_defaults || {}), [weekday]: tag },
    }
    notice.value = weekdayText(weekday) + ' 的默认标签已设为 ' + tag + '。'
  } catch (err) {
    errorText.value = err.message || '星期默认没保存成功'
  }
}

function openImportDialog() {
  importDialog.value = true
  importFile.value = null
  importPreview.value = null
  importError.value = ''
  if (importFileInput.value) importFileInput.value.value = ''
}

function closeImportDialog() {
  if (importApplying.value) return
  importDialog.value = false
}

async function onImportFile(event) {
  const file = event.target.files && event.target.files[0]
  if (!file) return
  importFile.value = file
  await previewImport(file)
}

async function refreshImportPreview() {
  if (importFile.value) await previewImport(importFile.value)
}

async function previewImport(file) {
  importError.value = ''
  importPreview.value = null
  if (!file.name.toLowerCase().endsWith('.xlsx')) {
    importError.value = '请选择 .xlsx 文件（Excel 工作簿）'
    return
  }
  if (file.size > 10 * 1024 * 1024) {
    importError.value = '文件不能超过 10 MB'
    return
  }
  importLoading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    form.append('mode', importMode.value)
    form.append('dry_run', 'true')
    importPreview.value = await api.upload('/api/purchase/admin/products/import', form)
  } catch (err) {
    importError.value = err.message || '表格没读出来'
  } finally {
    importLoading.value = false
  }
}

async function applyImport() {
  if (!importFile.value || !importPreview.value) return
  importApplying.value = true
  importError.value = ''
  try {
    const form = new FormData()
    form.append('file', importFile.value)
    form.append('mode', importMode.value)
    form.append('dry_run', 'false')
    const result = await api.upload('/api/purchase/admin/products/import', form)
    importPreview.value = result
    notice.value =
      '导入完成：新建 ' + result.created + ' 个，更新 ' + result.updated + ' 个，失败 ' + result.failed + ' 行。'
    await loadAll()
  } catch (err) {
    importError.value = err.message || '导入没成功'
  } finally {
    importApplying.value = false
  }
}

async function downloadCatalog() {
  errorText.value = ''
  try {
    await api.download('/api/purchase/admin/products/export')
  } catch (err) {
    errorText.value = err.message || '目录没导出成功'
  }
}

async function downloadTemplate() {
  errorText.value = ''
  try {
    await api.download('/api/purchase/admin/products/template')
  } catch (err) {
    errorText.value = err.message || '模板没下载成功'
  }
}

onMounted(loadAll)
onUnmounted(() => {
  if (clearTimer) window.clearTimeout(clearTimer)
})
</script>

<template>
  <div class="catalog">
    <PurchaseNav active="catalog" />

    <header class="cat-hero">
      <div class="cat-hero-top">
        <div class="cat-hero-copy">
          <p class="cat-eyebrow">厨房采购 · 目录</p>
          <h1 class="cat-title">{{ activeTab === 'positions' ? '岗位管理' : '商品管理' }}</h1>
          <p class="cat-sub">
            {{ activeTab === 'positions'
              ? '岗位决定下单页看到哪些商品；序号决定顺序，停用保留历史。'
              : '供应商 → 分类 → 商品；编码 2 + 2 + 3，停用保留历史。' }}
          </p>
        </div>
        <button v-if="activeTab !== 'positions'" type="button" class="cat-pos-link" @click="activeTab = 'positions'">
          岗位管理 <span aria-hidden="true">›</span>
        </button>
      </div>
      <div v-if="activeTab !== 'positions'" class="cat-hero-actions">
        <button type="button" class="cat-hero-btn" @click="openImportDialog">批量导入</button>
        <button type="button" class="cat-hero-btn" @click="downloadCatalog">批量导出</button>
        <button type="button" class="cat-hero-btn" @click="router.push(PURCHASE_MIGRATION_PATH)">备份/迁移</button>
      </div>
    </header>

    <p v-if="errorText" class="banner" role="alert">{{ errorText }}</p>
    <p v-else-if="notice" class="banner banner-soft">{{ notice }}</p>

    <nav v-if="activeTab !== 'positions'" class="tabs" role="tablist" aria-label="目录分区">
      <button
        v-for="tab in TABS"
        :key="tab.key"
        type="button"
        class="tab"
        :class="{ 'is-active': activeTab === tab.key }"
        role="tab"
        :aria-selected="activeTab === tab.key"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>

    <p v-if="loading && !products.length" class="empty">正在加载目录…</p>

    <template v-else>
      <section v-if="activeTab === 'products'" class="tab-panel products-panel" role="tabpanel" aria-label="商品">
        <div class="product-tools">
          <div class="search-box">
            <input
              v-model="keyword"
              class="input product-search"
              type="search"
              placeholder="搜编码 / 名称 / 供应商"
              aria-label="搜索商品"
            />
            <svg class="search-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <circle cx="11" cy="11" r="6.5" />
              <path d="m16 16 4 4" />
            </svg>
          </div>
          <div class="filter-row">
            <select
              v-model.number="supplierFilter"
              class="filter-chip"
              :class="{ 'is-on': supplierFilter }"
              aria-label="按供应商筛选"
            >
              <option :value="0">供应商</option>
              <option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">
                {{ supplier.name }}{{ supplier.active ? '' : '（停用）' }}
              </option>
            </select>
            <select
              v-model.number="positionFilter"
              class="filter-chip"
              :class="{ 'is-on': positionFilter }"
              aria-label="按岗位筛选"
            >
              <option :value="0">岗位</option>
              <option v-for="position in positionsSorted" :key="position.id" :value="position.id">
                {{ position.name }}{{ position.active ? '' : '（停用）' }}
              </option>
            </select>
            <button
              v-if="keyword || supplierFilter || positionFilter"
              type="button"
              class="filter-clear"
              @click="clearProductFilters"
            >
              清除筛选
            </button>
            <button type="button" class="btn btn-primary product-add-desktop" @click="openProductDialog(null)">
              + 新建商品
            </button>
          </div>
        </div>

        <div class="list-meta">
          <span>共 {{ products.length }} 个商品 · 筛选后 {{ filteredProducts.length }} 个</span>
          <span class="list-meta-right">
            <span v-if="filteredProducts.length">第 {{ currentPage }} / {{ pageCount }} 页</span>
            <button type="button" class="meta-refresh" :disabled="loading" @click="loadAll">
              {{ loading ? '刷新中…' : '刷新' }}
            </button>
          </span>
        </div>

        <div v-if="!filteredProducts.length" class="empty">
          没有匹配的商品。先确认供应商与分类建好，再新建商品。
        </div>

        <div v-else class="product-grid">
          <article
            v-for="product in pagedProducts"
            :key="product.id"
            class="product"
            :class="{ 'is-off': !product.active }"
          >
            <div class="product-head">
              <span class="code-chip">{{ product.product_code }}</span>
              <div class="product-head-main">
                <h2 class="product-name">{{ product.name }}</h2>
                <div class="badges">
                  <span v-if="product.need_stock_check" class="badge badge-stock">需盘点</span>
                  <span v-if="product.is_long_cycle" class="badge badge-cycle">长周期</span>
                  <span v-if="!product.active" class="badge badge-off">已停用</span>
                </div>
              </div>
            </div>

            <div class="product-fields">
              <div>
                <span>供应商</span>
                <strong>{{ product.supplier_name || '—' }}<template v-if="product.supplier_code">（{{ product.supplier_code }}）</template></strong>
              </div>
              <div>
                <span>分类</span>
                <strong>{{ product.category_name || '—' }}<template v-if="product.category_code">（{{ product.category_code }}）</template></strong>
              </div>
              <div>
                <span>岗位</span>
                <strong v-if="(product.positions || []).length">
                  {{ product.positions.map((item) => item.name).join('、') }}
                </strong>
                <strong v-else class="product-field-warn">未挂岗位，不会出现在下单页</strong>
              </div>
              <div>
                <span>单位</span>
                <strong>{{ product.unit || '—' }}</strong>
              </div>
              <div v-if="product.specification">
                <span>规格</span>
                <strong>{{ product.specification }}</strong>
              </div>
              <div v-if="product.remark" class="product-field-remark">
                <span>备注</span>
                <strong>{{ product.remark }}</strong>
              </div>
            </div>

            <div class="product-actions">
              <button type="button" class="btn" @click="openProductDialog(product)">编辑</button>
              <button
                type="button"
                class="btn"
                :class="product.active ? 'btn-danger' : 'btn-enable'"
                :disabled="productBusy === product.id"
                @click="toggleProductActive(product)"
              >
                {{ productBusy === product.id ? '保存中…' : product.active ? '停用' : '启用' }}
              </button>
            </div>
          </article>
        </div>

        <nav v-if="pageCount > 1" class="pager" aria-label="商品分页">
          <button type="button" class="btn icon-btn" :disabled="currentPage <= 1" aria-label="上一页" @click="goPage(-1)">
            ←
          </button>
          <span class="pager-info">第 {{ currentPage }} / {{ pageCount }} 页</span>
          <button
            type="button"
            class="btn icon-btn"
            :disabled="currentPage >= pageCount"
            aria-label="下一页"
            @click="goPage(1)"
          >
            →
          </button>
        </nav>

        <button type="button" class="product-fab" @click="openProductDialog(null)">+ 添加商品</button>
      </section>

      <section v-else-if="activeTab === 'suppliers'" class="tab-panel" role="tabpanel" aria-label="供应商">
        <div class="toolbar">
          <p class="toolbar-note">
            供应商编码 2 位、分类编码 2 位；分类挂在供应商下面。停用不会动历史单据，新增分类的编码可以留空自动取。
          </p>
          <div class="toolbar-actions">
            <button type="button" class="btn btn-primary" @click="openSupplierDialog(null)">+ 新建供应商</button>
          </div>
        </div>

        <div v-if="!suppliers.length" class="empty">还没有供应商。先新建一个，再给它建分类。</div>

        <div v-else class="supplier-list">
          <article
            v-for="supplier in suppliers"
            :key="supplier.id"
            class="supplier"
            :class="{ 'is-off': !supplier.active }"
          >
            <header class="supplier-head">
              <span class="code-chip">{{ pad2(supplier.code) }}</span>
              <div class="supplier-main">
                <h2 class="supplier-name">{{ supplier.name }}</h2>
                <p class="supplier-meta">
                  {{ supplierCategories(supplier.id).length }} 个分类 · 下一分类编码 {{ pad2(supplier.next_category_code) }}
                </p>
              </div>
              <span class="pill" :class="supplier.active ? 'pill-on' : 'pill-off'">
                <i class="dot"></i>{{ supplier.active ? '启用中' : '已停用' }}
              </span>
              <div class="row-actions">
                <button type="button" class="btn" @click="openCategoryDialog(null, supplier)">+ 新增分类</button>
                <button type="button" class="btn" @click="openSupplierDialog(supplier)">编辑</button>
                <button
                  type="button"
                  class="btn"
                  :class="supplier.active ? 'btn-danger' : 'btn-enable'"
                  :disabled="supplierBusy === supplier.id"
                  @click="toggleSupplierActive(supplier)"
                >
                  {{ supplierBusy === supplier.id ? '保存中…' : supplier.active ? '停用' : '启用' }}
                </button>
              </div>
            </header>

          </article>
        </div>
      </section>

      <section v-else-if="activeTab === 'categories'" class="tab-panel" role="tabpanel" aria-label="分类">
        <div class="toolbar">
          <p class="toolbar-note">分类挂在供应商下面；编码是供应商 2 位 + 分类 2 位。停用分类不影响历史单据。</p>
          <div class="toolbar-actions">
            <select v-model.number="categorySupplierFilter" class="filter-chip" aria-label="按供应商筛选分类">
              <option :value="0">全部供应商</option>
              <option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">
                {{ supplier.name }}{{ supplier.active ? '' : '（停用）' }}
              </option>
            </select>
            <button
              type="button"
              class="btn btn-primary"
              @click="openCategoryDialog(null, categorySupplierFilter ? supplierById(categorySupplierFilter) : null)"
            >
              + 新建分类
            </button>
          </div>
        </div>

        <div v-if="!filteredCategories.length" class="empty">
          还没有分类。先选一个供应商，再给它建分类；分类编码可以留空自动取。
        </div>

        <div v-else class="category-cards">
          <article
            v-for="category in filteredCategories"
            :key="category.id"
            class="category-card"
            :class="{ 'is-off': !category.active }"
          >
            <header class="category-card-head">
              <span class="code-chip">{{ categoryFullCode(category) }}</span>
              <div class="category-card-main">
                <h2 class="category-card-name">{{ category.name }}</h2>
                <p class="category-card-meta">
                  {{ supplierById(category.supplier_id)?.name || '供应商已删' }} · {{ categoryProductCountOf(category.id) }} 个商品
                </p>
              </div>
              <span class="pill" :class="category.active ? 'pill-on' : 'pill-off'">
                <i class="dot"></i>{{ category.active ? '启用中' : '已停用' }}
              </span>
              <div class="row-actions">
                <button type="button" class="btn" @click="openCategoryDialog(category, null)">编辑</button>
                <button
                  type="button"
                  class="btn"
                  :class="category.active ? 'btn-danger' : 'btn-enable'"
                  :disabled="categoryBusy === category.id"
                  @click="toggleCategoryActive(category)"
                >
                  {{ categoryBusy === category.id ? '保存中…' : category.active ? '停用' : '启用' }}
                </button>
              </div>
            </header>
          </article>
        </div>
      </section>

      <section v-else-if="activeTab === 'positions'" class="tab-panel" role="tabpanel" aria-label="岗位">
        <div class="subpage-head">
          <button type="button" class="btn" @click="activeTab = 'products'">‹ 商品管理</button>
        </div>
        <div class="toolbar">
          <p class="toolbar-note">
            按序号从小到大排；上移 / 下移会交换相邻两条的序号。岗位被商品占用时不要物理删除，用停用。
          </p>
          <div class="toolbar-actions">
            <button type="button" class="btn btn-primary" @click="openPositionDialog(null)">+ 新建岗位</button>
          </div>
        </div>

        <div v-if="!positionsSorted.length" class="empty">还没有岗位。新建后商品才能挂岗位。</div>

        <div v-else class="panel pos-list">
          <div
            v-for="(position, index) in positionsSorted"
            :key="position.id"
            class="pos-row"
            :class="{ 'is-off': !position.active }"
          >
            <span class="pos-order">{{ index + 1 }}</span>
            <div class="pos-main">
              <strong class="pos-name">{{ position.name }}</strong>
              <span class="pos-meta">
                序号 {{ position.sort_order }} · {{ positionUsageCount(position.id) }} 个商品在用
              </span>
            </div>
            <label class="switch-line">
              <input
                type="checkbox"
                :checked="position.show_in_order"
                :disabled="positionBusy === position.id"
                @change="toggleShowInOrder(position)"
              />
              下单可见
            </label>
            <span class="pill" :class="position.active ? 'pill-on' : 'pill-off'">
              <i class="dot"></i>{{ position.active ? '启用中' : '已停用' }}
            </span>
            <div class="pos-actions">
              <button
                type="button"
                class="btn icon-btn"
                :disabled="index === 0 || positionBusy === position.id"
                aria-label="上移岗位"
                @click="movePosition(index, -1)"
              >
                ↑
              </button>
              <button
                type="button"
                class="btn icon-btn"
                :disabled="index === positionsSorted.length - 1 || positionBusy === position.id"
                aria-label="下移岗位"
                @click="movePosition(index, 1)"
              >
                ↓
              </button>
              <button type="button" class="btn" @click="openPositionDialog(position)">编辑</button>
              <button
                type="button"
                class="btn"
                :class="position.active ? 'btn-danger' : 'btn-enable'"
                :disabled="positionBusy === position.id"
                @click="togglePositionActive(position)"
              >
                {{ positionBusy === position.id ? '保存中…' : position.active ? '停用' : '启用' }}
              </button>
            </div>
          </div>
        </div>
      </section>

      <section v-else class="tab-panel" role="tabpanel" aria-label="日期标签">
        <div class="date-layout">
          <div class="panel calendar">
            <div class="cal-head">
              <div class="cal-nav">
                <button type="button" class="btn icon-btn" aria-label="上个月" :disabled="tagBusy" @click="shiftMonth(-1)">
                  ←
                </button>
                <strong class="cal-month">{{ monthLabel }}</strong>
                <button type="button" class="btn icon-btn" aria-label="下个月" :disabled="tagBusy" @click="shiftMonth(1)">
                  →
                </button>
              </div>
              <button
                type="button"
                class="btn"
                :disabled="tagBusy || monthCursor === TODAY.slice(0, 7)"
                @click="goToday"
              >
                回到本月
              </button>
            </div>

            <div class="cal-weekdays" aria-hidden="true">
              <span v-for="label in CALENDAR_HEAD" :key="label" class="cal-weekday">{{ label }}</span>
            </div>

            <div class="cal-grid" role="grid" aria-label="日期标签月历">
              <button
                v-for="cell in monthCells"
                :key="cell.date"
                type="button"
                class="cal-day"
                :class="{
                  'is-out': !cell.inMonth,
                  'is-today': cell.inMonth && cell.date === TODAY,
                  'is-selected': cell.inMonth && selectedSet.has(cell.date),
                }"
                :disabled="!cell.inMonth || tagBusy"
                :aria-pressed="cell.inMonth ? selectedSet.has(cell.date) : undefined"
                :aria-label="calendarCellLabel(cell)"
                @click="toggleDate(cell.date)"
              >
                <span class="cal-day-num">{{ cell.day }}</span>
                <span v-if="cell.inMonth && tagMap[cell.date]" class="cal-tag">{{ tagMap[cell.date] }}</span>
                <span v-else-if="cell.inMonth && weekdayDefaults[cell.weekday]" class="cal-default">
                  {{ weekdayDefaults[cell.weekday] }}
                </span>
              </button>
            </div>

            <p class="cal-legend">
              点日期多选。橙色角标是当天指定标签，灰色小字是星期默认；指定日期优先。
            </p>
          </div>

          <aside class="date-side">
            <div class="panel side-card">
              <h2 class="side-title">已选 {{ selectedDates.length }} 天</h2>
              <p class="side-note">选中后可以批量设置，也可以只删这些日期的手工标签。</p>
              <label class="field" for="bulk-tag">
                <span class="field-label">标签</span>
                <select id="bulk-tag" v-model="bulkTag" class="select" :disabled="tagBusy">
                  <option v-for="tag in TAG_VALUES" :key="tag" :value="tag">{{ tag }}</option>
                </select>
              </label>
              <div class="btn-col">
                <button
                  type="button"
                  class="btn btn-primary"
                  :disabled="!selectedDates.length || tagBusy"
                  @click="setSelectedTag"
                >
                  {{ tagBusy ? '处理中…' : '批量设置选中日期' }}
                </button>
                <button
                  type="button"
                  class="btn btn-danger"
                  :disabled="!selectedDates.length || tagBusy"
                  @click="deleteSelectedTags"
                >
                  批量删除选中日期
                </button>
                <button type="button" class="btn" :disabled="!selectedDates.length || tagBusy" @click="clearSelection">
                  清空选择
                </button>
              </div>
            </div>

            <div class="panel side-card">
              <h2 class="side-title">快捷设置</h2>
              <p class="side-note">只改本月的指定日期标签，星期默认不受影响。</p>
              <div class="btn-col">
                <button type="button" class="btn" :disabled="tagBusy" @click="setWeekendTags">
                  本月所有周六日设 6万
                </button>
                <button type="button" class="btn" :disabled="tagBusy" @click="setMondayTags">
                  本月所有周一设 4万
                </button>
                <button type="button" class="btn btn-danger" :disabled="tagBusy" @click="clearMonthTags">
                  {{ clearArmed ? '再点一次确认清空本月' : '清空本月标签' }}
                </button>
              </div>
            </div>

            <div class="panel side-card">
              <h2 class="side-title">星期默认</h2>
              <p class="side-note">某天没有指定标签时按星期取默认；指定日期优先。</p>
              <div class="wd-grid">
                <div v-for="weekday in WEEKDAY_KEYS" :key="weekday" class="wd-row">
                  <span>{{ weekdayText(weekday) }}</span>
                  <select
                    class="select"
                    :value="weekdayDefaults[weekday] || ''"
                    :disabled="tagBusy"
                    @change="saveWeekdayDefault(weekday, $event.target.value)"
                  >
                    <option value="" disabled>未设置</option>
                    <option v-for="tag in TAG_VALUES" :key="tag" :value="tag">{{ tag }}</option>
                  </select>
                </div>
              </div>
            </div>
          </aside>
        </div>

        <div class="panel tag-list-panel">
          <h2 class="side-title">本月已设标签</h2>
          <p v-if="tagsLoading" class="side-note">正在加载日期标签…</p>
          <p v-else-if="!monthTags.length" class="side-note">本月还没有手工标签。可以从上面的月历多选后批量设置。</p>
          <ul v-else class="tag-list">
            <li v-for="row in monthTags" :key="row.tag_date" class="tag-item">
              <span class="tag-date">{{ formatMonthDay(row.tag_date) }}</span>
              <span class="tag-weekday">{{ weekdayText(dateWeekday(row.tag_date)) }}</span>
              <span class="tag-value">{{ row.tag }}</span>
              <button type="button" class="link-btn" :disabled="tagBusy" @click="removeDateTag(row.tag_date)">
                删除
              </button>
            </li>
          </ul>
        </div>
      </section>
    </template>

    <Teleport to="body">
      <div v-if="dialogKind" class="scrim" @click.self="closeDialog">
        <div
          class="dialog"
          :class="{ 'dialog-wide': dialogKind === 'product' }"
          role="dialog"
          aria-modal="true"
          :aria-label="dialogTitle"
        >
          <header class="dialog-head">
            <h2 class="dialog-title">{{ dialogTitle }}</h2>
            <button type="button" class="dialog-close" aria-label="关闭" @click="closeDialog">×</button>
          </header>

          <div class="dialog-body">
            <p v-if="dialogError" class="dialog-error" role="alert">{{ dialogError }}</p>

            <template v-if="dialogKind === 'product'">
              <section class="form-section">
                <h3 class="form-section-title">归属与编码</h3>
                <div class="form-grid">
                  <label class="field">
                    <span class="field-label">供应商</span>
                    <select v-model.number="productForm.supplier_id" class="select" @change="onProductSupplierChange">
                      <option :value="0" disabled>请选择供应商</option>
                      <option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">
                        {{ supplier.name }}（{{ pad2(supplier.code) }}）{{ supplier.active ? '' : ' · 已停用' }}
                      </option>
                    </select>
                  </label>
                  <label class="field">
                    <span class="field-label">分类</span>
                    <select
                      v-model.number="productForm.category_id"
                      class="select"
                      :disabled="!productForm.supplier_id"
                      @change="onProductCategoryChange"
                    >
                      <option :value="0" disabled>
                        {{ productCategoryOptions.length ? '请选择分类' : '这个供应商还没有分类' }}
                      </option>
                      <option v-for="category in productCategoryOptions" :key="category.id" :value="category.id">
                        {{ pad2(category.code) }} {{ category.name }}{{ category.active ? '' : ' · 已停用' }}
                      </option>
                    </select>
                  </label>

                  <div class="field field-full">
                    <div class="code-box">
                      <div class="code-preview">
                        <span class="code-preview-label">商品编码预览</span>
                        <strong class="code-preview-value">{{ productPreviewCode || '待选择' }}</strong>
                        <p class="code-preview-hint">{{ productPreviewHint }}</p>
                      </div>
                      <label class="switch-line">
                        <input
                          type="checkbox"
                          :checked="productForm.code_manual"
                          @change="onCodeManualChange"
                        />
                        手动填写完整编码
                      </label>
                      <input
                        v-if="productForm.code_manual"
                        class="input mono"
                        inputmode="numeric"
                        maxlength="7"
                        placeholder="7 位数字"
                        :value="productForm.manual_code"
                        aria-label="完整商品编码"
                        @input="onManualCodeInput"
                      />
                      <label v-else class="field">
                        <span class="field-label">序号（3 位）</span>
                        <input
                          class="input mono"
                          inputmode="numeric"
                          maxlength="3"
                          placeholder="留空自动"
                          :value="productForm.seq"
                          :disabled="!productForm.supplier_id || !productForm.category_id"
                          aria-label="商品序号"
                          @input="onSeqInput"
                        />
                        <span class="field-hint">
                          留空按下一序号 {{ pad3(productNextSeq) }} 自动生成；编码 = 供应商
                          {{ productSupplierCodeText }} + 分类 {{ productCategoryCodeText }} + 序号。
                        </span>
                      </label>
                    </div>
                  </div>
                </div>
                <p v-if="!productForm.supplier_id || !productForm.category_id" class="field-hint">
                  还没有可用的供应商 / 分类？先到「供应商与分类」里建。
                </p>
              </section>

              <section class="form-section">
                <h3 class="form-section-title">基本信息</h3>
                <div class="form-grid">
                  <label class="field field-full">
                    <span class="field-label">名称</span>
                    <input v-model="productForm.name" class="input" maxlength="60" placeholder="例如 五花肉" />
                  </label>
                  <label class="field">
                    <span class="field-label">规格</span>
                    <input
                      v-model="productForm.specification"
                      class="input"
                      maxlength="60"
                      placeholder="例如 带皮 / 5 斤装"
                    />
                  </label>
                  <label class="field">
                    <span class="field-label">单位</span>
                    <input
                      v-model="productForm.unit"
                      class="input"
                      maxlength="10"
                      list="purchase-units"
                      placeholder="斤 / 箱 / 袋"
                    />
                  </label>
                  <label class="field field-full">
                    <span class="field-label">备注</span>
                    <textarea
                      v-model="productForm.remark"
                      class="textarea"
                      maxlength="300"
                      rows="3"
                      placeholder="选填，最多 300 字"
                    ></textarea>
                  </label>
                </div>
                <datalist id="purchase-units">
                  <option v-for="unit in meta.units" :key="unit" :value="unit" />
                </datalist>
              </section>

              <section class="form-section">
                <h3 class="form-section-title">采购参数</h3>
                <div class="form-grid">
                  <label class="field">
                    <span class="field-label">下单步长</span>
                    <input
                      v-model.number="productForm.order_step"
                      class="input"
                      type="number"
                      min="0.01"
                      step="0.01"
                    />
                    <span class="field-hint">大于 0；下单数量按这个步长走。</span>
                  </label>
                  <label class="field">
                    <span class="field-label">安全系数</span>
                    <input
                      v-model.number="productForm.safety_factor"
                      class="input"
                      type="number"
                      min="0.01"
                      step="0.01"
                    />
                    <span class="field-hint">大于 0；参考量会按这个系数放大。</span>
                  </label>
                </div>
              </section>

              <section class="form-section">
                <h3 class="form-section-title">标记与岗位</h3>
                <div class="switch-row">
                  <label class="switch-line">
                    <input v-model="productForm.need_stock_check" type="checkbox" />
                    需盘点
                  </label>
                  <label class="switch-line">
                    <input v-model="productForm.is_long_cycle" type="checkbox" />
                    长周期
                  </label>
                  <label v-if="productForm.id" class="switch-line">
                    <input v-model="productForm.active" type="checkbox" />
                    启用
                  </label>
                </div>
                <p v-if="!productForm.id" class="field-hint">
                  新建商品保存后默认启用；要停用，保存后在列表里点「停用」。
                </p>
                <div class="field">
                  <span class="field-label">岗位（至少一个）</span>
                  <div v-if="productPositionOptions.length" class="pos-picker">
                    <label
                      v-for="position in productPositionOptions"
                      :key="position.id"
                      class="pos-chip"
                      :class="{ 'is-on': productForm.position_ids.includes(Number(position.id)) }"
                    >
                      <input v-model="productForm.position_ids" type="checkbox" :value="Number(position.id)" />
                      <span>{{ position.name }}</span>
                      <span v-if="!position.active" class="chip-note">已停用</span>
                    </label>
                  </div>
                  <p v-else class="field-hint">还没有岗位，先到「岗位」tab 建一个。</p>
                </div>
              </section>
            </template>

            <template v-else-if="dialogKind === 'supplier'">
              <div class="form-grid">
                <label class="field field-full">
                  <span class="field-label">供应商名称</span>
                  <input v-model="supplierForm.name" class="input" maxlength="60" placeholder="例如 鲜丰蔬菜" />
                </label>
                <label class="field">
                  <span class="field-label">编码（2 位）</span>
                  <input
                    class="input mono"
                    inputmode="numeric"
                    maxlength="2"
                    placeholder="留空自动"
                    :value="supplierForm.code"
                    @input="onSupplierCodeInput"
                  />
                  <span class="field-hint">新建留空会自动取下一个 2 位码；编辑留空表示不改。</span>
                </label>
              </div>
              <label v-if="supplierForm.id" class="switch-line">
                <input v-model="supplierForm.active" type="checkbox" />
                启用
              </label>
              <p v-else class="field-hint">新建后默认启用；要停用，保存后在卡片上点「停用」。</p>
            </template>

            <template v-else-if="dialogKind === 'category'">
              <div class="form-grid">
                <label class="field field-full">
                  <span class="field-label">供应商（锁定）</span>
                  <input class="input" :value="categoryForm.supplier_name" disabled />
                </label>
                <label class="field">
                  <span class="field-label">分类名称</span>
                  <input v-model="categoryForm.name" class="input" maxlength="60" placeholder="例如 叶菜" />
                </label>
                <label class="field">
                  <span class="field-label">编码（2 位）</span>
                  <input
                    class="input mono"
                    inputmode="numeric"
                    maxlength="2"
                    placeholder="留空自动"
                    :value="categoryForm.code"
                    @input="onCategoryCodeInput"
                  />
                  <span class="field-hint">新建留空自动取这个供应商的下一个 2 位码；编辑留空表示不改。</span>
                </label>
              </div>
              <label v-if="categoryForm.id" class="switch-line">
                <input v-model="categoryForm.active" type="checkbox" />
                启用
              </label>
              <p v-else class="field-hint">新建后默认启用；要停用，保存后在分类行上点「停用」。</p>
            </template>

            <template v-else>
              <div class="form-grid">
                <label class="field field-full">
                  <span class="field-label">岗位名称</span>
                  <input v-model="positionForm.name" class="input" maxlength="60" placeholder="例如 砧板" />
                </label>
                <label class="field">
                  <span class="field-label">排序序号</span>
                  <input v-model.number="positionForm.sort_order" class="input" type="number" step="1" />
                  <span class="field-hint">数字越小越靠前；上移 / 下移会交换序号。</span>
                </label>
              </div>
              <div class="switch-row">
                <label class="switch-line">
                  <input v-model="positionForm.show_in_order" type="checkbox" />
                  下单页可见
                </label>
                <label v-if="positionForm.id" class="switch-line">
                  <input v-model="positionForm.active" type="checkbox" />
                  启用
                </label>
              </div>
              <p v-if="!positionForm.id" class="field-hint">新建后默认启用；被商品占用的岗位请停用，不要删。</p>
            </template>
          </div>

          <footer class="dialog-foot">
            <button type="button" class="btn" :disabled="dialogSaving" @click="closeDialog">取消</button>
            <button type="button" class="btn btn-primary" :disabled="dialogSaving" @click="saveDialog">
              {{ dialogSaving ? '保存中…' : '保存' }}
            </button>
          </footer>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="importDialog" class="scrim" @click.self="closeImportDialog">
        <div class="dialog dialog-wide" role="dialog" aria-modal="true" aria-label="导入商品目录">
          <header class="dialog-head">
            <h2 class="dialog-title">导入商品目录</h2>
            <button type="button" class="dialog-close" aria-label="关闭" @click="closeImportDialog">×</button>
          </header>

          <div class="dialog-body">
            <p class="side-note">
              先预览再写入。供应商、分类、岗位必须已经在目录里；不会按表格自动新建。
              导出文件也可以直接改完再导入。
            </p>
            <div class="form-grid">
              <label class="field">
                <span class="field-label">导入方式</span>
                <select v-model="importMode" class="select" @change="refreshImportPreview">
                  <option value="upsert">新增或更新（按商品编码）</option>
                  <option value="add">只新增（编码已存在就报错）</option>
                </select>
              </label>
              <div class="field">
                <span class="field-label">Excel 文件</span>
                <label class="btn file-btn">
                  <span class="file-name">{{ importFile ? importFile.name : '选择 .xlsx 文件' }}</span>
                  <input ref="importFileInput" class="file-input" type="file" accept=".xlsx" @change="onImportFile" />
                </label>
              </div>
              <div class="field">
                <span class="field-label">空白模板</span>
                <button type="button" class="btn" @click="downloadTemplate">下载模板</button>
              </div>
            </div>

            <p v-if="importError" class="dialog-error" role="alert">{{ importError }}</p>
            <p v-if="importLoading" class="side-note">正在读表…</p>

            <template v-else-if="importPreview">
              <div class="import-summary">
                <span>共 {{ importPreview.total }} 行</span>
                <span class="import-ok">可新建 {{ importPreview.created }}</span>
                <span class="import-ok">可更新 {{ importPreview.updated }}</span>
                <span :class="{ 'import-bad': importPreview.failed }">失败 {{ importPreview.failed }}</span>
              </div>
              <p v-if="importPreview.dry_run" class="side-note">这是预览，还没有写库。确认后只写入能通过校验的行。</p>
              <div class="import-table">
                <table>
                  <thead>
                    <tr>
                      <th>行</th>
                      <th>动作</th>
                      <th>商品编码</th>
                      <th>商品</th>
                      <th>结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in importPreview.rows.slice(0, 200)" :key="row.row">
                      <td>{{ row.row }}</td>
                      <td>{{ row.action === 'update' ? '更新' : '新建' }}</td>
                      <td>{{ row.product_code || '自动生成' }}</td>
                      <td>{{ row.name }}</td>
                      <td :class="{ 'import-bad': !row.ok }">{{ row.ok ? '可以写入' : row.error }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p v-if="importPreview.rows.length > 200" class="side-note">
                只显示前 200 行，其余行仍会按导入方式处理。
              </p>
            </template>
          </div>

          <footer class="dialog-foot">
            <button type="button" class="btn" :disabled="importApplying" @click="closeImportDialog">关闭</button>
            <button
              v-if="importPreview && importPreview.dry_run"
              type="button"
              class="btn btn-primary"
              :disabled="importApplying || importLoading || importPreview.created + importPreview.updated === 0"
              @click="applyImport"
            >
              {{ importApplying ? '导入中…' : '确认导入（' + (importPreview.created + importPreview.updated) + ' 行）' }}
            </button>
          </footer>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.catalog {
  display: flex;
  flex-direction: column;
  gap: 18px;
  width: 100%;
  min-width: 0;
  max-width: 1180px;
  margin: 0 auto;
  padding: 10px 12px 32px;
  color: var(--ink);
}

.cat-hero {
  margin-bottom: 10px;
  padding: 14px;
  border: 1px solid var(--line-strong);
  border-radius: 16px;
  background: linear-gradient(135deg, var(--surface-1), var(--wb-tint-08));
  box-shadow: var(--paper-shadow-sm);
}

.cat-hero-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.cat-refresh {
  flex: 0 0 auto;
}

.cat-hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 10px;
}
.cat-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.cat-title {
  margin: 0 0 6px;
  font-size: var(--fs-24);
  line-height: 1.2;
}

.cat-sub {
  max-width: 680px;
  margin: 0;
  color: var(--ink-dim);
  font-size: var(--fs-13);
  line-height: 1.7;
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 44px;
  padding: 0 16px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink);
  font-family: inherit;
  font-size: var(--fs-13);
  font-weight: 700;
  line-height: 1.2;
  cursor: pointer;
}

.btn:hover:not(:disabled) {
  border-color: var(--action);
  background: var(--surface-2);
}

.btn:focus-visible,
.tab:focus-visible,
.input:focus-visible,
.select:focus-visible,
.textarea:focus-visible,
.cal-day:focus-visible,
.switch-line input:focus-visible,
.pos-chip input:focus-visible,
.dialog-close:focus-visible,
.link-btn:focus-visible {
  outline: 2px solid var(--action);
  outline-offset: 2px;
}

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.btn-primary {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  box-shadow: var(--paper-shadow-sm);
}

.btn-primary:hover:not(:disabled) {
  background: var(--action);
}

.btn-danger {
  border-color: var(--wb-danger-a45);
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
}

.btn-danger:hover:not(:disabled) {
  background: var(--danger-soft);
  border-color: var(--danger-bright);
}

.btn-enable {
  border-color: var(--wb-tint-45);
  background: var(--wb-tint-08);
  color: var(--ink);
}

.icon-btn {
  min-width: 44px;
  padding: 0 10px;
}

.banner {
  display: flex;
  align-items: center;
  min-height: 44px;
  margin: 0;
  padding: 8px 14px;
  border: 1px solid var(--wb-danger-a45);
  border-radius: var(--r-1);
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
  font-size: var(--fs-13);
  line-height: 1.6;
}

.banner-soft {
  border-color: var(--wb-tint-45);
  background: var(--wb-tint-08);
  color: var(--ink);
}

.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}

.tab {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  padding: 0 18px;
  border: 1px solid transparent;
  border-radius: var(--r-1);
  background: transparent;
  color: var(--ink-dim);
  font-family: inherit;
  font-size: var(--fs-13);
  font-weight: 700;
  cursor: pointer;
}

.tab:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.tab.is-active {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  box-shadow: var(--paper-shadow-sm);
}

.tab-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}

.toolbar-filters {
  display: flex;
  flex: 1 1 420px;
  flex-wrap: wrap;
  gap: 8px;
}

.toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.toolbar-note {
  flex: 1 1 320px;
  margin: 0;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  line-height: 1.6;
}

.input,
.select,
.textarea {
  width: 100%;
  min-height: 44px;
  padding: 9px 12px;
  border: 1px solid var(--line-control);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink);
  font-family: inherit;
  font-size: var(--fs-13);
  line-height: 1.4;
}

.input::placeholder,
.textarea::placeholder {
  color: var(--ink-faint);
}

.textarea {
  min-height: 88px;
  padding-top: 10px;
  resize: vertical;
}

.search {
  flex: 1 1 200px;
}

.select {
  min-width: 150px;
}

.mono {
  font-family: var(--font-mono);
  letter-spacing: 0.06em;
}

.list-meta {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 6px;
  color: var(--ink-dim);
  font-size: var(--fs-12);
}

.empty {
  padding: 26px 16px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-2);
  background: var(--surface-1);
  color: var(--ink-dim);
  font-size: var(--fs-13);
  line-height: 1.6;
  text-align: center;
}

.product-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.product {
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}

.product.is-off {
  border-style: dashed;
  background: var(--surface-0);
}

.product-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.product-head-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.code-chip {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  padding: 4px 8px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-2);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: var(--fs-12);
  letter-spacing: 0.08em;
}

.product-name {
  margin: 0;
  font-size: var(--fs-16);
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 7px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
  color: var(--ink-dim);
  font-size: var(--fs-11);
  font-weight: 700;
}

.badge-stock {
  border-color: var(--wb-tint-45);
  background: var(--wb-tint-08);
  color: var(--ink);
}

.badge-cycle {
  border-color: var(--wb-warn-a45);
  background: var(--wb-warn-a10);
  color: var(--warn);
}

.badge-off {
  border-color: var(--wb-danger-a45);
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
}

.product-spec,
.product-remark {
  margin: 0;
  font-size: var(--fs-12);
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.product-spec {
  color: var(--ink);
}

.product-remark {
  color: var(--ink-dim);
}

.product-fields {
  display: flex;
  flex-direction: column;
  border-top: 1px dashed var(--line);
}

.product-fields > div {
  display: grid;
  grid-template-columns: 68px minmax(0, 1fr);
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--line);
  font-size: var(--fs-12);
  line-height: 1.5;
}

.product-fields > div:last-child {
  border-bottom: 0;
}

.product-fields span {
  color: var(--ink-faint);
}

.product-fields strong {
  min-width: 0;
  overflow-wrap: anywhere;
  font-weight: 500;
}

.product-field-warn {
  color: var(--warn);
}

.product-field-remark strong {
  color: var(--warn);
}
.product-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px 12px;
  margin: 0;
}

.product-meta div {
  display: flex;
  gap: 6px;
  min-width: 0;
  font-size: var(--fs-12);
  line-height: 1.5;
}

.product-meta dt {
  flex: 0 0 auto;
  margin: 0;
  color: var(--ink-faint);
}

.product-meta dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.product-positions {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.label-mini {
  color: var(--ink-faint);
  font-size: var(--fs-11);
  font-weight: 700;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
  color: var(--ink-dim);
  font-size: var(--fs-11);
  font-weight: 600;
}

.chip.is-off {
  border-style: dashed;
  color: var(--ink-faint);
}

.chip.is-warn {
  border-color: var(--wb-warn-a45);
  background: var(--wb-warn-a10);
  color: var(--warn);
}

.product-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin: 6px -16px -16px;
  border-top: 1px solid var(--line);
}
.product-actions .btn {
  min-height: 44px;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.product-actions .btn + .btn {
  border-left: 1px solid var(--line);
}
.product-actions .btn-danger,
.product-actions .btn-enable {
  background: transparent;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.pager-info {
  color: var(--ink-dim);
  font-size: var(--fs-12);
}

.supplier-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.supplier {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}

.supplier.is-off {
  border-style: dashed;
  background: var(--surface-0);
}

.supplier-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 10px 12px;
}

.supplier-main {
  flex: 1 1 220px;
  min-width: 0;
}

.supplier-name {
  margin: 0 0 4px;
  font-size: var(--fs-16);
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.supplier-meta {
  margin: 0;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  line-height: 1.5;
}

.row-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-left: auto;
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--ink-dim);
  font-size: var(--fs-11);
  font-weight: 700;
  white-space: nowrap;
}

.pill-on {
  border-color: var(--wb-tint-45);
  background: var(--action-soft);
  color: var(--ink);
}

.pill-off {
  border-color: var(--wb-danger-a45);
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
}

.pill-off .dot {
  background: var(--danger);
}

.category-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.category-empty {
  margin: 0;
  padding: 10px 12px;
  border: 1px dashed var(--line);
  border-radius: var(--r-1);
  color: var(--ink-faint);
  font-size: var(--fs-12);
}

.category-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-0);
}

.category-row.is-off {
  border-style: dashed;
}

.category-name {
  font-size: var(--fs-13);
  font-weight: 700;
  overflow-wrap: anywhere;
}

.muted {
  color: var(--ink-dim);
  font-size: var(--fs-12);
}

.pos-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
}

.pos-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-1);
}

.pos-row.is-off {
  border-style: dashed;
  background: var(--surface-0);
}

.pos-order {
  width: 18px;
  color: var(--ink-faint);
  font-family: var(--font-mono);
  font-size: var(--fs-12);
  text-align: right;
}

.pos-main {
  display: flex;
  flex: 1 1 180px;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.pos-name {
  font-size: var(--fs-13);
  overflow-wrap: anywhere;
}

.pos-meta {
  color: var(--ink-dim);
  font-size: var(--fs-11);
}

.pos-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-left: auto;
}

.switch-line {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 0 4px;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  font-weight: 600;
  cursor: pointer;
}

.switch-line input,
.pos-chip input {
  flex: 0 0 18px;
  width: 18px;
  height: 18px;
  margin: 0;
  accent-color: var(--action);
}

.switch-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
}

.date-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(300px, 1fr);
  align-items: start;
  gap: 16px;
}

.calendar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
}

.cal-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.cal-nav {
  display: flex;
  align-items: center;
  gap: 8px;
}

.cal-month {
  min-width: 108px;
  font-size: var(--fs-16);
  text-align: center;
}

.cal-weekdays,
.cal-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 6px;
}

.cal-weekday {
  padding: 4px 0;
  color: var(--ink-dim);
  font-size: var(--fs-11);
  font-weight: 700;
  text-align: center;
}

.cal-day {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-height: 68px;
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink);
  font-family: inherit;
  font-size: var(--fs-12);
  text-align: left;
  cursor: pointer;
}

.cal-day:hover:not(:disabled) {
  border-color: var(--line-strong);
  background: var(--surface-2);
}

.cal-day.is-out {
  border-color: transparent;
  background: transparent;
  color: var(--ink-faint);
  cursor: default;
}

.cal-day.is-today {
  box-shadow: inset 0 0 0 1px var(--line-strong);
}

.cal-day.is-selected {
  border-color: var(--action);
  background: var(--action-soft);
  box-shadow: inset 0 0 0 1px var(--action);
}

.cal-day:disabled {
  opacity: 0.55;
}

.cal-day-num {
  font-family: var(--font-mono);
  font-size: var(--fs-13);
  font-weight: 700;
}

.cal-tag {
  padding: 2px 6px;
  border-radius: var(--r-1);
  background: var(--action);
  color: var(--action-ink);
  font-size: var(--fs-11);
  font-weight: 700;
}

.cal-default {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}

.cal-legend {
  margin: 0;
  color: var(--ink-faint);
  font-size: var(--fs-11);
  line-height: 1.6;
}

.date-side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.side-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
}

.side-title {
  margin: 0;
  font-size: var(--fs-14);
  line-height: 1.4;
}

.side-note {
  margin: 0;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  line-height: 1.6;
}

.btn-col {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.field-label {
  color: var(--ink-dim);
  font-size: var(--fs-12);
  font-weight: 700;
}

.field-hint {
  margin: 0;
  color: var(--ink-faint);
  font-size: var(--fs-11);
  line-height: 1.55;
}

.wd-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 10px;
}

.wd-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.wd-row > span {
  flex: 0 0 44px;
  color: var(--ink-dim);
  font-size: var(--fs-12);
}

.wd-row .select {
  min-width: 0;
}

.tag-list-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
}

.tag-list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.tag-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 2px 0;
  border-bottom: 1px dashed var(--line);
}

.tag-item:last-child {
  border-bottom: 0;
}

.tag-date {
  font-family: var(--font-mono);
  font-size: var(--fs-12);
}

.tag-weekday {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}

.tag-value {
  padding: 2px 8px;
  border-radius: var(--r-1);
  background: var(--action);
  color: var(--action-ink);
  font-size: var(--fs-11);
  font-weight: 700;
}

.link-btn {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: var(--r-1);
  background: transparent;
  color: var(--danger-bright);
  font-family: inherit;
  font-size: var(--fs-12);
  font-weight: 700;
  cursor: pointer;
}

.link-btn:hover:not(:disabled) {
  background: var(--wb-danger-a10);
}

.link-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.scrim {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 24px 16px;
  overflow-y: auto;
  background: var(--wb-scrim-strong);
}

.dialog {
  display: flex;
  flex-direction: column;
  width: min(720px, 100%);
  max-height: calc(100vh - 48px);
  margin: auto;
  overflow: hidden;
  border: 2px solid var(--line-strong);
  border-radius: var(--r-3);
  background: var(--surface-1);
  box-shadow: var(--paper-shadow);
}

.dialog-wide {
  width: min(900px, 100%);
}

.dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--line);
}

.dialog-title {
  margin: 0;
  font-size: var(--fs-20);
  line-height: 1.3;
}

.dialog-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--r-1);
  background: transparent;
  color: var(--ink-dim);
  font-family: inherit;
  font-size: var(--fs-20);
  cursor: pointer;
}

.dialog-close:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.dialog-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 18px;
  overflow-y: auto;
}

.dialog-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 18px;
  border-top: 1px solid var(--line);
  background: var(--surface-1);
}

.dialog-error {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--wb-danger-a45);
  border-radius: var(--r-1);
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
  font-size: var(--fs-12);
  line-height: 1.5;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.field-full {
  grid-column: 1 / -1;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.form-section:first-child {
  padding-top: 0;
  border-top: 0;
}

.form-section-title {
  margin: 0;
  font-size: var(--fs-14);
  line-height: 1.4;
}

.code-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.code-preview {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 12px;
  padding: 10px 12px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-0);
}

.code-preview-label {
  color: var(--ink-dim);
  font-size: var(--fs-11);
  font-weight: 700;
}

.code-preview-value {
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: var(--fs-20);
  letter-spacing: 0.08em;
}

.code-preview-hint {
  flex: 1 1 100%;
  margin: 0;
  color: var(--ink-faint);
  font-size: var(--fs-11);
  line-height: 1.5;
}

.pos-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pos-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink);
  font-size: var(--fs-12);
  cursor: pointer;
}

.pos-chip.is-on {
  border-color: var(--action);
  background: var(--action-soft);
}

.chip-note {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}

.import-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  font-size: var(--fs-12);
}

.import-ok {
  color: var(--ok);
}

.import-bad {
  color: var(--danger-bright);
}

.import-table {
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
}

.import-table table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-12);
}

.import-table th,
.import-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}

.import-table th {
  background: var(--surface-2);
  color: var(--ink-dim);
  font-size: var(--fs-11);
  white-space: nowrap;
}

.import-table tr:last-child td {
  border-bottom: 0;
}

.file-btn {
  max-width: 100%;
  overflow: hidden;
}

.file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  border: 0;
}

/* 小程序式目录头：橙色实底 + 深墨字，和 luyun 的工作台主色一致。 */
.cat-hero {
  padding: 16px;
  border: 1px solid var(--action);
  border-radius: 16px;
  background: var(--action);
  color: var(--action-ink);
  box-shadow: var(--paper-shadow-sm);
}

.cat-hero-copy {
  min-width: 0;
}

.cat-eyebrow {
  margin: 0 0 3px;
  color: var(--action-ink);
  font-size: var(--fs-11);
  font-weight: 700;
  letter-spacing: 0.08em;
  opacity: 0.72;
}

.cat-title {
  margin: 0 0 4px;
  color: var(--action-ink);
  font-size: var(--fs-24);
}

.cat-sub {
  max-width: none;
  color: var(--action-ink);
  font-size: var(--fs-12);
  line-height: 1.5;
  opacity: 0.76;
}

.cat-pos-link {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--surface-1);
  border-radius: 999px;
  background: var(--surface-1);
  color: var(--action-ink);
  font-family: inherit;
  font-size: var(--fs-13);
  font-weight: 700;
  cursor: pointer;
}

.cat-pos-link:hover {
  background: var(--surface-0);
}

.cat-hero-actions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
}

.cat-hero-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  padding: 0 8px;
  border: 1px solid var(--surface-1);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--action-ink);
  font-family: inherit;
  font-size: var(--fs-13);
  font-weight: 700;
  line-height: 1.2;
  cursor: pointer;
}

.cat-hero-btn:hover {
  background: var(--surface-0);
}

.cat-pos-link:focus-visible,
.cat-hero-btn:focus-visible,
.filter-chip:focus-visible,
.filter-clear:focus-visible,
.meta-refresh:focus-visible,
.product-fab:focus-visible {
  outline: 2px solid var(--action-ink);
  outline-offset: 2px;
}

.tabs {
  flex-wrap: nowrap;
  gap: 0;
  padding: 0;
  overflow: hidden;
  border-radius: var(--r-2);
}

.tab {
  flex: 1 1 0;
  min-width: 0;
  min-height: 48px;
  padding: 0 6px;
  border: 0;
  border-bottom: 3px solid transparent;
  border-radius: 0;
  background: transparent;
  font-size: var(--fs-13);
  white-space: nowrap;
}

.tab:hover {
  background: var(--surface-0);
}

.tab.is-active {
  border: 0;
  border-bottom: 3px solid var(--action);
  background: transparent;
  color: var(--action);
  box-shadow: none;
}

.product-tools {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}

.search-box {
  position: relative;
}

.product-search {
  padding-right: 44px;
  background: var(--surface-0);
}

.search-icon {
  position: absolute;
  top: 50%;
  right: 13px;
  width: 18px;
  height: 18px;
  transform: translateY(-50%);
  stroke: var(--ink-faint);
  stroke-width: 2;
  stroke-linecap: round;
  pointer-events: none;
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.filter-chip {
  min-height: 44px;
  max-width: 100%;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: var(--r-1);
  background: var(--surface-2);
  color: var(--ink-dim);
  font-family: inherit;
  font-size: var(--fs-12);
  font-weight: 600;
}

.filter-chip.is-on {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
}

.filter-clear {
  min-height: 44px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--r-1);
  background: transparent;
  color: var(--danger-bright);
  font-family: inherit;
  font-size: var(--fs-12);
  font-weight: 700;
  cursor: pointer;
}

.filter-clear:hover {
  background: var(--wb-danger-a10);
}

.product-add-desktop {
  margin-left: auto;
}

.list-meta-right {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.meta-refresh {
  min-height: 44px;
  padding: 0 6px;
  border: 0;
  background: transparent;
  color: var(--ink-dim);
  font-family: inherit;
  font-size: var(--fs-11);
  font-weight: 700;
  text-decoration: underline;
  cursor: pointer;
}

.meta-refresh:hover {
  color: var(--action);
}

.product {
  gap: 0;
  padding: 0;
}

.product-head {
  align-items: center;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-0);
}

.product-head-main {
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
}

.product-name {
  flex: 0 1 auto;
  min-width: 0;
}

.product-head .badges {
  flex: 0 0 auto;
  justify-content: flex-end;
  margin-left: auto;
}

.product-fields {
  padding: 3px 14px 7px;
  border-top: 0;
}

.product-fields > div {
  grid-template-columns: 56px minmax(0, 1fr);
  padding: 7px 0;
}

.product-actions {
  margin: 0;
}

.supplier {
  gap: 0;
  padding: 14px;
}

.supplier-head {
  align-items: center;
}

.supplier-main {
  flex: 1 1 160px;
}

.supplier-name {
  margin: 0 0 2px;
}

.category-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.category-card {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}

.category-card.is-off {
  border-style: dashed;
  background: var(--surface-0);
}

.category-card-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 12px;
  padding: 12px 14px;
}

.category-card-main {
  flex: 1 1 160px;
  min-width: 0;
}

.category-card-name {
  margin: 0;
  font-size: var(--fs-14);
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.category-card-meta {
  margin: 2px 0 0;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  line-height: 1.5;
}

.subpage-head {
  display: flex;
  align-items: center;
  gap: 10px;
}


.product-fab {
  display: none;
}

@media (max-width: 1024px) {
  .date-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .product-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .cat-hero {
    padding: 14px;
  }

  .toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .toolbar-note {
    flex: 0 0 auto;
  }

  .toolbar-actions {
    width: 100%;
  }

  .cat-hero-actions {
    gap: 6px;
    margin-top: 12px;
  }

  .cat-hero-btn {
    padding: 0 4px;
    font-size: var(--fs-12);
  }

  .cat-pos-link {
    padding: 0 10px;
    font-size: var(--fs-12);
  }

  .product-tools {
    padding: 10px;
  }

  .product-add-desktop {
    display: none !important;
  }

  .products-panel {
    padding-bottom: 142px;
  }

  .product-fab {
    position: fixed;
    bottom: calc(68px + var(--safe-b));
    left: 50%;
    z-index: var(--z-shell);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 200px;
    max-width: calc(100vw - 48px);
    min-height: 50px;
    padding: 0 24px;
    transform: translateX(-50%);
    border: 0;
    border-radius: 999px;
    background: var(--action);
    color: var(--action-ink);
    box-shadow: var(--paper-shadow);
    font-family: inherit;
    font-size: var(--fs-14);
    font-weight: 700;
    cursor: pointer;
  }

  .product-head {
    padding: 10px 12px;
  }

  .product-fields {
    padding: 2px 12px 6px;
  }

  .category-card-head {
    padding: 12px;
  }

  .toolbar-filters {
    flex: 0 0 auto;
    flex-direction: column;
  }

  .toolbar-filters .search,
  .toolbar-filters .select {
    flex: 0 0 auto;
    width: 100%;
  }

  .form-grid {
    grid-template-columns: 1fr;
  }

  .field-full {
    grid-column: auto;
  }
}

@media (max-width: 560px) {
  .catalog {
    gap: 14px;
  }

  .tabs {
    gap: 0;
    padding: 0;
  }

  .tab {
    flex: 1 1 0;
    padding: 0 4px;
    font-size: var(--fs-11);
  }

  .filter-row {
    gap: 6px;
  }

  .filter-chip {
    flex: 1 1 120px;
    min-width: 0;
  }

  .product-head .badges {
    margin-left: 0;
  }

  .list-meta-right {
    width: 100%;
    justify-content: space-between;
  }

  .product-meta {
    grid-template-columns: 1fr;
  }

  .calendar {
    padding: 12px;
  }

  .cal-weekdays,
  .cal-grid {
    gap: 4px;
  }

  .cal-day {
    min-height: 60px;
    padding: 5px;
  }

  .cal-month {
    min-width: 0;
  }

  .scrim {
    padding: 12px 8px;
  }

  .dialog {
    max-height: calc(100vh - 24px);
  }

  .dialog-body {
    padding: 14px;
  }

  .wd-grid {
    grid-template-columns: 1fr;
  }

  .row-actions,
  .pos-actions {
    margin-left: 0;
  }
}

@media (max-width: 430px) {
  .cat-title {
    font-size: var(--fs-20);
  }

  .btn {
    padding: 0 12px;
  }

  .cal-day {
    min-height: 56px;
  }
}
</style>
