/**
 * Kitchen 出餐 batch outcome → toast, or null when the list update is enough.
 * Print failure stays on the 补打 banner, not this helper.
 *
 * @param {{ processed: number, requested: number, errorMessage?: string }} outcome
 * @returns {{ title: string, icon: string } | null}
 */
export function toastForServeBatch({ processed, requested, errorMessage }) {
  if (errorMessage) {
    return {
      title: '批量出餐失败: ' + errorMessage,
      icon: 'error'
    }
  }
  if (!processed) {
    return { title: '没有可提交的订单', icon: 'none' }
  }
  if (processed < requested) {
    return {
      title: `部分出餐成功 ${processed}/${requested}份`,
      icon: 'none'
    }
  }
  return null
}
