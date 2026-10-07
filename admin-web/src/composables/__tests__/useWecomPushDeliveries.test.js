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
  useWecomPush, deliveryQuery, deliveryStatusLabel, formatDeliveryTime,
  DELIVERY_STATUS_OPTIONS, DELIVERY_PAGE_SIZE,
} = await import('../useWecomPush.js')

/** 一页发送记录（读接口给的就是这个形状）。 */
function deliveriesPayload(overrides = {}) {
  return {
    success: true,
    rows: [
      {
        id: 11,
        created_at: '2026-10-05T21:30:12+08:00',
        finished_at: '2026-10-05T21:30:13+08:00',
        channel_id: 2,
        channel_name: '日报群',
        topic_id: 'sales_report',
        topic_name: '销售报表',
        status: 'sent',
        message_bytes: 512,
        attempts: 1,
        last_error: '',
        content_summary: '【销售报表】2026-10-05',
      },
    ],
    total: 7,
    page: 1,
    page_size: 50,
    pages: 1,
    ...overrides,
  }
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiPut.mockReset()
  apiDelete.mockReset()
  apiGet.mockResolvedValue(deliveriesPayload())
})

describe('发送记录的查询参数', () => {
  it('默认什么都不筛：只带页码与页大小', () => {
    expect(deliveryQuery({})).toEqual({ page: 1, page_size: DELIVERY_PAGE_SIZE })
    expect(deliveryQuery({ topicId: '', channelId: '', status: '' })).toEqual({
      page: 1, page_size: DELIVERY_PAGE_SIZE,
    })
  })

  it('三项筛选取值都带上去，供后端按内容类型 / 渠道 / 状态过滤', () => {
    expect(deliveryQuery({
      topicId: 'hygiene_reminder', channelId: 3, status: 'failed', page: 2,
    })).toEqual({
      page: 2,
      page_size: DELIVERY_PAGE_SIZE,
      topic_id: 'hygiene_reminder',
      channel_id: 3,
      status: 'failed',
    })
  })

  it('翻页只改页码，筛选条件跟着走', () => {
    expect(deliveryQuery({ topicId: 'sales_report', page: 3 }).page).toBe(3)
    expect(deliveryQuery({ topicId: 'sales_report', page: 3 }).topic_id).toBe('sales_report')
  })
})

describe('发送记录的加载', () => {
  it('带上筛选与分页请求 /logs，并收下总数与页数', async () => {
    const { deliveries, loadDeliveries } = useWecomPush()
    deliveries.filters.topicId = 'hygiene_reminder'
    deliveries.filters.status = 'failed'

    await loadDeliveries()

    expect(apiGet).toHaveBeenCalledWith('/api/wecom-push/logs', {
      page: 1,
      page_size: DELIVERY_PAGE_SIZE,
      topic_id: 'hygiene_reminder',
      status: 'failed',
    })
    expect(deliveries.rows.map((row) => row.id)).toEqual([11])
    expect(deliveries.total).toBe(7)
    expect(deliveries.pages).toBe(1)
  })

  it('改筛选条件回到第一页（否则会停在一个已经不存在的页码上）', async () => {
    const { deliveries, loadDeliveries, applyDeliveryFilters } = useWecomPush()
    deliveries.page = 4

    await applyDeliveryFilters({ topicId: 'sales_report', channelId: '', status: 'sent' })

    expect(apiGet).toHaveBeenCalledWith(
      '/api/wecom-push/logs',
      expect.objectContaining({ page: 1, topic_id: 'sales_report', status: 'sent' }),
    )
  })

  it('翻页请求目标页，筛选照旧', async () => {
    const { loadDeliveries } = useWecomPush()

    await loadDeliveries({ page: 2 })

    expect(apiGet).toHaveBeenCalledWith(
      '/api/wecom-push/logs',
      expect.objectContaining({ page: 2 }),
    )
  })

  it('后端拒绝时不吞错，交给调用方提示', async () => {
    apiGet.mockRejectedValue(new Error('请求失败 (500)'))
    const { loadDeliveries } = useWecomPush()

    await expect(loadDeliveries()).rejects.toThrow('请求失败 (500)')
  })
})

// U18：后端对 `page` 只做下界（≥1），不夹上界。超过保留期的记录被清理后 `pages` 会变小，
// 而页面停在原来的页码上，于是收到一页空表 + 自相矛盾的「第 2 / 1 页」（「上一页」还可点）。
describe('发送记录的分页夹取（U18）', () => {
  it('收到 page > pages 时回落到最后一页并重拉一次', async () => {
    apiGet
      .mockResolvedValueOnce(deliveriesPayload({ page: 3, pages: 1, rows: [], total: 3 }))
      .mockResolvedValueOnce(deliveriesPayload({ page: 1, pages: 1, total: 3 }))
    const { deliveries, loadDeliveries } = useWecomPush()

    await loadDeliveries({ page: 3 })

    expect(apiGet).toHaveBeenCalledTimes(2)
    expect(apiGet).toHaveBeenLastCalledWith(
      '/api/wecom-push/logs',
      expect.objectContaining({ page: 1 }),
    )
    expect(deliveries.page).toBe(1)
    expect(deliveries.rows.map((row) => row.id)).toEqual([11])
  })

  it('一条记录都没有时也回落（pages = 0 ⇒ 回第 1 页）', async () => {
    apiGet
      .mockResolvedValueOnce(deliveriesPayload({ page: 2, pages: 0, rows: [], total: 0 }))
      .mockResolvedValueOnce(deliveriesPayload({ page: 1, pages: 0, rows: [], total: 0 }))
    const { deliveries, loadDeliveries } = useWecomPush()

    await loadDeliveries({ page: 2 })

    expect(apiGet).toHaveBeenCalledTimes(2)
    expect(deliveries.page).toBe(1)
  })

  it('后端给出不可能的组合时只重拉一次，不会无限递归', async () => {
    // 每次都回 page 5 / pages 1：夹取只允许发生一次
    apiGet.mockResolvedValue(deliveriesPayload({ page: 5, pages: 1, rows: [], total: 0 }))
    const { loadDeliveries } = useWecomPush()

    await loadDeliveries({ page: 5 })

    expect(apiGet).toHaveBeenCalledTimes(2)
  })
})

describe('发送记录的展示口径', () => {
  it('五种状态各有中文名，未知状态原样显示（不吞掉这一行）', () => {
    expect(DELIVERY_STATUS_OPTIONS.map((item) => item.id)).toEqual([
      'pending', 'sending', 'sent', 'failed', 'skipped',
    ])
    expect(deliveryStatusLabel('failed')).toBe('失败')
    expect(deliveryStatusLabel('sent')).toBe('已发')
    expect(deliveryStatusLabel('pending')).toBe('待发')
    expect(deliveryStatusLabel('sending')).toBe('发送中')
    expect(deliveryStatusLabel('skipped')).toBe('已跳过')
    expect(deliveryStatusLabel('weird')).toBe('weird')
  })

  it('时间按秒截断显示，空值显示占位而不是 Invalid Date', () => {
    expect(formatDeliveryTime('2026-10-05T21:30:12+08:00')).toBe('2026-10-05 21:30:12')
    expect(formatDeliveryTime('')).toBe('—')
    expect(formatDeliveryTime(null)).toBe('—')
  })
})
