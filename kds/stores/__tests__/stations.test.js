import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/stations.js', () => ({
  stationsAPI: {
    getStations: vi.fn(),
    getStationStats: vi.fn()
  }
}))

import { stationsAPI } from '../../api/stations.js'
import { useStationsStore } from '../stations.js'

const API_STATIONS = [
  { id: 'changfen', name: '肠粉档', color: '#4ECDC4' },
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
    expect(store.stationList.map((station) => station.id)).toEqual(['changfen', 'shulong'])
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
