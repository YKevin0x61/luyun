export function deleteStationConfirmCopy({ title, recipeCount } = {}) {
  const name = title == null || String(title).trim() === '' ? '该岗位' : String(title).trim()
  const n = Number(recipeCount)
  const count = Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
  return {
    title: `删除岗位「${name}」？`,
    body: `将删除本岗全部 ${count} 条配方及修改历史，不可恢复。`,
    confirmLabel: '删除岗位',
  }
}

export function deleteRecipeConfirmCopy({ recipeName } = {}) {
  const name = recipeName == null || String(recipeName).trim() === ''
    ? '这条配方'
    : String(recipeName).trim()
  return {
    title: `删除配方「${name}」？`,
    body: '将删除这条配方及其修改历史，不可恢复。',
    confirmLabel: '删除配方',
  }
}

export function discardRecipeEditsCopy() {
  return {
    title: '放弃未保存的修改？',
    body: '关闭后当前编辑不会保存。',
    confirmLabel: '放弃修改',
  }
}

export function restoreHistoryCopy({ recipeName, changedAt } = {}) {
  const name = recipeName == null || String(recipeName).trim() === ''
    ? '配方'
    : String(recipeName).trim()
  const when = changedAt == null || String(changedAt).trim() === ''
    ? ''
    : String(changedAt).trim()
  return {
    title: `写回「${name}」的这一版？`,
    body: when
      ? `当前配方会先记入修改历史，再恢复为 ${when} 的内容。`
      : '当前配方会先记入修改历史，再恢复为所选版本。',
    confirmLabel: '写回这一版',
  }
}
