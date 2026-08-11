<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api/client'
import SvgIcon from '../SvgIcon.vue'

const emit = defineEmits(['close'])

const fixedDishes = ref([]) // {id, dish_name, notes}
const allDishNames = ref([])
const searchKeyword = ref('')
const selectedDish = ref('')
const dragIndex = ref(null)
const errorMsg = ref('')

onMounted(load)

async function load() {
  try {
    const [dishesRes, namesRes] = await Promise.all([
      api.get('/api/report-dishes/'),
      api.get('/api/semi-rules/dishes/available'),
    ])
    fixedDishes.value = dishesRes.dishes || []
    allDishNames.value = namesRes.dishes || []
  } catch (e) {
    errorMsg.value = e.message || '加载失败'
  }
}

function availableOptions() {
  const existing = new Set(fixedDishes.value.map((d) => d.dish_name))
  const kw = searchKeyword.value.trim().toLowerCase()
  return allDishNames.value.filter((n) => !existing.has(n) && (!kw || n.toLowerCase().includes(kw)))
}

async function addSelected() {
  if (!selectedDish.value) return
  const res = await api.post('/api/report-dishes/', { dish_name: selectedDish.value, notes: '' })
  fixedDishes.value.push({ id: res.id, dish_name: selectedDish.value, notes: '' })
  selectedDish.value = ''
}

async function removeDish(id) {
  await api.delete(`/api/report-dishes/${id}`)
  fixedDishes.value = fixedDishes.value.filter((d) => d.id !== id)
}

async function saveReorder() {
  await api.put('/api/report-dishes/reorder', { ids: fixedDishes.value.map((d) => d.id) })
}

function onDragStart(idx) {
  dragIndex.value = idx
}
function onDragOver(idx, e) {
  e.preventDefault()
}
function onDrop(idx) {
  if (dragIndex.value === null || dragIndex.value === idx) return
  const list = [...fixedDishes.value]
  const [moved] = list.splice(dragIndex.value, 1)
  list.splice(idx, 0, moved)
  fixedDishes.value = list
  dragIndex.value = null
  saveReorder()
}
</script>


<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-box" style="width:500px">
      <div class="modal-header">
        <h3><SvgIcon name="settings" :size="15" /> 固定报表菜品设置</h3>
        <button class="btn btn-sm" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>
      <p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">
        文字版导出将仅输出以下菜品（拖拽可调整顺序），未售出显示 0
      </p>
      <input class="input" v-model="searchKeyword" placeholder="搜索菜品名称..." style="width:100%;margin-bottom:8px">
      <div style="display:flex;gap:8px;margin-bottom:10px">
        <select class="select" v-model="selectedDish" style="flex:1">
          <option value="">— 从菜品库选择添加 —</option>
          <option v-for="n in availableOptions()" :key="n" :value="n">{{ n }}</option>
        </select>
        <button class="btn btn-primary" @click="addSelected">添加</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:4px;max-height:300px;overflow-y:auto" class="luyun-scrollbar">
        <div v-if="!fixedDishes.length" class="empty-state">暂未添加固定菜品，请从上方选择添加</div>
        <div
          v-for="(d, idx) in fixedDishes"
          :key="d.id"
          draggable="true"
          @dragstart="onDragStart(idx)"
          @dragover="onDragOver(idx, $event)"
          @drop="onDrop(idx)"
          style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:var(--card2);border-radius:6px;font-size:13px;cursor:grab"
        >
          <span style="color:var(--text-dim);display:inline-flex"><SvgIcon name="menu" :size="12" /></span>
          <span style="flex:1">{{ d.dish_name }}</span>
          <span v-if="d.notes" style="color:var(--text-dim);font-size:12px">{{ d.notes }}</span>
          <button class="btn btn-sm btn-danger" @click="removeDish(d.id)">删除</button>
        </div>
      </div>
      <div v-if="errorMsg" style="color:var(--red);font-size:12px;margin-top:8px">{{ errorMsg }}</div>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>
