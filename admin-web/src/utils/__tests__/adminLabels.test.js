import { describe, expect, it } from 'vitest'
import { getColumnLabel, getTableLabel } from '../adminLabels.js'

describe('getTableLabel', () => {
  it('返回映射命中的中文表名', () => {
    expect(getTableLabel('orders')).toBe('订单记录')
    expect(getTableLabel('wecom_push_jobs')).toBe('企微推送任务')
    expect(getTableLabel('auth')).toBe('登录认证')
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
  })

  it('映射缺失时回退为原始列名', () => {
    expect(getColumnLabel('orders', 'unknown_column')).toBe('unknown_column')
  })
})
