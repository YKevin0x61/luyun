<script setup>
import { computed, reactive, ref } from 'vue'
import { useStationsStore } from '../../stores/stations'
import { useSemiRules } from '../../composables/useSemiRules'
import { useNudgePull } from '../../composables/useNudgePull'
import RuleFormModal from './RuleFormModal.vue'

const REALTIME_DEBOUNCE_MS = 500

const stationsStore = useStationsStore()
const { groupedData, loading, loadRules, findRuleById, deleteRule, createRule, updateRule } = useSemiRules()

const searchQuery = ref('')
const openStations = reactive(new Set())
const ruleModal = ref(null) // { dishName, initial } | null

useNudgePull({
  id: 'sales-report-rules-admin',
  topics: ['admin'],
  pull: loadRules,
  debounceMs: REALTIME_DEBOUNCE_MS,
  immediate: true,
})

const filtered = computed(() => {
  if (!groupedData.value) return {}
  const kw = searchQuery.value.trim().toLowerCase()
  const out = {}
  for (const [station, dishes] of Object.entries(groupedData.value)) {
    if (station === 'loumian') continue
    const matched = dishes.filter((d) => !kw || String(d.dish_name || '').toLowerCase().includes(kw))
    if (matched.length) out[station] = matched
  }
  return out
})

const totalDishes = computed(() => Object.values(filtered.value).reduce((s, arr) => s + arr.length, 0))
const totalWithRules = computed(() => Object.values(filtered.value).reduce((s, arr) => s + arr.filter((x) => x.has_rules).length, 0))
const sortedStations = computed(() => Object.keys(filtered.value).sort())

function toggleStation(station) {
  if (openStations.has(station)) openStations.delete(station)
  else openStations.add(station)
}

function openAdd(dishName) {
  ruleModal.value = { dishName, initial: null }
}
function openEdit(ruleId) {
  const rule = findRuleById(ruleId)
  if (rule) ruleModal.value = { dishName: rule.dish_name, initial: rule }
}
async function submitRuleForm(payload) {
  if (ruleModal.value.initial) {
    await updateRule(ruleModal.value.initial.id, payload)
  } else {
    await createRule({ dish_name: ruleModal.value.dishName, ...payload })
  }
  ruleModal.value = null
}

async function onDeleteRule(ruleId) {
  if (!window.confirm('确定删除该规则？')) return
  await deleteRule(ruleId)
}
</script>

<template>
  <div>
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap">
      <input class="input" v-model="searchQuery" placeholder="搜索菜品名称..." style="flex:1;max-width:300px">
      <span style="font-size:13px;color:var(--text-dim)">{{ totalDishes }} 道菜 · {{ totalWithRules }} 道已有换算规则</span>
    </div>

    <div v-if="loading" class="loading-state">加载中...</div>
    <div v-else-if="!sortedStations.length" class="empty-state">没有匹配的菜品</div>
    <div v-else style="display:flex;flex-direction:column;gap:10px">
      <div v-for="station in sortedStations" :key="station" class="card" style="padding:0;overflow:hidden">
        <div
          style="padding:10px 14px;cursor:pointer;display:flex;align-items:center;gap:10px;background:var(--card2)"
          :style="{ borderLeft: `3px solid ${stationsStore.colorOf(station)}` }"
          @click="toggleStation(station)"
        >
          <span style="width:10px;height:10px;border-radius:50%" :style="{ background: stationsStore.colorOf(station) }"></span>
          <span style="flex:1;font-weight:600;font-size:14px">{{ stationsStore.nameOf(station) }}</span>
          <span style="font-size:12px">
            <span v-if="filtered[station].filter((d) => d.has_rules).length" style="color:var(--green)">
              {{ filtered[station].filter((d) => d.has_rules).length }}条规则
            </span>
            <span v-if="filtered[station].filter((d) => !d.has_rules).length" style="color:var(--text-dim)">
              {{ filtered[station].filter((d) => d.has_rules).length ? ' · ' : '' }}{{ filtered[station].filter((d) => !d.has_rules).length }}道待添加
            </span>
          </span>
          <span style="color:var(--text-dim)">{{ openStations.has(station) ? '▾' : '▸' }}</span>
        </div>
        <div v-show="openStations.has(station)" style="padding:8px 0">
          <template v-for="dish in filtered[station].filter((d) => d.has_rules)" :key="dish.dish_name">
            <div style="padding:8px 14px;border-bottom:1px solid var(--border)">
              <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px">
                <span style="font-size:13px">{{ dish.dish_name }}</span>
                <button class="btn btn-sm" @click="openAdd(dish.dish_name)">+ 添加换算</button>
              </div>
              <div v-for="r in dish.rules" :key="r.id" style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12px">
                <span style="color:var(--text-dim)">├</span>
                <span style="min-width:80px">{{ r.semi_name }}</span>
                <span class="badge">{{ r.position }}</span>
                <span v-if="r.category" class="badge" style="color:#818cf8">{{ r.category }}</span>
                <span style="color:var(--accent)">× {{ r.factor }} {{ r.unit }}</span>
                <button class="btn btn-sm" style="margin-left:auto" @click="openEdit(r.id)">编辑</button>
                <button class="btn btn-sm btn-danger" @click="onDeleteRule(r.id)">删除</button>
              </div>
            </div>
          </template>
          <template v-if="filtered[station].filter((d) => !d.has_rules).length">
            <div style="padding:8px 14px 4px;font-size:12px;color:var(--text-dim)">待添加换算规则</div>
            <div
              v-for="dish in filtered[station].filter((d) => !d.has_rules)"
              :key="dish.dish_name"
              style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 14px;border-bottom:1px solid var(--border);opacity:0.75"
            >
              <span style="font-size:13px">{{ dish.dish_name }}</span>
              <button class="btn btn-sm" @click="openAdd(dish.dish_name)">+ 添加换算</button>
            </div>
          </template>
        </div>
      </div>
    </div>

    <RuleFormModal
      v-if="ruleModal"
      :dish-name="ruleModal.dishName"
      :initial="ruleModal.initial"
      @close="ruleModal = null"
      @submit="submitRuleForm"
    />
  </div>
</template>
