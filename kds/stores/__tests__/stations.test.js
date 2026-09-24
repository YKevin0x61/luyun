import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/stations.js', () => ({
  stationsAPI: {
    getStations: vi.fn(),
    getStationStats: vi.fn()
  }
}))

vi.mock('../../utils/request.js', () => ({
  request: vi.fn()
}))

import { stationsAPI } from '../../api/stations.js'
import { request } from '../../utils/request.js'
import { useStationsStore } from '../stations.js'

const API_STATIONS = [
  { id: 'changfen', name: '肠粉档', color: '#4ECDC4' },
  { id: 'jianzha', name: '煎炸档', color: '#FF9FF3' },
  {
    id: 'shulong',
    name: '熟笼档',
    color: '#45B7D1',
    steamer_layout: {
      steamers: [
        { id: '1', port_count: 6 },
        { id: '2', port_count: 6 }
      ],
      port_capacity: 10,
      awaiting_cancel_notice_seconds: 180
    }
  },
  { id: 'loumian', name: '楼面', color: '#A78BFA' }
]

describe('stations store catalog', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(stationsAPI.getStations).mockReset()
  })

  it('starts empty until /api/stations is fetched', () => {
    const store = useStationsStore()
    expect(store.stationList).toEqual([])
    expect(store.steamerLayout).toEqual({
      steamers: [],
      portCapacity: 0,
      awaitingCancelNoticeSeconds: 180
    })
  })

  it('loads the catalog and hides 楼面 from stationList', async () => {
    vi.mocked(stationsAPI.getStations).mockResolvedValue(API_STATIONS)
    const store = useStationsStore()
    await store.initializeStations()
    expect(store.stationList.map((station) => station.id)).toEqual([
      'changfen',
      'jianzha',
      'shulong'
    ])
    expect(store.getStationColor('jianzha')).toBe('#FF9FF3')
    expect(store.getStationById('loumian')?.name).toBe('楼面')
    expect(store.steamerLayout).toEqual({
      steamers: [
        { id: '1', portCount: 6 },
        { id: '2', portCount: 6 }
      ],
      portCapacity: 10,
      awaitingCancelNoticeSeconds: 180
    })
  })

  it('clears the catalog when fetch fails', async () => {
    vi.mocked(stationsAPI.getStations).mockRejectedValue(new Error('network'))
    const store = useStationsStore()
    await expect(store.initializeStations()).resolves.toBe(false)
    expect(store.stationList).toEqual([])
    expect(store.steamerLayout.steamers).toEqual([])
  })
})

describe('stations stats parameter surface (票 24)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(stationsAPI.getStations).mockReset()
    vi.mocked(stationsAPI.getStationStats).mockReset()
    vi.mocked(request).mockReset()
  })

  it('fetchStationStats asks only for the station id', async () => {
    vi.mocked(stationsAPI.getStations).mockResolvedValue(API_STATIONS)
    vi.mocked(stationsAPI.getStationStats).mockResolvedValue({
      success: true,
      data: { orderCount: 3 }
    })
    const store = useStationsStore()
    await store.initializeStations()

    await store.fetchStationStats('changfen')

    expect(vi.mocked(stationsAPI.getStationStats).mock.calls).toEqual([['changfen']])
    expect(store.stationStats.changfen).toEqual({ orderCount: 3 })
  })

  it('refreshStationData never forwards a date argument', async () => {
    vi.mocked(stationsAPI.getStations).mockResolvedValue(API_STATIONS)
    vi.mocked(stationsAPI.getStationStats).mockResolvedValue({
      success: true,
      data: { orderCount: 1 }
    })
    const store = useStationsStore()
    await store.initializeStations()

    await store.refreshStationData()

    const calls = vi.mocked(stationsAPI.getStationStats).mock.calls
    expect(calls.length).toBeGreaterThan(0)
    for (const call of calls) {
      expect(call).toHaveLength(1)
      expect(typeof call[0]).toBe('string')
    }
    expect(store.stationStats.changfen).toEqual({ orderCount: 1 })
  })

  it('the API layer never sends a date query param (server has no such param)', async () => {
    const { stationsAPI: realStationsAPI } = await vi.importActual('../../api/stations.js')
    vi.mocked(request).mockResolvedValue({ success: true, data: { orderCount: 3 } })

    // 第二个实参是历史遗留的死参数：即使调用方仍传日期，也不得进入请求参数。
    const response = await realStationsAPI.getStationStats('changfen', '2026-05-02')

    expect(response).toEqual({ success: true, data: { orderCount: 3 } })
    expect(vi.mocked(request)).toHaveBeenCalledTimes(1)
    const config = vi.mocked(request).mock.calls[0][0]
    expect(config.url).toBe('/api/orders/station/changfen/stats')
    expect(config.method).toBe('GET')
    expect(config.params ?? {}).not.toHaveProperty('date')
  })
})
