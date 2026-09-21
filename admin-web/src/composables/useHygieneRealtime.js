import { useNudgePull } from './useNudgePull'

/**
 * @param {object} options
 * @param {object} [options.filters] 服务端按 nudge 的 scope 过滤，例如
 *   `{ employee_id: 7 }`——改班次这类只与当事人有关的广播就不必推给全店。
 *   留空表示收该 topic 的全部 nudge（默认，与旧行为一致）。
 */
export function useHygieneRealtime({ id, resources, pull, immediate = false, filters = {} }) {
  return useNudgePull({
    id,
    topics: ['hygiene'],
    pull,
    immediate,
    filters,
    match: (event) => (
      event?.type === 'nudge'
      && event?.topic === 'hygiene'
      && resources.includes(event?.scope?.resource)
    ),
  })
}
