import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 弹窗最小可访问性：Esc 关闭、打开时把焦点移进弹窗、关闭时还原到触发元素。
 *
 * 不做完整焦点陷阱（Tab 仍可能走到背景），但配合 role="dialog" / aria-modal 已能满足
 * 「键盘能关、读屏器能识别」这条底线（审查 R14）。
 */
export function useModalA11y(onClose) {
  const boxRef = ref(null)
  let previous = null

  function onKey(e) {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onClose()
    }
  }

  onMounted(async () => {
    previous = typeof document !== 'undefined' ? document.activeElement : null
    if (typeof window !== 'undefined') window.addEventListener('keydown', onKey)
    await nextTick()
    boxRef.value?.focus?.()
  })

  onBeforeUnmount(() => {
    if (typeof window !== 'undefined') window.removeEventListener('keydown', onKey)
    if (previous?.focus) previous.focus()
  })

  return boxRef
}
