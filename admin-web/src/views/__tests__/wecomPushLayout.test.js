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

// D2：这张卡以前和「定时任务」共用 `v-if="activeTab === 'jobs' || activeTab === 'logs'"`
// 的两列 grid，而左列自己带 `v-if="activeTab === 'jobs'"` —— 切到发送记录时左列不渲染，
// 卡片被 grid 自动放进第一列 `minmax(280px, 420px)`：1440 下 7 列表格只有 388px 可视宽
// （字节数 / 尝试次数 / 最后一次错误要横向滚动），右边 1000px 空白。
// 结构上钉死两件事：发送记录是**根容器的直接子节点**（class 只有 card），而两列 grid
// 只属于定时任务那一个 tab。
describe('发送记录不再被塞进两列 grid 的左列（D2）', () => {
  it('两列 grid 只挂在「定时任务」上，发送记录不共用它', () => {
    const src = compact(view)
    expect(src).toContain('v-if="activeTab===\'jobs\'"class="grid"')
    expect(src).not.toContain("activeTab==='jobs'||activeTab==='logs'")
    expect(src).not.toContain('v-if="activeTab===\'logs\'"class="grid"')
  })

  it('发送记录卡不套 grid（class 里没有 grid）', () => {
    const src = compact(view)
    expect(src).toContain('<divv-if="activeTab===\'logs\'"class="card">')
  })

  it('窄屏单列那条 !important 还在（它管的是渠道 / 定时任务那两个 grid 的收缩）', () => {
    expect(compact(view)).toContain('grid-template-columns:minmax(0,1fr)!important')
  })
})

// D4：390 下 5 个页内标签与「接口版本 v2」抢同一行，flex 把按钮压得比文字还窄，中文
// 逐字换行成「渠 道」「定时任 务」「发送记 录」「变更历 史」。两条一起上：标签行自己
// 横向滚动（按钮不收缩、不换行），版本徽章窄屏让位。
describe('页内 tab 条在窄屏不竖排（D4）', () => {
  it('标签进横向滚动行：按钮 flex:0 0 auto + nowrap', () => {
    const src = compact(view)
    expect(src).toContain('.wp-tabbar{')
    expect(src).toContain('overflow-x:auto')
    expect(src).toContain('.wp-tabbar.view-tab{flex:00auto;white-space:nowrap;}')
  })

  it('版本徽章在窄屏让位（真不匹配时页顶有专门的提示条）', () => {
    const src = compact(view)
    expect(src).toContain('@media(max-width:700px)')
    expect(src).toContain('.wp-api-version{display:none;}')
  })

  it('模板把 5 个 tab 放进 .wp-tabbar（样式挂在真的元素上）', () => {
    const src = compact(view)
    expect(src).toContain('class="wp-tabbarluyun-scrollbar"')
    expect(src).toContain('class="wp-api-version"')
    expect(src).toContain('接口版本{{WECOM_PUSH_API_VERSION}}')
  })
})
