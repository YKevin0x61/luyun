// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import WorkbenchLayout from '../WorkbenchLayout.vue'
import { workbenchNavFor } from '../../../utils/workbenchNav'

// 票 03：工作台外壳第一次以员工视角渲染 —— 导航里**只有「我的」一项**（配方与备货
// 计划在票 07 / 08 各自并入时加），可点击、且整组都高亮。
// 断言的是渲染出来的东西（有几格、点了去哪、亮不亮），不是组件的内部结构。

const ROUTES = [
  { path: '/workbench/me/today', component: { template: '<div>今天</div>' }, meta: { audience: 'staff', standalone: true } },
  { path: '/workbench/me/month', component: { template: '<div>整月</div>' }, meta: { audience: 'staff', standalone: true } },
  { path: '/workbench/daily', component: { template: '<div>日常验收</div>' }, meta: { audience: 'admin', standalone: true } },
]

async function mountShell(path) {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(WorkbenchLayout, { global: { plugins: [router] } })
  return { wrapper, router }
}

describe('工作台外壳的导航', () => {
  it('员工这一档只有「我的」一格，指到「今天」', async () => {
    const { wrapper } = await mountShell('/workbench/me/today')

    const items = wrapper.findAll('.wb-nav-item')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toBe('我的')
    expect(items[0].attributes('href')).toBe('/workbench/me/today')
    // 员工点不到店长那几页（它们在清单里是 admin，导航按身份过滤）。
    expect(wrapper.find('a[href="/workbench/daily"]').exists()).toBe(false)
  })

  it('整组都亮：站在「整月」上，「我的」那一格仍是当前项', async () => {
    const { wrapper } = await mountShell('/workbench/me/month')

    const item = wrapper.get('.wb-nav-item')
    expect(item.classes()).toContain('is-on')
    expect(item.attributes('aria-current')).toBe('page')
  })

  it('点一下真的走到那一格（外壳是路由链接，不是摆设）', async () => {
    const { wrapper, router } = await mountShell('/workbench/me/month')

    await wrapper.get('.wb-nav-item').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/workbench/me/today')
  })

  it('导航项从页面清单派生：身份过滤两个方向都成立（店长那档现在没有工作台页）', () => {
    expect(workbenchNavFor('staff').map((item) => item.key)).toEqual(['me'])
    // 「我的」三页是员工专属（清单里 audience: staff），店长看不见这一格；
    // 票 05 把人事 / 现场并进来之后这里才会有内容。
    expect(workbenchNavFor('admin')).toEqual([])
    expect(workbenchNavFor(null)).toEqual([])
  })
})
