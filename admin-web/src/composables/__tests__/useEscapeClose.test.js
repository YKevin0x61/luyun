// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { useEscapeClose } from '../useEscapeClose'

// 弹窗按 Esc 关闭是**纯交互契约**，没有任何自动检查会覆盖它：框还开着、后面的内容
// 点不动，看起来就像页面卡死（`/settings` 的恢复整库确认框、表结构管理、数据质量
// 三处正是这样）。这里用最小替身组件固定住行为，尤其是"同一时刻只有最上层响应"。
function mountEscape(handler, { isOpen = () => true } = {}) {
  const Probe = defineComponent({
    setup() {
      useEscapeClose(isOpen, handler)
      return () => h('div')
    },
  })
  return mount(Probe)
}

function pressEscape() {
  const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
  document.dispatchEvent(event)
  return event
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('useEscapeClose', () => {
  it('挂载后按 Esc 触发关闭，卸载后不再触发', async () => {
    const onClose = vi.fn()
    const wrapper = mountEscape(onClose)

    pressEscape()
    expect(onClose).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    pressEscape()
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('isOpen 为假时不响应（同页多个弹窗各自 v-if 的场景）', () => {
    const onClose = vi.fn()
    mountEscape(onClose, { isOpen: () => false })

    pressEscape()
    expect(onClose).not.toHaveBeenCalled()
  })

  // 两个弹窗同时挂着（例如"恢复完成"叠在确认框上）时，一次 Esc 只该关掉最上面那个；
  // 否则底下那些也会一起消失，用户会以为操作被取消了。
  it('只有最后注册（最上层）的弹窗响应 Esc', () => {
    const bottom = vi.fn()
    const top = vi.fn()
    mountEscape(bottom)
    const topWrapper = mountEscape(top)

    pressEscape()
    expect(top).toHaveBeenCalledTimes(1)
    expect(bottom).not.toHaveBeenCalled()

    topWrapper.unmount()
    pressEscape()
    expect(bottom).toHaveBeenCalledTimes(1)
  })

  it('阻止默认行为并拦截事件，避免同一按键被页面其它监听器再处理一遍', () => {
    const onClose = vi.fn()
    mountEscape(onClose)

    const event = pressEscape()
    expect(event.defaultPrevented).toBe(true)
  })

  it('非 Esc 按键不触发', () => {
    const onClose = vi.fn()
    mountEscape(onClose)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    expect(onClose).not.toHaveBeenCalled()
  })
})
