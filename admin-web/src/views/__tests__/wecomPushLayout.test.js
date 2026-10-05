import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// A11：390 下 webhook 卡片右侧的「卫生群」「启用」徽章被挤出屏幕（位于 380–431px，
// 视口 390px），页面体 scrollWidth 503 > 390，要横向拖动才看得到。而"这个群是不是
// 卫生群"正是这一页最关键的信息（卫生提醒只发勾选的群）。
//
// 根因不是徽章本身，而是窄屏那条 `grid-template-columns: 1fr`：轨道里的卡片带
// flex 标题行与 form，`1fr` 的自动最小尺寸被内容固有宽度顶起来，轨道实测被撑到
// 493px。**这一点用 DOM 实测量过**（线上 8123 里打上 minmax(0, 1fr) 后
// page-body scrollWidth 从 503 回落到 390），所以这里固定的是那个已被验证的写法。
const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../WecomPushView.vue'), 'utf8')

function compact(source) {
  return source.replace(/\s+/g, '')
}

describe('企微推送页的窄屏布局', () => {
  it('窄屏单列用 minmax(0, 1fr)，否则轨道被内容顶宽、徽章被挤出视口', () => {
    const src = compact(view)
    expect(src).toContain('grid-template-columns:minmax(0,1fr)!important')
    // 1fr 会把轨道撑到 493px，这条断言是防止将来有人"简化"回去
    expect(src).not.toContain('grid-template-columns:1fr!important')
  })

  it('卡片标题行允许换行，名称不把徽章挤出去', () => {
    const src = compact(view)
    expect(src).toContain('.wp-hook-head{')
    expect(src).toContain('flex-wrap:wrap')
    // 名称必须能收缩，否则它会先占满整行
    expect(src).toContain('.wp-hook-name{font-size:13px;min-width:0;')
    expect(src).toContain('.wp-hook-badges{')
  })

  it('窄屏把徽章换到名称下一行，不再同行抢宽度', () => {
    const src = compact(view)
    expect(src).toContain('@media(max-width:560px)')
    expect(src).toContain('.wp-hook-name{flex:11100%;}')
    expect(src).toContain('.wp-hook-badges{flex:11100%;}')
  })

  it('模板用上了这些类名（不是只写了没人用的样式）', () => {
    const src = compact(view)
    expect(src).toContain('class="cardwp-hook-card"')
    expect(src).toContain('class="wp-hook-head"')
    expect(src).toContain('class="wp-hook-name"')
    expect(src).toContain('class="wp-hook-badges"')
  })
})

// A10：预览为空时「立即发送」不该可点，确认文案也要跟着页面状态走。
describe('企微推送页的立即发送', () => {
  it('按钮在预览为空时禁用，并说明为什么', () => {
    const src = compact(view)
    expect(src).toContain(':disabled="!sendReady"')
    expect(src).toContain("sendReady?'发送前会再确认一次':'先选择任务并刷新预览'")
  })

  it('确认文案由 sendNowConfirmText 生成，不再写死一句"当前预览对应的"', () => {
    const src = compact(view)
    expect(src).toContain('sendNowConfirmText({')
    expect(src).not.toContain('确定立即发送当前预览对应的')
  })
})
