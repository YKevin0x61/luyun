import { describe, expect, it, vi } from 'vitest'
import { createMaintenanceNotice } from '../useMaintenanceNotice'

function jsonResponse(payload, ok = true) {
  return { ok, json: async () => payload }
}

describe('createMaintenanceNotice', () => {
  it('shows the server message while an update or restore is active', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({ active: true, kind: 'update', message: '系统正在执行数据库迁移。' }),
    )
    const controller = createMaintenanceNotice({ fetchImpl })

    await controller.refresh()

    expect(fetchImpl).toHaveBeenCalledWith('/api/system/maintenance', expect.objectContaining({ cache: 'no-store' }))
    expect(controller.visible.value).toBe(true)
    expect(controller.message.value).toBe('系统正在执行数据库迁移。')
  })

  it('hides the banner again once maintenance ends', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ active: true, message: '系统正在恢复数据。' }))
      .mockResolvedValueOnce(jsonResponse({ active: false }))
    const controller = createMaintenanceNotice({ fetchImpl })

    await controller.refresh()
    expect(controller.visible.value).toBe(true)
    await controller.refresh()
    expect(controller.visible.value).toBe(false)
    expect(controller.message.value).toBe('')
  })

  it('keeps a known maintenance state when the probe becomes unreachable', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ active: true, message: '系统正在重启。' }))
      .mockRejectedValueOnce(new Error('network down'))
    const controller = createMaintenanceNotice({ fetchImpl })

    await controller.refresh()
    await controller.refresh()

    expect(controller.visible.value).toBe(true)
    expect(controller.message.value).toContain('系统正在重启。')
    expect(controller.message.value).toContain('暂时连接不上服务器')
  })

  it('does not claim maintenance just because the probe failed', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('network down'))
    const controller = createMaintenanceNotice({ fetchImpl })

    await controller.refresh()

    expect(controller.visible.value).toBe(false)
    expect(controller.unreachable.value).toBe(true)
  })
})
