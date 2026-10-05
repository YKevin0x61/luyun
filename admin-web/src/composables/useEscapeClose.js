import { onBeforeUnmount, onMounted } from 'vue'

/**
 * 弹窗的 Esc 关闭。
 *
 * 为什么要有这个共享实现：应用里同时存在两套弹窗——`components/admin/ConfirmDialog.vue`
 * 那套自带 Esc（捕获阶段监听），而 `/settings` 的确认框、表结构管理、数据质量这几处
 * 直接手写了 `.modal-overlay`，谁都没接键盘。结果同一页面上「删除行」能按 Esc 关、
 * 「恢复整库数据」按 Esc 没反应——模态框还挡着后面的内容，鼠标用户以为页面卡住了，
 * 键盘用户则被困在框里。**弹窗不响应 Esc 不会报错，只会让人以为界面死了**，所以这
 * 一类缺陷不会被任何自动检查抓到，只能靠这里显式接上。
 *
 * 捕获阶段 + 栈：`ConfirmDialog` 用捕获阶段是为了抢在员工页那层全局 keydown 之前拿到
 * 事件（见那个文件的注释），这里沿用同一相位，保证叠在上层的弹窗先响应。`stack` 是
 * 模块级的，同一时刻只有最后打开（最后注册）的那一个响应 Esc——否则一次 Esc 会把
 * 底下所有弹窗一起关掉。
 *
 * @param {() => boolean} isOpen 现在是否该由 Esc 关闭。三种情况都要能表达：只在打开时
 *   挂载的弹窗传 `() => true`；常驻 DOM 但 `v-if` 在外层的传 `() => true`；同一页里
 *   有多个弹窗各自 `v-if` 的，传 `() => confirmState.open` 这样的判据——关闭态的弹窗
 *   不占用栈顶，Esc 才会落到真正可见的那一个上。
 * @param {() => void} onClose 关闭动作，由调用方提供（各弹窗的关闭路径不一样）。
 */
const stack = []

export function useEscapeClose(isOpen, onClose) {
  const entry = { isOpen, onClose }

  function onKeydown(event) {
    if (event.key !== 'Escape') return
    if (stack[stack.length - 1] !== entry) return
    if (!entry.isOpen()) return
    event.preventDefault()
    event.stopPropagation()
    entry.onClose()
  }

  onMounted(() => {
    stack.push(entry)
    document.addEventListener('keydown', onKeydown, true)
  })

  onBeforeUnmount(() => {
    const index = stack.indexOf(entry)
    if (index !== -1) stack.splice(index, 1)
    document.removeEventListener('keydown', onKeydown, true)
  })
}
