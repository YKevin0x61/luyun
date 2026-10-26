import { describe, expect, it } from 'vitest'
import { getCellLabel, getColumnLabel, getTableLabel } from '../adminLabels.js'

describe('getTableLabel', () => {
  it('返回映射命中的中文表名', () => {
    expect(getTableLabel('orders')).toBe('订单记录')
    expect(getTableLabel('wecom_push_jobs')).toBe('企微推送任务')
    expect(getTableLabel('auth')).toBe('登录认证')
    expect(getTableLabel('sop_recipes')).toBe('配方内容')
    expect(getTableLabel('hygiene_zones')).toBe('卫生区域')
    expect(getTableLabel('logs')).toBe('运行日志')
  })

  // 侧边栏按「业务数据 / 配方库 / 卫生管理 / 排班 / 企微推送 / 人事 / 考勤 / 损耗」分组，
  // 除业务数据那组以外都是 2026 年新加的表；漏一条就是侧边栏里一串裸英文。
  it('排班 / 企微订阅 / 人事 / 考勤 / 损耗这几组表也有中文名', () => {
    expect(getTableLabel('staff_shifts')).toBe('排班班次')
    expect(getTableLabel('staff_assignments')).toBe('排班结果')
    expect(getTableLabel('scheduling_requests')).toBe('请假与换班')
    expect(getTableLabel('wecom_push_subscriptions')).toBe('企微推送订阅')
    expect(getTableLabel('wecom_push_outbox')).toBe('企微出站队列')
    expect(getTableLabel('overtime_entries')).toBe('加班与补钟')
    expect(getTableLabel('statutory_holidays')).toBe('法定节假日')
    expect(getTableLabel('attendance_ledger')).toBe('月度考勤台账')
    expect(getTableLabel('wastage_records')).toBe('损耗登记')
    expect(getTableLabel('prep_plan_snapshots')).toBe('备货预测快照')
    expect(getTableLabel('hygiene_wecom_shares')).toBe('验收照片投递')
  })

  it('映射缺失时回退为原始表名', () => {
    expect(getTableLabel('unknown_table')).toBe('unknown_table')
  })
})

describe('getColumnLabel', () => {
  it('返回 table.column 映射命中的中文列名', () => {
    expect(getColumnLabel('orders', 'dish_name')).toBe('菜品名称')
    expect(getColumnLabel('wecom_push_logs', 'webhook_name')).toBe('Webhook名称')
    expect(getColumnLabel('wecom_push_logs', 'sent_at')).toBe('发送时间')
    expect(getColumnLabel('app_settings', 'key')).toBe('配置键')
    expect(getColumnLabel('sop_recipes', 'needs_review')).toBe('待复核')
    expect(getColumnLabel('hygiene_daily_items', 'zone_id')).toBe('区域ID')
    expect(getColumnLabel('hygiene_capture_variants', 'content_sha256')).toBe('内容SHA256')
  })

  it('新表的列名有中文（同名列按表覆盖成更具体的叫法）', () => {
    expect(getColumnLabel('staff_assignments', 'shift_id')).toBe('班次ID')
    expect(getColumnLabel('staff_shifts', 'duty_slot')).toBe('卫生档位')
    expect(getColumnLabel('scheduling_requests', 'peer_employee_id')).toBe('换班对象')
    expect(getColumnLabel('wecom_push_outbox', 'last_error')).toBe('最后错误')
    expect(getColumnLabel('attendance_ledger', 'opening_balance')).toBe('期初结余')
    expect(getColumnLabel('attendance_ledger', 'shift_name')).toBe('本月班次')
    expect(getColumnLabel('wastage_records', 'item_name')).toBe('物品名称')
    expect(getColumnLabel('wastage_records', 'reason')).toBe('损耗原因')
    expect(getColumnLabel('prep_plan_snapshots', 'snapshot_at')).toBe('快照时间')
    expect(getColumnLabel('prep_plan_actuals', 'sold_qty')).toBe('实际销量')
    expect(getColumnLabel('seniority_bonus_changes', 'changed_by')).toBe('修改人')
  })

  it('多租户列在每张表都显示为租户ID', () => {
    expect(getColumnLabel('orders', 'tenant_id')).toBe('租户ID')
    expect(getColumnLabel('staff_shifts', 'tenant_id')).toBe('租户ID')
    expect(getColumnLabel('wastage_records', 'tenant_id')).toBe('租户ID')
  })

  it('映射缺失时回退为原始列名', () => {
    expect(getColumnLabel('orders', 'unknown_column')).toBe('unknown_column')
  })

  it('orders.source 列显示为堂食/外卖', () => {
    expect(getColumnLabel('orders', 'source')).toBe('堂食/外卖')
  })
})

describe('getCellLabel', () => {
  it('把订单来源码显示成堂食或外卖', () => {
    expect(getCellLabel('orders', 'source', 'dine_in')).toBe('堂食')
    expect(getCellLabel('orders', 'source', 'delivery')).toBe('外卖')
  })

  it('把排班 / 加班 / 出站 / 损耗的状态码翻成中文', () => {
    expect(getCellLabel('scheduling_requests', 'status', 'pending_manager')).toBe('待店长审批')
    expect(getCellLabel('scheduling_requests', 'status', 'pending_peer')).toBe('待对方确认')
    expect(getCellLabel('scheduling_requests', 'kind', 'swap')).toBe('换班')
    expect(getCellLabel('staff_shifts', 'duty_slot', 'night')).toBe('夜班')
    expect(getCellLabel('overtime_entries', 'status', 'voided')).toBe('已作废')
    expect(getCellLabel('wecom_push_outbox', 'status', 'skipped')).toBe('已跳过')
    expect(getCellLabel('wastage_records', 'reason', 'spoiled')).toBe('变质腐败')
  })

  it('布尔列 0/1 显示成否/是', () => {
    expect(getCellLabel('staff_shifts', 'is_active', 1)).toBe('是')
    expect(getCellLabel('wecom_push_subscriptions', 'enabled', 0)).toBe('否')
    expect(getCellLabel('orders', 'is_hold', 1)).toBe('是')
    expect(getCellLabel('staff_assignments', 'half_rest', 0)).toBe('否')
  })

  it('未映射的值原样返回', () => {
    expect(getCellLabel('orders', 'source', 'other')).toBe('other')
    expect(getCellLabel('orders', 'dish_name', '虾饺')).toBe('虾饺')
    // 数字列不走布尔映射（sort_order 这类值必须照原样显示）
    expect(getCellLabel('staff_shifts', 'sort_order', 1)).toBe(1)
  })
})
