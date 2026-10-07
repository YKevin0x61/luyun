import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
const apiPost = vi.fn()
const apiPut = vi.fn()
const apiDelete = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
    put: (...args) => apiPut(...args),
    post: (...args) => apiPost(...args),
    delete: (...args) => apiDelete(...args),
  },
}))

const {
  useWecomPush, auditQuery, auditActionLabel, auditObjectLabel, auditFieldChanges,
  auditVisibleFields, auditFieldLabel, formatAuditValue, AUDIT_PAGE_SIZE,
  AUDIT_OBJECT_LABELS,
} = await import('../useWecomPush.js')

/** 一页变更历史（读接口给的就是这个形状）。 */
function auditPayload(overrides = {}) {
  return {
    success: true,
    rows: [
      {
        id: 12,
        created_at: '2026-10-05T21:30:12+08:00',
        actor: 'admin',
        action: 'update',
        object_type: 'wecom_push_subscriptions',
        object_id: 4,
        object_name: '销售报表 → 门店群',
        before: { enabled: true, topic_id: 'sales_report' },
        after: { enabled: false, topic_id: 'sales_report' },
      },
    ],
    total: 1,
    page: 1,
    page_size: 50,
    pages: 1,
    object_types: [
      { id: 'wecom_push_webhooks', name: '推送渠道' },
      { id: 'wecom_channel_groups', name: '渠道群组' },
      { id: 'wecom_push_subscriptions', name: '推送订阅' },
      { id: 'wecom_push_jobs', name: '推送任务' },
    ],
    actions: [
      { id: 'create', name: '新增' },
      { id: 'update', name: '修改' },
      { id: 'enable', name: '启用' },
      { id: 'disable', name: '停用' },
      { id: 'delete', name: '删除' },
    ],
    ...overrides,
  }
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiPut.mockReset()
  apiDelete.mockReset()
  apiGet.mockResolvedValue(auditPayload())
})

describe('变更历史的查询参数', () => {
  it('默认不带对象类型（后端会给全部），只带页码与页大小', () => {
    expect(auditQuery({})).toEqual({ page: 1, page_size: AUDIT_PAGE_SIZE })
    expect(auditQuery({ objectType: '' })).toEqual({ page: 1, page_size: AUDIT_PAGE_SIZE })
  })

  it('选了对象类型就带上去，翻页也跟着走', () => {
    expect(auditQuery({ objectType: 'wecom_push_jobs', page: 3 })).toEqual({
      page: 3,
      page_size: AUDIT_PAGE_SIZE,
      object_type: 'wecom_push_jobs',
    })
  })

  it('页大小与后端 /audit-log 的默认值一致', () => {
    expect(AUDIT_PAGE_SIZE).toBe(50)
  })
})

describe('变更历史的加载', () => {
  it('请求 /audit-log 并收下记录、总数与筛选项', async () => {
    const { audit, loadAuditLog } = useWecomPush()

    await loadAuditLog()

    expect(apiGet).toHaveBeenCalledWith('/api/wecom-push/audit-log', {
      page: 1,
      page_size: AUDIT_PAGE_SIZE,
    })
    expect(audit.rows.map((row) => row.id)).toEqual([12])
    expect(audit.total).toBe(1)
    // 筛选项来自后端：加一种对象类型只改后端，页面不维护第二份名单
    expect(audit.objectTypes.map((item) => item.id)).toEqual([
      'wecom_push_webhooks', 'wecom_channel_groups',
      'wecom_push_subscriptions', 'wecom_push_jobs',
    ])
  })

  it('按对象类型筛选，并回到第一页', async () => {
    const { audit, loadAuditLog, applyAuditFilters } = useWecomPush()
    await loadAuditLog()
    audit.page = 4

    await applyAuditFilters({ objectType: 'wecom_push_jobs' })

    expect(apiGet).toHaveBeenLastCalledWith('/api/wecom-push/audit-log', {
      page: 1,
      page_size: AUDIT_PAGE_SIZE,
      object_type: 'wecom_push_jobs',
    })
    expect(audit.filters.objectType).toBe('wecom_push_jobs')
  })

  it('翻页只改页码，筛选条件跟着走', async () => {
    const { loadAuditLog, applyAuditFilters } = useWecomPush()
    await applyAuditFilters({ objectType: 'wecom_push_channels' })

    await loadAuditLog({ page: 2 })

    expect(apiGet).toHaveBeenLastCalledWith(
      '/api/wecom-push/audit-log',
      expect.objectContaining({ page: 2, object_type: 'wecom_push_channels' }),
    )
  })

  it('后端拒绝时不吞错，交给调用方提示', async () => {
    apiGet.mockRejectedValue(new Error('请求失败 (500)'))
    const { loadAuditLog } = useWecomPush()

    await expect(loadAuditLog()).rejects.toThrow('请求失败 (500)')
  })
})

describe('变更历史的展示口径', () => {
  it('五种动作与四种对象都有中文名；后端给了名字就用后端的', () => {
    expect(auditActionLabel('create')).toBe('新增')
    expect(auditActionLabel('update')).toBe('修改')
    expect(auditActionLabel('enable')).toBe('启用')
    expect(auditActionLabel('disable')).toBe('停用')
    expect(auditActionLabel('delete')).toBe('删除')
    // 认不出的动作退回原值（不吞掉这一行）
    expect(auditActionLabel('weird')).toBe('weird')

    expect(auditObjectLabel('wecom_push_webhooks')).toBe('推送渠道')
    expect(auditObjectLabel('wecom_channel_groups')).toBe('渠道群组')
    expect(auditObjectLabel('wecom_push_subscriptions')).toBe('推送订阅')
    expect(auditObjectLabel('wecom_push_jobs')).toBe('推送任务')
    expect(auditObjectLabel('weird')).toBe('weird')

    expect(AUDIT_OBJECT_LABELS.wecom_push_jobs).toBe('推送任务')
  })

  it('变更内容算的是前后差异（页面上那一列直接读它）', () => {
    const changes = auditFieldChanges({
      before: { name: '门店群', enabled: true },
      after: { name: '门店群（改名）', enabled: true },
    })

    expect(Object.keys(changes)).toEqual(['name'])
    expect(changes.name).toEqual({ before: '门店群', after: '门店群（改名）' })
  })

  it('页面上不显示行内 id 与已用名字表达过的外键（记录里还在）', () => {
    const row = {
      before: { id: 4, topic_id: 'sales_report', target_channel_id: 2, target_name: '门店群', enabled: true },
      after: { id: 4, topic_id: 'sales_report', target_channel_id: 2, target_name: '日报群', enabled: false },
    }

    // 差异本身算得出来（记录里有这些字段，排查要靠它们）
    expect(Object.keys(auditFieldChanges(row))).toEqual(['target_name', 'enabled'])
    // 页面上只渲染「目标 + 启停」：id 是页面的 key、不是人读的变更
    expect(auditVisibleFields(row)).toEqual([
      ['target_name', { before: '门店群', after: '日报群' }],
      ['enabled', { before: true, after: false }],
    ])
    // 外键真的变了也照样不显示（名字那一项已经说了这件事）
    expect(auditVisibleFields({
      before: { target_channel_id: 2, target_name: '门店群' },
      after: { target_channel_id: 3, target_name: '日报群' },
    })).toEqual([['target_name', { before: '门店群', after: '日报群' }]])
  })

  it('新建与删除整份快照都算变更（没有改前 / 没有改后）', () => {
    expect(auditFieldChanges({ before: {}, after: { name: '门店群' } })).toEqual({
      name: { before: null, after: '门店群' },
    })
    expect(auditFieldChanges({ before: { name: '门店群' }, after: {} })).toEqual({
      name: { before: '门店群', after: null },
    })
  })

  it('字段名翻成中文，认不出的原样显示（不吞掉这一项）', () => {
    expect(auditFieldLabel('enabled')).toBe('启用')
    expect(auditFieldLabel('name')).toBe('名称')
    expect(auditFieldLabel('url')).toBe('Webhook 地址')
    expect(auditFieldLabel('target_name')).toBe('目标')
    expect(auditFieldLabel('members')).toBe('成员')
    expect(auditFieldLabel('weird_field')).toBe('weird_field')
  })

  it('值渲染：空值有占位、布尔与数组按人话显示', () => {
    expect(formatAuditValue(true)).toBe('启用')
    expect(formatAuditValue(false)).toBe('停用')
    expect(formatAuditValue(null)).toBe('（空）')
    expect(formatAuditValue('')).toBe('（空）')
    expect(formatAuditValue(['门店群', '日报群'])).toBe('门店群、日报群')
    expect(formatAuditValue('门店群')).toBe('门店群')
  })
})
