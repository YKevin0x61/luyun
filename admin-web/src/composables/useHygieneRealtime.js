import { useNudgePull } from './useNudgePull'

export function useHygieneRealtime({ id, resources, pull, immediate = false }) {
  return useNudgePull({
    id,
    topics: ['hygiene'],
    pull,
    immediate,
    match: (event) => (
      event?.type === 'nudge'
      && event?.topic === 'hygiene'
      && resources.includes(event?.scope?.resource)
    ),
  })
}
