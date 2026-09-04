/** Tip row list helpers. */

export function addTipRow(rows) {
  return [...(rows || []), '']
}

export function removeTipRow(rows, index) {
  const list = rows || []
  if (index < 0 || index >= list.length) return list
  return list.filter((_, i) => i !== index)
}

export function dropBlankTipRows(rows) {
  return (rows || []).filter((text) => String(text || '').trim())
}
