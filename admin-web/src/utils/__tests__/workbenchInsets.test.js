import { describe, expect, it } from 'vitest'
// 内容区让位的度量（票 03 / ADR 0106 决策 2 与 4）。
//
// 三根变量由壳定义、只被壳的内容容器消费；这里只有**算法**：把「本屏有没有底栏」
// 「底栏多高」「安全区多少」算成一个下 inset 的像素数。两个真实来源（底栏的几何、
// `env()`）在 jsdom 里都量不到（不做布局、没有计算样式），所以它们是**输入** ——
// 壳在运行时喂真值，测试喂值断言算出来的数。
import {
  MOBILE_MAX_WIDTH_PX,
  TAB_BAR_HEIGHT_PX,
  WIDE_INSET_MIN_WIDTH_PX,
  bottomInsetPx,
  contentInsetFor,
  readSafeBottomPx,
  tabBarOnScreen,
} from '../workbenchInsets.js'
import { PAGE_ROUTES } from '../../router/pageRoutes.js'

describe('tabBarOnScreen：本屏此刻渲染了底栏吗', () => {
  it('手机档 + 这一页不自带底栏 → 在屏上', () => {
    expect(tabBarOnScreen({ hasOwnBottomBar: false, mobile: true })).toBe(true)
  })

  it('自带底栏的页（配方阅读面）→ 工作台那条不渲染，也就不在屏上', () => {
    expect(tabBarOnScreen({ hasOwnBottomBar: true, mobile: true })).toBe(false)
  })

  it('桌面档 → 底栏 `display: none`，不在屏上（今天桌面档没有下留白）', () => {
    expect(tabBarOnScreen({ hasOwnBottomBar: false, mobile: false })).toBe(false)
  })

  it('判据里没有 `audience`：管理端与员工端在手机档是一回事', () => {
    // 2026-10-13 基线实测：管理端手机档同样有那条 57px 底栏（`WorkbenchTabBar` 只看
    // `!hasOwnBottomBar`）。所以「按 audience 给管理端算 0」在这里根本无从表达 ——
    // 输入里就没有 audience 这一项。
    expect(tabBarOnScreen.length, 'tabBarOnScreen 只该有一个入参对象').toBe(1)
    expect(tabBarOnScreen({ hasOwnBottomBar: false, mobile: true })).toBe(true)
  })
})

describe('bottomInsetPx：下 inset = 底栏高 + 安全区，否则 0', () => {
  it('手机档底栏在时与今天等价：57px + 0（01 票基线实测 57px）', () => {
    expect(bottomInsetPx({ barOnScreen: true, safeBottomPx: 0 })).toBe(TAB_BAR_HEIGHT_PX)
    expect(bottomInsetPx({ barOnScreen: true, safeBottomPx: 0 })).toBe(57)
  })

  it('安全区是可注入的输入：iPhone 的 home indicator 那 34px 要加进去', () => {
    expect(bottomInsetPx({ barOnScreen: true, safeBottomPx: 34 })).toBe(91)
  })

  it('底栏不在屏上（桌面档 / 自带底栏的页）→ 0，安全区也不加', () => {
    expect(bottomInsetPx({ barOnScreen: false, safeBottomPx: 0 })).toBe(0)
    expect(bottomInsetPx({ barOnScreen: false, safeBottomPx: 34 })).toBe(0)
  })

  it('底栏高也是可注入的输入（底栏改高时这里不改算法）', () => {
    expect(bottomInsetPx({ barOnScreen: true, barHeightPx: 60, safeBottomPx: 4 })).toBe(64)
  })
})

describe('readSafeBottomPx：安全区原始值从令牌层取，不写第二遍 env()', () => {
  /** 假的 `getComputedStyle`：只回答「这个元素上 `--safe-b` 算出来是什么」。
   *  真实浏览器里那一步会把 `env(safe-area-inset-bottom, 0px)` 解成 px（桌面 0、
   *  iPhone 竖屏 34）。 */
  const styleOf = (value) => () => ({ getPropertyValue: () => value })

  it('令牌层写 `34px` 就取到 34', () => {
    expect(readSafeBottomPx({}, styleOf('34px'))).toBe(34)
  })

  it('桌面 Chromium 是 `0px`', () => {
    expect(readSafeBottomPx({}, styleOf('0px'))).toBe(0)
  })

  it('读不到（jsdom / 令牌层没挂 / 老浏览器）就是 0，不是 NaN', () => {
    for (const raw of ['', '   ', 'env(safe-area-inset-bottom,0px)', 'calc(1px + 2px)', undefined]) {
      expect(readSafeBottomPx({}, styleOf(raw)), `--safe-b: ${JSON.stringify(raw)}`).toBe(0)
      expect(Number.isFinite(readSafeBottomPx({}, styleOf(raw)))).toBe(true)
    }
  })

  it('没有 DOM（node 环境 / SSR）时也是 0，不抛', () => {
    expect(readSafeBottomPx(undefined, undefined)).toBe(0)
  })
})

describe('两个常量就是今天的两个数', () => {
  it('底栏高 57px = 底栏自身 56px + 1px 上边框（今天那条 calc 里的数）', () => {
    expect(TAB_BAR_HEIGHT_PX).toBe(57)
  })

  it('手机档断点 720px 与底栏自己那条媒体查询同值', () => {
    expect(MOBILE_MAX_WIDTH_PX).toBe(720)
  })
})

// 横向与上内边距按 `group` 查表（票 04 / ADR 0106 决策 3）。三组数值不同是现状的事实，
// **不归一**：每一行的出处是收口前那一处的原文（共享样式表的 `.hy-main`、工作台首页的
// `.wbh`），值可以是 CSS 表达式 —— 壳只是把它套在内容容器上，解算式仍是浏览器的事。
// 900px 是那一组样式自己的断点（原来那条 `@media (min-width: 900px)`），不是壳的 720 档。
describe('contentInsetFor：按组查表给出横向与上内边距', () => {
  it('现场八页 = 共享样式表 `.hy-main` 那条（base `var(--hy-page)` / 桌面 clamp）', () => {
    expect(contentInsetFor('floor', { wide: false })).toEqual({
      top: 'var(--hy-page)',
      inline: 'max(var(--hy-page), var(--safe-l), var(--safe-r))',
    })
    expect(contentInsetFor('floor', { wide: true })).toEqual({
      top: 'clamp(1.1rem, 2.2vw, 2rem)',
      inline: 'clamp(1.2rem, 3vw, 2.6rem)',
    })
  })

  it('工作台首页 = 页面自己那条 `max(var(--hy-page, 18px), --safe-*)`，两个档通用', () => {
    const row = {
      top: 'max(var(--hy-page, 18px), var(--safe-t))',
      inline: 'max(var(--hy-page, 18px), var(--safe-l), var(--safe-r))',
    }
    expect(contentInsetFor('home', { wide: false })).toEqual(row)
    // 没有 `wide` 那一档的行在宽屏上照样用 base（这一页本来就只有一条规则）。
    expect(contentInsetFor('home', { wide: true })).toEqual(row)
  })

  it('人事 / 我的 / 后勤 / 卫生四组是 0：那几个内边距是页面自己的（见各组注释）', () => {
    for (const group of ['hr', 'me', 'kitchen', 'hygiene']) {
      expect(contentInsetFor(group, { wide: false }), group).toEqual({ top: '0px', inline: '0px' })
      expect(contentInsetFor(group, { wide: true }), group).toEqual({ top: '0px', inline: '0px' })
    }
  })

  it('认不出的组（壳外的页 / 新加组还没想好）给 0，不给一个可能撑破版面的默认值', () => {
    for (const group of ['', null, undefined, 'entry', 'system', 'nope']) {
      expect(contentInsetFor(group, { wide: true }), String(group)).toEqual({
        top: '0px', inline: '0px',
      })
    }
  })

  it('铺满的页（`flush`，配方打印那一类）不吃这一份：组里给什么都是 0', () => {
    // 与「自带底栏」互不隐含（ADR 0106 决策 5）：底下那条栏是谁的、内边距留不留，两件事。
    for (const wide of [false, true]) {
      expect(contentInsetFor('floor', { wide, flush: true })).toEqual({ top: '0px', inline: '0px' })
      expect(contentInsetFor('home', { wide, flush: true })).toEqual({ top: '0px', inline: '0px' })
      expect(contentInsetFor('kitchen', { wide, flush: true })).toEqual({ top: '0px', inline: '0px' })
    }
  })

  it('页面清单里会套统一壳的每一组都有一行（加组就必须在这里做一次决定）', () => {
    // 只列「工作台里、且不是 entry」的组：entry 那两页（`/workbench/forbidden`、登录 / 注册）
    // 不套统一壳。加一个新组而忘了在这里登记，就会静默拿到 0 —— 这条把它变红。
    const groups = new Set(
      PAGE_ROUTES.filter((row) => row.path.startsWith('/workbench') && row.group !== 'entry')
        .map((row) => row.group),
    )
    expect([...groups].sort()).toEqual(['floor', 'home', 'hr', 'hygiene', 'kitchen', 'me'])
    for (const group of groups) {
      expect(contentInsetFor(group, { wide: false }), `${group} 没有登记`).toBeTruthy()
    }
  })

  it('宽档断点 900px 就是共享样式表那条媒体查询的数', () => {
    expect(WIDE_INSET_MIN_WIDTH_PX).toBe(900)
  })
})
