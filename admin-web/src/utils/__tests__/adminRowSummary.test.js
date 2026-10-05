import { describe, expect, it } from 'vitest'
import { describeRow } from '../adminRowSummary.js'

// 删除确认原本只写「确认删除该行（rowid=7）？」——rowid 是数据库主键，店长既认不出
// 它是哪一条，也看不出"删了不可撤销"。这里固定住"行 → 一句话"的挑列规则：业务列优先、
// 其次时间、最后兜底，并且**永远附上主键**（核对时唯一能对上表格首列的值）。
describe('describeRow', () => {
  it('订单行给出菜品 / 桌号 / 时间 / 序号，而不是只有主键', () => {
    const text = describeRow({
      rowid: 7,
      dish_name: '宫保鸡丁',
      table_number: 'A12',
      quantity: 2,
      created_at: '2026-10-05T12:03:44+08:00',
    })
    expect(text).toContain('「宫保鸡丁」')
    expect(text).toContain('桌号 A12')
    expect(text).toContain('2026-10-05 12:03')
    expect(text).toContain('序号 7')
    // 时间只保留到分钟：ISO 原文带秒与偏移，确认框里读起来是噪音
    expect(text).not.toContain('12:03:44')
    expect(text).not.toContain('+08:00')
  })

  it('业务列最多取 max 条，时间列随后补齐', () => {
    const text = describeRow(
      {
        rowid: 3,
        dish_name: 'A',
        table_number: 'B',
        station: 'C',
        status: 'D',
        order_time: '2026-10-05T09:00:00+08:00',
      },
      { max: 2 },
    )
    expect(text).toContain('「A」')
    expect(text).toContain('桌号 B')
    expect(text).not.toContain('档口 C')
    expect(text).not.toContain('状态 D')
  })

  it('没有已知业务列时用表里前几个短字段兜底，长文本与机器字段跳过', () => {
    const text = describeRow({
      rowid: 12,
      zone_name: '明档',
      note: 'x'.repeat(60),
      password_hash: 'deadbeef',
      session_id: 'sid',
    })
    expect(text).toContain('明档')
    expect(text).not.toContain('xxxx')
    expect(text).not.toContain('deadbeef')
    expect(text).not.toContain('sid')
    expect(text).toContain('序号 12')
  })

  it('一个可用字段都没有时只剩序号，也不返回 undefined', () => {
    expect(describeRow({ rowid: 9 })).toBe('序号 9')
    expect(describeRow({})).toBe('')
    expect(describeRow(null)).toBe('')
  })

  it('withKey=false 时不附主键（给"只想要业务标识"的调用方）', () => {
    const text = describeRow({ rowid: 9, dish_name: '汤' }, { withKey: false })
    expect(text).toBe('「汤」')
  })

  it('空字符串与 null 不占位（NULL 显示为 NULL 会让确认框读起来像出了错）', () => {
    const text = describeRow({ rowid: 4, dish_name: '', table_number: null, status: '待出餐' })
    expect(text).toBe('状态 待出餐 · 序号 4')
  })
})
