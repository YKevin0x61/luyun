// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ConfirmDialog from '../ConfirmDialog.vue'

/**
 * 驳回原因必填（2026-10-05 用户裁定）。
 *
 * 判据只有一条：**填之前不让提交**。服务端同样会拦（空原因回 400），前端这一道是为了
 * 省掉那次网络往返，也为了避免"点了一下没反应"那种体验 —— 所以这儿的断言压在
 * 「按钮被禁用」与「填了之后真的把去掉空格的那句话交出去」两头。
 */
function mountDialog({ confirmLabel = '驳回', prompt } = {}) {
  return mount(ConfirmDialog, {
    props: {
      title: '驳回这一项',
      message: '驳回后要重新拍。',
      confirmLabel,
      danger: true,
      prompt,
    },
  })
}

function buttonByText(wrapper, text) {
  return wrapper.findAll('button').find((btn) => btn.text() === text)
}

describe('确认框的必填输入（prompt.required）', () => {
  it('空着或只有空格都不让提交；写了才放行，并把去掉首尾空格的文本交出去', async () => {
    const wrapper = mountDialog({
      prompt: { label: '哪里不合格（必填）', required: true, hint: '必填 · 员工照这句重拍' },
    })
    // 提示就写在输入框旁（不是只有页面顶上那一行报错）。
    expect(wrapper.text()).toContain('必填 · 员工照这句重拍')

    const confirm = buttonByText(wrapper, '驳回')
    expect(confirm.attributes('disabled')).toBeDefined()

    await wrapper.get('textarea').setValue('   ')
    expect(buttonByText(wrapper, '驳回').attributes('disabled')).toBeDefined()

    await wrapper.get('textarea').setValue('  台面还有油渍  ')
    const enabled = buttonByText(wrapper, '驳回')
    expect(enabled.attributes('disabled')).toBeUndefined()
    await enabled.trigger('click')
    expect(wrapper.emitted('confirm')).toEqual([['台面还有油渍']])
  })

  it('没标必填的老调用方（删除那类确认）行为不变：空着也能确认', async () => {
    // 删除 / 排除这几处只弹一句确认，没有输入框；就算给了输入框，不标 required
    // 也不该被新规矩拦住 —— 这次只改驳回。
    const wrapper = mountDialog({ confirmLabel: '删除', prompt: { label: '说明' } })
    const confirm = buttonByText(wrapper, '删除')
    expect(confirm.attributes('disabled')).toBeUndefined()
    await confirm.trigger('click')
    expect(wrapper.emitted('confirm')).toEqual([['']])
  })
})
