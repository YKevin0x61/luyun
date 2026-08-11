/**
 * Pure connection-status edge detector (no Vue / uni / audio).
 *
 * @param {string|undefined|null} prev
 * @param {string|undefined|null} next
 * @returns {'disconnect' | null}
 */
export function connectionEdge(prev, next) {
  if (prev === 'connected' && next !== 'connected') return 'disconnect'
  return null
}
