/** Step row list helpers. */

export function addStepRow(rows) {
  return [...(rows || []), '']
}

export function removeStepRow(rows, index) {
  const list = rows || []
  if (index < 0 || index >= list.length) return list
  return list.filter((_, i) => i !== index)
}

export function moveStepRow(rows, fromIndex, toIndex) {
  const list = rows || []
  if (
    fromIndex === toIndex
    || fromIndex < 0
    || toIndex < 0
    || fromIndex >= list.length
    || toIndex >= list.length
  ) {
    return list
  }
  const next = [...list]
  const [moved] = next.splice(fromIndex, 1)
  next.splice(toIndex, 0, moved)
  return next
}

export function dropBlankStepRows(rows) {
  return (rows || []).filter((text) => String(text || '').trim())
}
