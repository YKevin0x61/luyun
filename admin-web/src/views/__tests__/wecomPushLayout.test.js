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
// U8：禁用态不能只靠 `opacity:.5`（绿底透出来仍是最显眼的主按钮），`title` 也要与页面
// 状态一致 —— 进 tab 时任务已经默认选中，再写「先选择任务」与「当前任务：…」自相矛盾。
describe('企微推送页的立即发送', () => {
  it('按钮在预览为空时禁用，并说明为什么', () => {
    const src = compact(view)
    expect(src).toContain(':disabled="!sendReady"')
    expect(src).toContain("sendReady?'发送前会再确认一次':'点「刷新预览」后可发送'")
  })

  it('禁用态是灰底而不是半透明绿底，可点时才是绿色主按钮（U8）', () => {
    const src = compact(view)
    // 绿底挂在 .wp-send-now 上（不再是内联样式），禁用时被 :disabled 规则覆盖成灰
    expect(src).toContain('.wp-send-now{background:var(--green);')
    expect(src).toContain('.wp-send-now:disabled{background:var(--card2);')
    expect(src).toContain('opacity:1;')
    // 内联绿底那一条必须消失，否则它会把 :disabled 的灰底压回去
    expect(src).not.toContain('style="background:var(--green);border-color:var(--green);color:#fff"')
  })

  it('确认文案由 sendNowConfirmText 生成，不再写死一句"当前预览对应的"', () => {
    const src = compact(view)
    expect(src).toContain('sendNowConfirmText({')
    expect(src).not.toContain('确定立即发送当前预览对应的')
  })
})

// U6（旧清单 A29）：「测试」一点就真外发，而它和「删除」并排、同为小按钮。
describe('企微推送页的「测试」按钮', () => {
  it('点击前先确认，且把整条渠道交给确认文案（要点名群名）', () => {
    const src = compact(view)
    expect(src).toContain('if(!window.confirm(testChannelConfirmText(item)))return')
    expect(src).toContain('@click="handleTestChannel(item)"')
    // 传 id 就写不出群名了
    expect(src).not.toContain('handleTestChannel(item.id)')
  })
})

// U11（旧清单 A25 在本页的落点）：三档控件尺寸完全相同，手机档仍是桌面尺寸。
describe('企微推送页的触屏档尺寸（U11）', () => {
  /** ≤700px 那一档的规则块（从按钮规则起往后截一段）。 */
  function touchBlock() {
    const src = compact(view)
    const at = src.indexOf('@media(max-width:700px){:deep(.btn){')
    expect(at, '找不到 ≤700px 那一档控件尺寸的规则块').toBeGreaterThan(-1)
    return src.slice(at, at + 700)
  }

  it('≤700px 时小按钮 / 页内 tab / 输入框都放大到能点', () => {
    const block = touchBlock()
    expect(block).toContain(':deep(.btn-sm){min-height:40px;')
    expect(block).toContain('.wp-tabbar.view-tab{min-height:44px;')
    expect(block).toContain(':deep(.input),:deep(.select){min-height:44px;')
  })

  it('这两条规则在 560px 那一档之后，也就是确实挂在 ≤700px 上', () => {
    const src = compact(view)
    const at = src.indexOf('@media(max-width:700px){:deep(.btn){')
    const narrow = src.lastIndexOf('@media(max-width:560px){')
    expect(at).toBeGreaterThan(narrow)
  })
})

// U3：矩阵每格原来只有 16×16 的勾选框可点（64 格 × 3 档实测 192 次点空）。
describe('订阅矩阵的整格热区（U3）', () => {
  it('格子整体接管点击，命中区 ≥44×44', () => {
    const src = compact(view)
    expect(src).toContain('class="wp-matrix-cell"')
    expect(src).toContain('@click="handleMatrixCellClick(row,channel,$event)"')
    expect(src).toContain('.wp-matrix-hit{display:flex;')
    expect(src).toContain('min-width:44px;min-height:44px;')
    expect(src).toContain('td.wp-matrix-cell){cursor:pointer;')
  })

  it('点在勾选框上的那一次不会被格子再切一遍', () => {
    const src = compact(view)
    expect(src).toContain("event.target.closest('.luyun-checkbox')")
  })

  it('勾选框有程序化名称（读屏读得出是哪一类内容发给哪个群）', () => {
    const src = compact(view)
    expect(src).toContain(':aria-label="matrixCellLabel(row,channel)"')
    expect(src).toContain('return`${row.name}发给${channel.name}`')
  })
})

// U2：390 下宽表只能横向拖、关键列被硬切，而且没有任何滚动提示。
describe('窄屏宽表改卡片式与矩阵冻结列（U2）', () => {
  it('发送记录 / 变更历史两张表都带卡片式用的 data-label', () => {
    const src = compact(view)
    for (const label of ['时间', '目标渠道', '内容类型', '状态', '字节数', '尝试次数', '最后一次错误']) {
      expect(src, `发送记录少了 data-label="${label}"`).toContain(`data-label="${label}"`)
    }
    for (const label of ['操作人', '操作', '对象', '变更内容']) {
      expect(src, `变更历史少了 data-label="${label}"`).toContain(`data-label="${label}"`)
    }
  })

  it('≤700px 把表格切成一行一卡，字段名走 td::before', () => {
    const src = compact(view)
    expect(src).toContain('.wp-card-tablethead{display:none;}')
    expect(src).toContain('.wp-card-tabletd::before{content:attr(data-label);')
    expect(src).toContain('.wp-card-tabletbodytr{border:1pxsolidvar(--border);')
    // 卡片式那条也在 ≤700px 里
    expect(src.indexOf('.wp-card-tablethead{display:none;}'))
      .toBeGreaterThan(src.indexOf('@media(max-width:700px){:deep(.btn){'))
  })

  it('矩阵冻结「内容类型」列并给出右侧渐隐 / 滚动提示', () => {
    const src = compact(view)
    expect(src).toContain(':deep(.data-table.wp-matrixth:first-child),')
    expect(src).toContain('left:0;')
    expect(src).toContain('.wp-matrix-wrap{')
    expect(src).toContain('radial-gradient(farthest-sideat100%50%')
    expect(src).toContain('class="wp-scroll-hint"')
    // 提示只在窄屏出（桌面档整张表摆得下）
    expect(src).toContain('.wp-scroll-hint{display:none;}')
    expect(src).toContain('.wp-scroll-hint{display:block;')
  })
})

// U1：改完推送时间保存后卡片换位，按位置连点「编辑」会改到另一条任务上。
describe('任务卡的稳定排序与保存反馈（U1）', () => {
  it('卡片带 id 供滚动定位，页面上没有按时间再排一次的影子逻辑', () => {
    const src = compact(view)
    expect(src).toContain('v-for="iteminjobs"')
    expect(src).toContain(':data-job-id="item.id"')
    // 时间只**显示**在卡上（`item.schedule_time`），不参与排序：页面里没有 sort
    expect(src).toContain("{{item.enabled?item.schedule_time:'停用'}}")
    expect(src).not.toContain('jobs.value.sort')
    expect(src).not.toContain('.sort(')
  })

  it('保存成功后短时高亮 + scrollIntoView', () => {
    const src = compact(view)
    expect(src).toContain(":class=\"{'is-saved':flashJobId===item.id}\"")
    expect(src).toContain('flashSavedJob(selectedJobId.value)')
    expect(src).toContain("card.scrollIntoView({block:'nearest',behavior:'smooth'})")
    expect(src).toContain('.wp-job-card.is-saved{animation:wp-job-flash')
  })
})

// U5：后端 5xx 时把英文原文透传给店长（「企微推送数据加载失败：Internal Server Error」）。
describe('加载失败的提示条（U5）', () => {
  it('只显示人话 + 重试，技术原文折叠在「详情」里', () => {
    const src = compact(view)
    expect(src).toContain('企微推送数据加载失败：{{error}}')
    expect(src).toContain('@click="handleRetryLoad"')
    expect(src).toContain('v-if="errorDetail&&errorDetail!==error"')
    expect(src).toContain('<summary>详情</summary>')
  })
})

// U4：数据没回来时页面渲染的是"空数据"的样子（「渠道 0 个 / 暂无渠道」）。
describe('加载态（U4）', () => {
  it('三块卡片各自有加载态，数据没回来时不渲染空态文案', () => {
    const src = compact(view)
    expect(src).toContain('constchannelsLoading=computed(()=>loading.value&&!channels.value.length)')
    expect(src).toContain('constgroupsLoading=computed(()=>loading.value&&!groups.value.length)')
    expect(src).toContain('constsubscriptionsLoading=computed(()=>loading.value&&!matrixRows.value.length)')
    expect(src).toContain('v-if="channelsLoading"class="wp-skeleton"')
    expect(src).toContain('v-if="groupsLoading"class="wp-skeleton"')
    expect(src).toContain('v-if="subscriptionsLoading"class="wp-skeleton"')
    // 空态在加载态之后（v-else-if），两者不会同时出现
    expect(src).toContain('v-else-if="!channels.length"class="empty-state"')
    expect(src).toContain('v-else-if="!groups.length"class="empty-state"')
    // 计数也不能在加载时说「0 个」
    expect(src).toContain("channelsLoading?'加载中…'")
    expect(src).toContain("groupsLoading?'加载中…'")
  })
})

// U10（旧清单 E4 在本页的落点）：14 个表单控件 0 个有程序化名称。
describe('表单控件的程序化名称（U10）', () => {
  it('可见 label 的控件都补上了 id + for', () => {
    const src = compact(view)
    const pairs = [
      ['wp-channel-name', '名称'],
      ['wp-channel-url', 'Webhook地址'],
      ['wp-channel-notes', '备注'],
      ['wp-group-name', '群组名称'],
      ['wp-group-notes', '备注'],
      ['wp-job-name', '任务名称'],
      ['wp-job-topic', '内容类型'],
      ['wp-job-notes', '备注'],
    ]
    for (const [id, label] of pairs) {
      expect(src, `${label} 的 label 没有 for="${id}"`).toContain(`<labelfor="${id}">${label}</label>`)
      expect(src, `控件没有 id="${id}"`).toContain(`id="${id}"`)
    }
  })

  it('没有可见标签的控件（预览框与四个筛选下拉）用 aria-label', () => {
    const src = compact(view)
    expect(src).toContain('aria-label="消息预览内容"')
    for (const label of ['按内容类型筛选', '按目标渠道筛选', '按状态筛选', '按对象类型筛选']) {
      expect(src).toContain(`aria-label="${label}"`)
    }
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
