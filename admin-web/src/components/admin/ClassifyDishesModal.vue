<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useStationsStore } from '../../stores/stations'
import SvgIcon from '../SvgIcon.vue'

const emit = defineEmits(['close', 'saved'])
const stationsStore = useStationsStore()

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const dishes = ref([]) // [{ name, isNew }]
const assignments = ref({}) // name -> stationId
const bulkStation = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/api/admin/unmapped-dishes')
    const unmapped = (data.dishes || data.unmapped || []).map((name) => ({ name, isNew: true }))
    const existing = (data.existing_mappings || data.existing || []).map((name) => ({ name, isNew: false }))
    dishes.value = [...unmapped, ...existing]
  } catch (e) {
    error.value = e.message || '加载未分类菜品失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

function assign(name, stationId) {
  if (stationId) assignments.value[name] = stationId
  else delete assignments.value[name]
}

function applyBulk() {
  if (!bulkStation.value) return
  for (const d of dishes.value) assign(d.name, bulkStation.value)
}

function fillDown(idx) {
  const station = assignments.value[dishes.value[idx].name]
  if (!station) return
  for (let i = idx + 1; i < dishes.value.length; i++) {
    if (!assignments.value[dishes.value[i].name]) assign(dishes.value[i].name, station)
  }
}

async function saveAll() {
  const entries = Object.entries(assignments.value).filter(([, v]) => v)
  if (!entries.length) {
    error.value = '请先选择至少一个档口'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.post('/api/dish-stations/batch', {
      mappings: entries.map(([dish_name, station_id]) => ({ dish_name, station_id, notes: '批量分类' })),
    })
    emit('saved', entries.length)
    emit('close')
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-box" style="width:min(680px, 100%)">
      <div class="modal-header">
        <h3>批量分类菜品</h3>
        <button class="btn btn-sm" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>

      <div v-if="loading" class="loading-state">加载中...</div>
      <div v-else-if="error && !dishes.length" class="empty-state">{{ error }}</div>
      <div v-else-if="!dishes.length" class="empty-state"><SvgIcon name="sparkles" :size="14" /> 所有菜品已分类完毕！</div>
      <template v-else>
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap">
          <span style="font-size:11px;color:var(--text-dim)">共 <b style="color:var(--text)">{{ dishes.length }}</b> 个菜品</span>
          <div style="margin-left:auto;display:flex;gap:6px">
            <select class="select" v-model="bulkStation">
              <option value="">— 批量选档口 —</option>
              <option v-for="s in stationsStore.list" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
            <button class="btn btn-primary btn-sm" @click="applyBulk">批量应用</button>
          </div>
        </div>

        <div style="max-height:420px;overflow-y:auto;border:1px solid var(--border);border-radius:8px" class="luyun-scrollbar">
          <table style="width:100%;border-collapse:collapse">
            <tbody>
              <tr
                v-for="(d, idx) in dishes"
                :key="d.name"
                style="border-bottom:1px solid var(--border)"
              >
                <td style="width:14px;padding:6px 4px 6px 10px">
                  <span
                    style="display:inline-block;width:7px;height:7px;border-radius:50%"
                    :style="{ background: assignments[d.name] ? 'var(--green)' : 'transparent', opacity: assignments[d.name] ? 1 : 0.3 }"
                  ></span>
                </td>
                <td style="width:40px;padding:6px 4px">
                  <span class="badge" :style="d.isNew ? 'color:var(--green);border-color:var(--green)' : ''">{{ d.isNew ? '新' : '已有' }}</span>
                </td>
                <td style="padding:6px 4px;font-size:12px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" :title="d.name">{{ d.name }}</td>
                <td style="width:120px;padding:6px 4px">
                  <select
                    class="select"
                    style="width:100%;font-size:11px"
                    :value="assignments[d.name] || ''"
                    @change="assign(d.name, $event.target.value)"
                  >
                    <option value="">— 选择档口 —</option>
                    <option v-for="s in stationsStore.list" :key="s.id" :value="s.id">{{ s.name }}</option>
                  </select>
                </td>
                <td style="width:32px;padding:6px 10px 6px 4px;text-align:right">
                  <button class="btn btn-sm" title="向下填充" @click="fillDown(idx)"><SvgIcon name="chevron-down" :size="12" /></button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="error" style="color:var(--red);font-size:12px;margin-top:8px">{{ error }}</div>
        <div class="modal-footer">
          <button class="btn" @click="emit('close')">取消</button>
          <button class="btn btn-primary" :disabled="saving" @click="saveAll">{{ saving ? '保存中...' : '保存全部' }}</button>
        </div>
      </template>
    </div>
  </div>
</template>
