<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useStationsStore } from '../../stores/stations'
import SvgIcon from '../SvgIcon.vue'

const BATCH_IMPORT_NOTES = '快捷导入'
const EXISTING_DISHES_PAGE_SIZE = 500
const BATCH_PREVIEW_LIMIT = 30

const emit = defineEmits(['close', 'added'])
const stationsStore = useStationsStore()

const loadingExisting = ref(true)
const existingNames = ref(new Set())

const batchStation = ref('')
const batchText = ref('')
const batchBusy = ref(false)
const batchMsg = ref('')
const batchError = ref('')

const singleName = ref('')
const singleStation = ref('')
const singleNotes = ref('')
const singleBusy = ref(false)
const singleMsg = ref('')
const singleError = ref('')

async function loadExisting() {
  loadingExisting.value = true
  try {
    const data = await api.get('/api/admin/tables/dish_stations/rows', { page_size: EXISTING_DISHES_PAGE_SIZE })
    existingNames.value = new Set(
      (data.rows || []).map((r) => String(r.dish_name ?? '').trim()).filter(Boolean),
    )
  } catch (e) {
    batchError.value = e.message || '加载已有菜品失败'
  } finally {
    loadingExisting.value = false
  }
}

onMounted(loadExisting)

const batchLines = computed(() =>
  batchText.value.split('\n').map((l) => l.trim()).filter((l) => l.length > 0),
)
const batchNewLines = computed(() => batchLines.value.filter((l) => !existingNames.value.has(l)))
const batchDupLines = computed(() => batchLines.value.filter((l) => existingNames.value.has(l)))
const batchCanSubmit = computed(
  () => !!batchStation.value && batchNewLines.value.length > 0 && !batchBusy.value,
)

async function submitBatch() {
  if (!batchStation.value) {
    batchError.value = '请选择档口'
    return
  }
  const names = batchNewLines.value
  if (!names.length) {
    batchError.value = '没有新增菜品'
    return
  }
  batchBusy.value = true
  batchError.value = ''
  batchMsg.value = ''
  try {
    const res = await api.post('/api/dish-stations/batch', {
      mappings: names.map((dish_name) => ({
        dish_name,
        station_id: batchStation.value,
        notes: BATCH_IMPORT_NOTES,
      })),
    })
    const created = res.data?.created_count ?? 0
    const errors = res.data?.errors ?? []
    batchMsg.value = `新增 ${created} 个${errors.length ? `，失败 ${errors.length} 个` : ''}`
    names.forEach((n) => existingNames.value.add(n))
    batchText.value = ''
    emit('added')
  } catch (e) {
    batchError.value = e.message || '批量添加失败'
  } finally {
    batchBusy.value = false
  }
}

async function submitSingle() {
  const name = singleName.value.trim()
  if (!name) {
    singleError.value = '请输入菜品名称'
    return
  }
  if (!singleStation.value) {
    singleError.value = '请选择档口'
    return
  }
  singleBusy.value = true
  singleError.value = ''
  singleMsg.value = ''
  try {
    await api.post('/api/dish-stations/', {
      dish_name: name,
      station_id: singleStation.value,
      notes: singleNotes.value.trim() || null,
    })
    singleMsg.value = `${name} 添加成功`
    existingNames.value.add(name)
    singleName.value = ''
    singleNotes.value = ''
    emit('added')
  } catch (e) {
    singleError.value = e.message || '添加失败，可能已存在'
  } finally {
    singleBusy.value = false
  }
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-box" style="width: min(560px, 100%)">
      <div class="modal-header">
        <h3>快捷添加菜品</h3>
        <button class="btn btn-sm" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>

      <div style="background: var(--card2); border-radius: 8px; padding: 14px; margin-bottom: 14px">
        <div class="panel-title" style="font-size: 12px">批量添加（一行一个菜名）</div>
        <div class="form-row">
          <label>目标档口 *</label>
          <select class="select" v-model="batchStation" style="width: 100%">
            <option value="">— 选择档口 —</option>
            <option v-for="s in stationsStore.list" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </div>
        <div class="form-row">
          <label>菜名列表</label>
          <textarea
            class="input"
            rows="6"
            v-model="batchText"
            :disabled="loadingExisting"
            placeholder="金牌LuckIn虾饺皇&#10;水晶鲜虾香茜饺&#10;金蒜好味蒸排骨"
            style="resize: vertical; font-family: inherit; line-height: 1.7"
          ></textarea>
        </div>
        <div v-if="batchLines.length" style="font-size: 11px; color: var(--text-dim); margin-bottom: 6px">
          共 {{ batchLines.length }} 行 | 新增 {{ batchNewLines.length }} | 已有 {{ batchDupLines.length }}
        </div>
        <div v-if="batchNewLines.length" style="max-height: 120px; overflow-y: auto; margin-bottom: 8px" class="luyun-scrollbar">
          <span
            v-for="d in batchNewLines.slice(0, BATCH_PREVIEW_LIMIT)"
            :key="d"
            class="badge"
            style="color: var(--green); border-color: var(--green); margin: 2px"
          >{{ d }}</span>
          <span v-if="batchNewLines.length > BATCH_PREVIEW_LIMIT" class="badge" style="margin: 2px">
            +{{ batchNewLines.length - BATCH_PREVIEW_LIMIT }}
          </span>
        </div>
        <div style="display: flex; justify-content: flex-end">
          <button class="btn btn-primary" :disabled="!batchCanSubmit" @click="submitBatch">
            {{ batchBusy ? '导入中...' : '批量导入' }}
          </button>
        </div>
        <div v-if="batchMsg" style="color: var(--green); font-size: 12px; margin-top: 6px">{{ batchMsg }}</div>
        <div v-if="batchError" style="color: var(--red); font-size: 12px; margin-top: 6px">{{ batchError }}</div>
      </div>

      <div style="text-align: center; color: var(--text-dim); font-size: 11px; margin-bottom: 12px">— 或单个添加 —</div>

      <div style="background: var(--card2); border-radius: 8px; padding: 14px">
        <div class="form-row">
          <label>菜品名称 *</label>
          <input class="input" v-model="singleName" placeholder="如：金莎脆皮红米肠" style="width: 100%" />
        </div>
        <div class="form-row">
          <label>档口 *</label>
          <select class="select" v-model="singleStation" style="width: 100%">
            <option value="">— 选择档口 —</option>
            <option v-for="s in stationsStore.list" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </div>
        <div class="form-row">
          <label>备注</label>
          <input class="input" v-model="singleNotes" placeholder="如：新品上架" style="width: 100%" />
        </div>
        <div style="display: flex; justify-content: flex-end">
          <button class="btn btn-primary" :disabled="singleBusy" @click="submitSingle">
            {{ singleBusy ? '添加中...' : '添加' }}
          </button>
        </div>
        <div v-if="singleMsg" style="color: var(--green); font-size: 12px; margin-top: 6px">{{ singleMsg }}</div>
        <div v-if="singleError" style="color: var(--red); font-size: 12px; margin-top: 6px">{{ singleError }}</div>
      </div>
    </div>
  </div>
</template>
