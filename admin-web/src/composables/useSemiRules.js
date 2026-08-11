import { ref } from 'vue'
import { api } from '../api/client'

export function useSemiRules() {
  const groupedData = ref(null) // { station: [{dish_name, has_rules, rules: [...]}] }
  const loading = ref(false)

  async function loadRules() {
    loading.value = true
    try {
      const data = await api.get('/api/semi-rules/dishes/grouped')
      if (data.success) groupedData.value = data.stations || {}
    } catch (e) {
      groupedData.value = {}
    } finally {
      loading.value = false
    }
  }

  function findRuleById(ruleId) {
    if (!groupedData.value) return null
    for (const dishes of Object.values(groupedData.value)) {
      for (const dish of dishes) {
        const rule = (dish.rules || []).find((r) => String(r.id) === String(ruleId))
        if (rule) return { ...rule, dish_name: rule.dish_name || dish.dish_name }
      }
    }
    return null
  }

  async function deleteRule(ruleId) {
    await api.delete(`/api/semi-rules/${ruleId}`)
    await loadRules()
  }

  async function createRule(payload) {
    await api.post('/api/semi-rules/', payload)
    await loadRules()
  }

  async function updateRule(ruleId, payload) {
    await api.put(`/api/semi-rules/${ruleId}`, payload)
    await loadRules()
  }

  return { groupedData, loading, loadRules, findRuleById, deleteRule, createRule, updateRule }
}
