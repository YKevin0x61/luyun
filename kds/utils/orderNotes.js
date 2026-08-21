/**
 * Canonical 备注 identity and visible copy.
 * Same rule as Python db_core.order_notes.canonical_order_notes:
 * strip; strings starting with 外卖平台: are empty (not 备注).
 */

const PLATFORM_NOTES_PREFIX = '外卖平台:'

/**
 * @param {unknown} value
 * @returns {string}
 */
export function canonicalOrderNotes(value) {
  if (value == null) return ''
  const text = String(value).trim()
  if (text.startsWith(PLATFORM_NOTES_PREFIX)) return ''
  return text
}

/**
 * 菜卡 / 待上笼组 identity: 菜名 + 归一备注.
 * @param {string} dishName
 * @param {unknown} notes
 * @returns {string}
 */
export function dishNotesIdentityKey(dishName, notes) {
  return `${String(dishName || '')}\0${canonicalOrderNotes(notes)}`
}
