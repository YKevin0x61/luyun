/**
 * 考勤表的说法（考勤表导出票 01）。
 *
 * 三种说法都不许在这份文件以外再写一遍：服务端下发的是 `work` / `rest` / `todo` /
 * `null` 这四种**状态码**，翻成人话只在这一处；卡片上那两项（是否新入职、净时长）
 * 同理 —— 「算不出来」与「是否否」长得一样是这一页最容易犯的错。
 */
import { describe, expect, it } from 'vitest'

import {
  closeSuggestionText,
  dayStateText,
  formatDays,
  formatNetHours,
  isMonthSealed,
  leaveFormulaText,
  manualStats,
  manualText,
  newHireText,
  pendingOvertimeText,
  sealedAtText,
  sealedMonthText,
  shiftNameDefault,
} from '../attendanceSheet.js'

describe('逐日三态（验收 3/4）', () => {
  it('上班 / 休 / 待补各说各话', () => {
    expect(dayStateText('work')).toBe('上班')
    expect(dayStateText('rest')).toBe('休')
    expect(dayStateText('todo')).toBe('待补')
  })

  it('今天之后的日期留空 —— 不是「待补」', () => {
    // 服务端给的是 `null`：那些日子还没发生，不该被当成「要人工确认」。
    expect(dayStateText(null)).toBe('')
    expect(dayStateText(undefined)).toBe('')
  })
})

describe('卡片上那两项（验收 6/7）', () => {
  it('是否新入职：算不出来（入职日期为空）就留空，不是「否」', () => {
    expect(newHireText(true)).toBe('是')
    expect(newHireText(false)).toBe('否')
    expect(newHireText(null)).toBe('')
    expect(newHireText(undefined)).toBe('')
  })

  it('净时长带符号，可为负、可带 .5', () => {
    expect(formatNetHours(2)).toBe('+2 小时')
    expect(formatNetHours(0.5)).toBe('+0.5 小时')
    expect(formatNetHours(-1.5)).toBe('−1.5 小时')
    expect(formatNetHours(0)).toBe('0 小时')
  })
})

describe('假期台账那一块的写法（票 03）', () => {
  it('天数：正数不带符号、负数用全角减号、.5 如实', () => {
    expect(formatDays(12)).toBe('12 天')
    expect(formatDays(19.5)).toBe('19.5 天')
    expect(formatDays(0)).toBe('0 天')
    // 负数是**欠班**，不是脏数据 —— 纸面上必须与攒下的假分得开。
    expect(formatDays(-10.5)).toBe('−10.5 天')
    expect(formatDays(null)).toBe('0 天')
  })

  it('算式把六项摊开，再说清池与存假', () => {
    const leave = {
      actual_days: 12,
      monthly_rest: 2,
      holiday_makeup: 1,
      birthday_leave: 0,
      annual_leave: 3,
      previous_balance: 5.5,
      pool: 23.5,
      month_days: 30,
      balance: -6.5,
    }
    expect(leaveFormulaText(leave)).toBe(
      '出勤（含已排） 12 天 + 月例休 2 天 + 法定节假日补休 1 天 + 生日假 0 天 + ' +
        '年假 3 天 + 上月存假 5.5 天 = 可用 23.5 天；本月在岗 30 天，存假 −6.5 天',
    )
  })

  it('没有 leave 时算式是空的（老载荷不编数字）', () => {
    expect(leaveFormulaText(null)).toBe('')
  })

  it('未审批的加班：有待批的才报数（0 笔不留痕迹）', () => {
    expect(pendingOvertimeText(2, 1)).toBe('待批 +1 小时')
    expect(pendingOvertimeText(1, -1.5)).toBe('待批 −1.5 小时')
    expect(pendingOvertimeText(0, 0)).toBe('')
    expect(pendingOvertimeText(undefined, undefined)).toBe('')
  })
})

describe('手填列的写法（票 05）', () => {
  it('空值写成一个破折号，不是留一个孔', () => {
    expect(manualText('是')).toBe('是')
    expect(manualText('调东莞')).toBe('调东莞')
    // 「没填过」与「填了个空串」在页面上长得一样（读侧已经把空串收敛成 `null`）。
    expect(manualText(null)).toBe('—')
    expect(manualText(undefined)).toBe('—')
    expect(manualText('')).toBe('—')
    expect(manualText('   ')).toBe('—')
  })

  it('「本月班次」的提示语说清默认是当月众数；没排班的人直说', () => {
    expect(shiftNameDefault({ default_shift_name: '白班' })).toBe('默认：当月最多 白班')
    // 当月一条排班都没有的人：这一格没有默认，直说「还没有排班」。
    expect(shiftNameDefault({ default_shift_name: null })).toBe('这个月还没有排班')
    expect(shiftNameDefault({})).toBe('这个月还没有排班')
    expect(shiftNameDefault(null)).toBe('这个月还没有排班')
  })

  it('三项摊在卡片上：标签与值一一对应，空的就是破折号', () => {
    // 2026-10-29：「是否离职」退役（离职改成花名册上的状态 + 日期，导出件从那里派生）。
    expect(
      manualStats({
        shift_name_stored: '早班',
        lodging: '是',
        borrowed_store: null,
        resigned: '是', // 台账上的旧列：页面这一块不再念它
      }),
    ).toEqual([
      { label: '本月班次', value: '早班' },
      { label: '是否住宿', value: '是' },
      { label: '借调门店', value: null },
    ])
  })

  it('「本月班次」的回显念台账里的原始值，不拿服务端回填的众数冒充填过', () => {
    // 没填过的人：读侧把 `shift_name` 回填成当月众数（导出件 A 列用的是它），
    // 但回显念的是台账里那一列（`shift_name_stored` = `null` → 页面画成 `—`）。
    const [shift] = manualStats({ shift_name: '夜班', shift_name_stored: null })
    expect(shift.label).toBe('本月班次')
    expect(shift.value).toBe(null)
  })
})

describe('封账上月那句回执（票 04）', () => {
  it('封过账的月说清是哪个月、什么时候封的', () => {
    expect(
      sealedMonthText({ month: '2026-08', sealed_at: '2026-09-24T10:00:00+08:00' }),
    ).toBe('2026-08 已封账（2026-09-24 10:00）')
  })

  it('还没封账 / 老载荷没有这一位时是空串（页面照旧画那颗按钮）', () => {
    expect(sealedMonthText({ month: '2026-08', sealed_at: null })).toBe('')
    expect(sealedMonthText(null)).toBe('')
    expect(sealedMonthText(undefined)).toBe('')
  })

  it('时刻只截字符串、不按浏览器时区挪（服务端给的就是北京时）', () => {
    expect(sealedAtText('2026-09-24T10:00:00+08:00')).toBe('2026-09-24 10:00')
    expect(sealedAtText('2026-09-24T23:05:00+08:00')).toBe('2026-09-24 23:05')
    // 截不出形状（比如只给了日期）就原样显示，不编一个时刻出来。
    expect(sealedAtText('2026-09-24')).toBe('2026-09-24')
    expect(sealedAtText(null)).toBe('')
  })
})

// ── 收尾修复 ⑥：日格那几个字不许前后端各说各话 ───────────────────────────────
//
// 卡片上的「休 / 休半天 / 待补」由这一份出，导出件（服务端
// `services/attendance/export.py`）是 Python、import 不到它，于是自己另存了一份
// `DAY_TEXT` / `HALF_REST_TEXT` / `TODO_TEXT`。两边**不是**同一批字，所以这里把
// 服务端那份**读出来逐字比一遍**（同仓库既有的读源码式断言）：改一边忘另一边就红。

describe('日格文案与导出件的常量钉在一起（收尾修复 ⑥）', () => {
  it('export.py 的 DAY_TEXT / HALF_REST_TEXT / TODO_TEXT 与这一份逐字相等', async () => {
    const { readFileSync } = await import('node:fs')
    const { dirname, join } = await import('node:path')
    const { fileURLToPath } = await import('node:url')
    const { HALF_REST_TEXT: halfRest } = await import('../attendanceSheet.js')

    const source = readFileSync(
      join(
        dirname(fileURLToPath(import.meta.url)),
        '../../../../services/attendance/export.py',
      ),
      'utf8',
    )
    const constant = (name) => {
      const matched = source.match(new RegExp(`^${name} = "(.+)"$`, 'm'))
      expect(matched, `${name} 不在 export.py 里了`).not.toBeNull()
      return matched[1]
    }

    // 「上班」在导出件里是**留空的空格子**（出勤不写字），所以只比另外三种写法。
    expect(constant('DAY_TEXT')).toBe(dayStateText('rest'))
    expect(constant('HALF_REST_TEXT')).toBe(halfRest)
    expect(constant('TODO_TEXT')).toBe(dayStateText('todo'))
  })
})

// ── 票 09：期末存假（本月剩余）可人工定稿 ─────────────────────────────────────
//
// 期末这一格有三位在说话，页面必须让人一眼分得开：台账里**填过没有**
// （`closing_balance_stored`，输入框绑的就是它）、**有效值**（`leave.balance` = 人工优先，
// 台账九项里那个「存假」念的还是它）、以及**公式算出来的那个数**
// （`leave.balance_computed`）—— 后者只出现在「系统建议」那句话里，绝不预填进输入框。

describe('期末存假的「系统建议」（票 09）', () => {
  it('没填过时只报系统建议那个数（公式值不预填进输入框）', () => {
    expect(closeSuggestionText({ balance_computed: -27, balance_is_manual: false })).toBe(
      '系统建议：−27 天',
    )
    // 负数照 `formatDays` 写全角减号（欠班，不是脏数据）；带 .5 也如实。
    expect(closeSuggestionText({ balance_computed: -3.5, balance_is_manual: false })).toBe(
      '系统建议：−3.5 天',
    )
    expect(closeSuggestionText({ balance_computed: 2, balance_is_manual: false })).toBe(
      '系统建议：2 天',
    )
  })

  it('填过时把「你填的」与「系统建议」并排说清（两个不一致时看得见）', () => {
    expect(
      closeSuggestionText({
        balance: 10,
        balance_is_manual: true,
        balance_computed: -27,
      }),
    ).toBe('你填的是 10 天，系统建议 −27 天')
  })

  it('服务端没给这几位（老载荷）时是空串 —— 不编一个数字出来', () => {
    expect(closeSuggestionText(null)).toBe('')
    expect(closeSuggestionText(undefined)).toBe('')
    expect(closeSuggestionText({})).toBe('')
  })
})

describe('封账后那一格锁定（票 09）', () => {
  it('当月给封账时刻（顶层 `sealed_at`）就锁定', () => {
    expect(isMonthSealed({ sealed_at: '2026-10-01T09:00:00+08:00' })).toBe(true)
  })

  it('当月那一行自己带 `sealed_at` 也算锁定', () => {
    expect(
      isMonthSealed({ employees: [{ id: 1, sealed_at: '2026-10-01T09:00:00+08:00' }] }),
    ).toBe(true)
  })

  it('没封过账时按「还没封」画', () => {
    expect(isMonthSealed({ month: '2026-09', sealed_at: null })).toBe(false)
    expect(isMonthSealed({ previous_month: { month: '2026-08', sealed_at: null } })).toBe(false)
    // **上月封过账也不算本月锁定**：那是本月的期初定了，本月的期末还是可以填的。
    // 拿 `previous_month.sealed_at` 当判据，门店第一次封账之后期末就再也填不了。
    expect(
      isMonthSealed({ previous_month: { month: '2026-08', sealed_at: '2026-09-24T10:00:00+08:00' } }),
    ).toBe(false)
    expect(isMonthSealed(null)).toBe(false)
    expect(isMonthSealed(undefined)).toBe(false)
  })
})
