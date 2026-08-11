import { computed, reactive, ref } from 'vue'
import { api } from '../api/client'

export const RUNTIME_DEFAULTS = {
  work_start: '07:30',
  work_end: '21:30',
  interval_min: 5,
  interval_max: 20,
  headless: true,
  retry_count: 3,
  timeout_ms: 30000,
  delivery_cancel_miss_threshold: 3,
}

/** Setup page — runtime / scraper settings panel. */
export function useRuntimeSettings({ showAlert, clearAlert }) {
  const runtimeForm = reactive({ ...RUNTIME_DEFAULTS })
  const runtimeLoading = ref(false)
  const runtimeSaving = ref(false)
  const runtimeUpdatedAt = ref('')
  const runtimeLoaded = ref(false)
  const runtimeSaveLabel = computed(() => (runtimeSaving.value ? '保存中…' : '保存并生效'))

  async function loadRuntimeSettings() {
    runtimeLoading.value = true
    try {
      const data = await api.get('/api/runtime-settings', null, null, 'no-store')
      Object.assign(runtimeForm, data.settings || {})
      runtimeUpdatedAt.value = data.updated_at || ''
      runtimeLoaded.value = true
    } catch (err) {
      showAlert('error', '加载运行配置失败：' + err.message)
    } finally {
      runtimeLoading.value = false
    }
  }

  async function saveRuntimeSettings() {
    clearAlert()
    if (runtimeForm.work_start >= runtimeForm.work_end) {
      showAlert('error', '营业开始时间必须早于结束时间（仅支持同日时段）')
      return
    }
    if (Number(runtimeForm.interval_min) > Number(runtimeForm.interval_max)) {
      showAlert('error', '轮询间隔下限不能大于上限')
      return
    }
    runtimeSaving.value = true
    try {
      const data = await api.put('/api/runtime-settings', {
        work_start: runtimeForm.work_start,
        work_end: runtimeForm.work_end,
        interval_min: Number(runtimeForm.interval_min),
        interval_max: Number(runtimeForm.interval_max),
        headless: !!runtimeForm.headless,
        retry_count: Number(runtimeForm.retry_count),
        timeout_ms: Number(runtimeForm.timeout_ms),
        delivery_cancel_miss_threshold: Number(runtimeForm.delivery_cancel_miss_threshold),
      })
      Object.assign(runtimeForm, data.settings || {})
      showAlert('success', '运行配置已保存并即时生效')
      await loadRuntimeSettings()
    } catch (err) {
      showAlert('error', '保存运行配置失败：' + err.message)
    } finally {
      runtimeSaving.value = false
    }
  }

  function resetRuntimeDefaults() {
    Object.assign(runtimeForm, RUNTIME_DEFAULTS)
  }

  return {
    runtimeForm,
    runtimeLoading,
    runtimeSaving,
    runtimeUpdatedAt,
    runtimeLoaded,
    runtimeSaveLabel,
    loadRuntimeSettings,
    saveRuntimeSettings,
    resetRuntimeDefaults,
  }
}
