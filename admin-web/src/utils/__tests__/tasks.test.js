import { describe, expect, it } from 'vitest'
import {
  addDays,
  assigneeEmptyHint,
  assigneeText,
  buildAssignee,
  deadlineHHMM,
  defaultDueDate,
  filterEmployees,
  formatDeadlineLabel,
  frequencyText,
  photoHint,
  recentPeriodsText,
  statusLabel,
  statusTone,
} from '../tasks'

describe('任务展示口径', () => {
  it('截止时刻按 ISO 文本切片，不随本机时区漂移', () => {
    expect(formatDeadlineLabel('2026-09-24T18:00:00+08:00')).toBe('9月24日 18:00')
    expect(formatDeadlineLabel('2026-11-03T09:05:00+08:00')).toBe('11月3日 09:05')
    expect(formatDeadlineLabel('')).toBe('未设截止')
    expect(formatDeadlineLabel('不是时间')).toBe('未设截止')
    expect(deadlineHHMM('2026-09-24T18:00:00+08:00')).toBe('18:00')
    expect(deadlineHHMM('')).toBe('')
  })

  it('进行中逾期要说「已逾期」，不能还写「进行中」', () => {
    expect(statusLabel({ status: 'todo', overdue: false })).toBe('进行中')
    expect(statusLabel({ status: 'todo', overdue: true })).toBe('已逾期')
    expect(statusLabel({ status: 'pending' })).toBe('待验收')
    expect(statusLabel({ status: 'passed' })).toBe('已通过')
    expect(statusLabel({ status: 'cancelled' })).toBe('已撤回')
    expect(statusTone({ status: 'todo', overdue: true })).toBe('danger')
    expect(statusTone({ status: 'pending' })).toBe('warn')
    expect(statusTone({ status: 'passed' })).toBe('ok')
  })

  it('小组任务列名字，超过三个人就折叠', () => {
    expect(
      assigneeText({ assignee_kind: 'group', assignee_names: ['张三', '李四'] }),
    ).toBe('张三、李四')
    expect(
      assigneeText({ assignee_kind: 'group', assignee_names: ['甲', '乙', '丙', '丁'] }),
    ).toBe('甲、乙、丙 等 4 人')
    expect(assigneeText({ assignee_kind: 'group', assignee_names: [] })).toBe('未指定执行人')
  })

  it('池任务写池名 + 工作区 + 当天解析人数；空池可提示', () => {
    const pool = {
      assignee_kind: 'pool',
      pool_label: '当天当班的人',
      pool_zone_name: '前台',
      assignee_names: ['张三'],
    }
    expect(assigneeText(pool)).toBe('当天当班的人「前台」 · 1 人')
    expect(assigneeEmptyHint({ ...pool, assignee_names: [] })).toBe(true)
    expect(assigneeEmptyHint({ assignee_kind: 'group', assignee_names: [] })).toBe(false)
    expect(photoHint({ photo_count: 2 })).toBe('需拍 2 张现场照')
    expect(photoHint({})).toBe('需拍 1 张现场照')
  })

  it('发布表单的指派结构', () => {
    expect(
      buildAssignee({ mode: 'group', groupIds: ['3', 5, 0], poolKind: 'all_staff' }),
    ).toEqual({ kind: 'group', employee_ids: [3, 5] })
    expect(
      buildAssignee({ mode: 'pool', poolKind: 'zone_on_duty', zoneId: 2 }),
    ).toEqual({ kind: 'pool', pool_kind: 'zone_on_duty', zone_id: 2 })
    expect(
      buildAssignee({ mode: 'pool', poolKind: 'on_duty', zoneId: 9 }),
    ).toEqual({ kind: 'pool', pool_kind: 'on_duty', zone_id: null })
  })

  it('默认截止日：18:00 还没到就是今天，过了就顺到明天', () => {
    const morning = new Date('2026-09-24T10:00:00+08:00')
    const evening = new Date('2026-09-24T19:00:00+08:00')
    expect(defaultDueDate('2026-09-24', '18:00', morning)).toBe('2026-09-24')
    expect(defaultDueDate('2026-09-24', '18:00', evening)).toBe('2026-09-25')
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
    expect(addDays('', 1)).toBe('')
  })

  it('定期任务的频率与近几期完成情况', () => {
    expect(frequencyText({ frequency_kind: 'daily' })).toBe('每天')
    expect(frequencyText({ frequency_kind: 'weekly', weekday: 0 })).toBe('每周一')
    expect(frequencyText({ frequency_kind: 'weekly', weekday: 6 })).toBe('每周日')
    expect(frequencyText({ frequency_kind: 'monthly', month_day: 5 })).toBe('每月 5 号')
    expect(frequencyText(null)).toBe('')
    expect(recentPeriodsText({ recent_total: 7, recent_passed: 5 })).toBe('近 7 期完成 5 期')
    expect(recentPeriodsText({ recent_total: 0, recent_passed: 0 })).toBe('还没有生成过期')
  })

  it('按名字或职位过滤员工', () => {
    const employees = [
      { id: 1, name: '张三', job_title: '领班' },
      { id: 2, name: '李四', job_title: '收银' },
    ]
    expect(filterEmployees(employees, '').length).toBe(2)
    expect(filterEmployees(employees, '李').map((item) => item.id)).toEqual([2])
    expect(filterEmployees(employees, '领班').map((item) => item.id)).toEqual([1])
    expect(filterEmployees(employees, '没有')).toEqual([])
  })
})
