<script setup>
import { ref } from 'vue'
import { api } from '../../api/client'
import SvgIcon from '../SvgIcon.vue'
import ClassifyDishesModal from './ClassifyDishesModal.vue'
import QuickAddDishesModal from './QuickAddDishesModal.vue'

const props = defineProps({
  flashToast: { type: Function, required: true },
  reload: { type: Function, required: true },
  afterQuickAdd: { type: Function, default: null },
})

const showClassifyModal = ref(false)
const showQuickAddModal = ref(false)
const syncing = ref(false)

async function syncStations() {
  syncing.value = true
  try {
    const res = await api.post('/api/admin/sync-stations', {})
    props.flashToast(res.message || '同步完成')
  } catch (e) {
    props.flashToast(e.message || '同步失败', 'error')
  } finally {
    syncing.value = false
  }
}

function onClassifySaved(count) {
  props.flashToast(`成功保存 ${count} 个分类`)
  props.reload()
}

function handleQuickAdded() {
  if (typeof props.afterQuickAdd === 'function') {
    props.afterQuickAdd({ reload: props.reload })
  } else {
    props.reload()
  }
}
</script>

<template>
  <button class="btn" :disabled="syncing" @click="syncStations">
    <SvgIcon name="refresh-cw" :size="13" /> {{ syncing ? '同步中...' : '补充档口' }}
  </button>
  <button class="btn" @click="showClassifyModal = true">
    <SvgIcon name="tag" :size="13" /> 批量分类
  </button>
  <button class="btn" @click="showQuickAddModal = true">
    <SvgIcon name="zap" :size="13" /> 快捷添加菜品
  </button>

  <ClassifyDishesModal
    v-if="showClassifyModal"
    @close="showClassifyModal = false"
    @saved="onClassifySaved"
  />

  <QuickAddDishesModal
    v-if="showQuickAddModal"
    @close="showQuickAddModal = false"
    @added="handleQuickAdded"
  />
</template>
