import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  deriveSteamerPhase,
  isSteamerConsole,
  advanceAwaitingGroupSelection,
  awaitingGroupSelectedCount,
  composeAwaitingSteamerGroups,
  groupAwaitingSteamerCages,
  listAwaitingSteamerCages,
  sortAwaitingCagesFifo,
  SHULONG_STEAMER_LAYOUT,
  steamerBasketServeIntent,
  steamerHoleTapIntent,
  steamerLoadIntent,
  steamerPluckIntent,
  steamerUnloadIntent,
  toggleSteamerCageSelection,
  toggleSteamerSelection,
  selectAllHoleCages,
  isHoleFullySelected,
  sortHoleDisplay,
  fillHoleSlots,
  formatSteamerTableLabel,
  formatSteamerCageCard,
  steamerAwaitingPlacement,
  steamerLayoutFromStations,
  steamUrgencyLevel
} from '../steamerConsole.js'

describe('deriveSteamerPhase', () => {
  it('treats 待出餐 without placement as 待上笼', () => {
    expect(deriveSteamerPhase({ dish_status: '待出餐' })).toBe('待上笼')
    expect(deriveSteamerPhase({ dish_status: '待出餐', placement: null })).toBe('待上笼')
  })

  it('treats 待出餐 with placement as 在蒸', () => {
    expect(
      deriveSteamerPhase({
        dish_status: '待出餐',
        placement: {
          steamer_id: '1',
          port_index: 3,
          stack_order: 1,
          loaded_at: '2026-08-14T10:05:00+08:00'
        }
      })
    ).toBe('在蒸')
  })

  it('does not invent a 出餐状态 from phase', () => {
    const steaming = deriveSteamerPhase({
      dish_status: '待出餐',
      placement: { steamer_id: '1', port_index: 1, stack_order: 1, loaded_at: 't' }
    })
    expect(steaming).toBe('在蒸')
    expect(['待出餐', '已制作待上菜', '退菜/已取消']).not.toContain(steaming)
  })

  const noticeNow = Date.parse('2026-08-14T10:05:00+08:00')
  const noticeSeconds = 180

  it('treats cancelled-with-placement as 退菜占位, not a new dish_status', () => {
    const hold = {
      dish_status: '已取消',
      status: '退菜',
      placement: { steamer_id: '1', port_index: 3, stack_order: 1, loaded_at: 't' },
      updated_at: '2026-08-14T10:04:00+08:00'
    }
    expect(deriveSteamerPhase(hold, { now: noticeNow, noticeSeconds })).toBe('退菜占位')
    expect(hold.dish_status).toBe('已取消')
  })

  it('treats cancelled-without-placement as 待上笼退示 only inside the notice window', () => {
    const notice = {
      dish_status: '已取消',
      status: '退菜',
      placement: null,
      updated_at: '2026-08-14T10:04:00+08:00'
    }
    expect(deriveSteamerPhase(notice, { now: noticeNow, noticeSeconds })).toBe('待上笼退示')
    expect(
      deriveSteamerPhase(notice, {
        now: Date.parse('2026-08-14T10:08:00+08:00'),
        noticeSeconds
      })
    ).toBeNull()
  })

  it('does not treat a plucked hold as 待上笼退示', () => {
    expect(
      deriveSteamerPhase(
        {
          dish_status: '已取消',
          status: '退菜',
          placement: null,
          loaded_at: '2026-08-14T10:05:00+08:00',
          updated_at: '2026-08-14T10:04:00+08:00'
        },
        { now: noticeNow, noticeSeconds }
      )
    ).toBeNull()
  })
})

describe('isSteamerConsole', () => {
  it('is true only for 熟笼档', () => {
    expect(isSteamerConsole({ stationId: 'shulong' })).toBe(true)
    expect(isSteamerConsole({ stationId: 'changfen' })).toBe(false)
    expect(isSteamerConsole({ stationId: 'xibing' })).toBe(false)
    expect(isSteamerConsole({})).toBe(false)
  })
})

describe('steamerLoadIntent', () => {
  it('is a no-op when tapping a hole with no selected cages', () => {
    expect(
      steamerLoadIntent({
        selectedOrderIds: [],
        steamerId: '1',
        portIndex: 3
      })
    ).toBeNull()
    expect(
      steamerLoadIntent({
        selectedOrderIds: null,
        steamerId: '1',
        portIndex: 3
      })
    ).toBeNull()
  })

  it('builds a load intent from selected awaiting ids and the tapped hole', () => {
    expect(
      steamerLoadIntent({
        selectedOrderIds: ['11', '12'],
        steamerId: '2',
        portIndex: 4
      })
    ).toEqual({
      orderIds: ['11', '12'],
      steamerId: '2',
      portIndex: 4
    })
  })
})

describe('steamerHoleTapIntent', () => {
  const hole = {
    steamerId: '1',
    portIndex: 3,
    occupiedOnHole: 2,
    portCapacity: SHULONG_STEAMER_LAYOUT.portCapacity
  }

  it('is a no-op when tapping a hole with empty selection', () => {
    expect(
      steamerHoleTapIntent({
        awaitingIds: [],
        steamingIds: [],
        ...hole
      })
    ).toBeNull()
    expect(
      steamerHoleTapIntent({
        awaitingIds: null,
        steamingIds: null,
        ...hole
      })
    ).toBeNull()
  })

  it('builds a load intent when only 待上笼 cages are selected', () => {
    expect(
      steamerHoleTapIntent({
        awaitingIds: ['11', '12'],
        steamingIds: [],
        ...hole
      })
    ).toEqual({
      type: 'load',
      orderIds: ['11', '12'],
      steamerId: '1',
      portIndex: 3
    })
  })

  it('keeps 待上笼 selection order in the load intent so stack follows tap order', () => {
    let selected = []
    selected = toggleSteamerSelection(selected, '12')
    selected = toggleSteamerSelection(selected, '11')
    expect(selected).toEqual(['12', '11'])
    expect(
      steamerHoleTapIntent({
        awaitingIds: selected,
        steamingIds: [],
        ...hole
      })
    ).toEqual({
      type: 'load',
      orderIds: ['12', '11'],
      steamerId: '1',
      portIndex: 3
    })
  })

  it('builds a move intent when only 在蒸 cages are selected', () => {
    expect(
      steamerHoleTapIntent({
        awaitingIds: [],
        steamingIds: ['21', '22'],
        ...hole
      })
    ).toEqual({
      type: 'move',
      orderIds: ['21', '22'],
      steamerId: '1',
      portIndex: 3
    })
  })

  it('allows 上笼 and 换孔 on the same 熟笼蒸炉屏', () => {
    expect(
      steamerHoleTapIntent({
        awaitingIds: ['11'],
        steamingIds: [],
        ...hole
      }).type
    ).toBe('load')
    expect(
      steamerHoleTapIntent({
        awaitingIds: [],
        steamingIds: ['21'],
        ...hole
      })
    ).toMatchObject({ type: 'move', orderIds: ['21'] })
  })

  it('rejects a mixed 待上笼 + 在蒸 selection without load or move', () => {
    expect(
      steamerHoleTapIntent({
        awaitingIds: ['11'],
        steamingIds: ['21'],
        ...hole
      })
    ).toEqual({ type: 'reject', reason: 'mixed' })
  })

  it('rejects when occupied plus incoming exceeds port capacity', () => {
    expect(
      steamerHoleTapIntent({
        awaitingIds: ['11', '12'],
        steamingIds: [],
        steamerId: '1',
        portIndex: 3,
        occupiedOnHole: 9,
        portCapacity: 10
      })
    ).toEqual({ type: 'reject', reason: 'capacity' })
    expect(
      steamerHoleTapIntent({
        awaitingIds: [],
        steamingIds: ['99'],
        steamerId: '1',
        portIndex: 3,
        occupiedOnHole: 10,
        portCapacity: 10,
        idsOnHole: ['a', 'b']
      })
    ).toEqual({ type: 'reject', reason: 'capacity' })
  })

  it('does not count selected cages already on the dest hole as incoming', () => {
    expect(
      steamerHoleTapIntent({
        awaitingIds: [],
        steamingIds: ['21', '22'],
        steamerId: '1',
        portIndex: 3,
        occupiedOnHole: 10,
        portCapacity: 10,
        idsOnHole: ['21', '22', '23']
      })
    ).toBeNull()
  })

  it('does not treat 退菜占位 as steaming for load or move', () => {
    expect(
      steamerHoleTapIntent({
        awaitingIds: [],
        steamingIds: [],
        holdIds: ['h1'],
        ...hole
      })
    ).toBeNull()
    expect(
      steamerHoleTapIntent({
        awaitingIds: ['11'],
        steamingIds: [],
        holdIds: ['h1'],
        ...hole
      })
    ).toEqual({ type: 'reject', reason: 'mixed' })
  })
})

describe('steamerUnloadIntent', () => {
  it('builds an unload intent from selected steaming ids; empty is a no-op', () => {
    expect(steamerUnloadIntent({ selectedOrderIds: ['21', '22'] })).toEqual({
      orderIds: ['21', '22']
    })
    expect(steamerUnloadIntent({ selectedOrderIds: [] })).toBeNull()
    expect(steamerUnloadIntent({ selectedOrderIds: null })).toBeNull()
  })
})

describe('笼上出餐 selection', () => {
  it('tapping a 在蒸 cage only toggles selection and does not build a serve intent', () => {
    let selected = []
    selected = toggleSteamerSelection(selected, '21')
    expect(selected).toEqual(['21'])
    selected = toggleSteamerSelection(selected, '22')
    expect(selected).toEqual(['21', '22'])
    selected = toggleSteamerSelection(selected, '21')
    expect(selected).toEqual(['22'])
    expect(steamerBasketServeIntent({ selectedOrderIds: [] })).toBeNull()
  })

  it('confirm with selected steaming ids builds a serve intent; empty confirm is a no-op', () => {
    expect(steamerBasketServeIntent({ selectedOrderIds: ['21', '22'] })).toEqual({
      orderIds: ['21', '22']
    })
    expect(steamerBasketServeIntent({ selectedOrderIds: [] })).toBeNull()
    expect(steamerBasketServeIntent({ selectedOrderIds: null })).toBeNull()
  })

  it('confirm with selected awaiting ids builds a serve intent; empty awaiting is a no-op', () => {
    expect(
      steamerBasketServeIntent({
        awaitingIds: ['11', '12'],
        steamingIds: []
      })
    ).toEqual({
      orderIds: ['11', '12']
    })
    expect(
      steamerBasketServeIntent({
        awaitingIds: [],
        steamingIds: []
      })
    ).toBeNull()
  })

  it('refuses mixed 待上笼 + 在蒸 serve on one confirm', () => {
    expect(
      steamerBasketServeIntent({
        awaitingIds: ['11'],
        steamingIds: ['21']
      })
    ).toEqual({ type: 'reject', reason: 'mixed' })
  })
})

describe('退菜占位 selection and 抽笼', () => {
  it('refuses mixed 在蒸 + 退菜占位 and keeps the previous selection', () => {
    const steamingOnly = toggleSteamerCageSelection({
      steamingIds: ['21'],
      holdIds: [],
      orderId: 'h1',
      phase: '退菜占位'
    })
    expect(steamingOnly).toEqual({
      awaitingIds: [],
      steamingIds: ['21'],
      holdIds: []
    })

    const holdOnly = toggleSteamerCageSelection({
      steamingIds: [],
      holdIds: ['h1'],
      orderId: '21',
      phase: '在蒸'
    })
    expect(holdOnly).toEqual({
      awaitingIds: [],
      steamingIds: [],
      holdIds: ['h1']
    })
  })

  it('tapping a 退菜占位 only toggles hold selection and does not build a pluck intent', () => {
    const afterTap = toggleSteamerCageSelection({
      steamingIds: [],
      holdIds: [],
      orderId: 'h1',
      phase: '退菜占位'
    })
    expect(afterTap.holdIds).toEqual(['h1'])
    expect(steamerPluckIntent({ selectedHoldIds: [] })).toBeNull()
    expect(steamerPluckIntent({ selectedHoldIds: null })).toBeNull()
  })

  it('builds a pluck intent only from selected holds plus explicit 抽走', () => {
    expect(steamerPluckIntent({ selectedHoldIds: ['h1', 'h2'] })).toEqual({
      orderIds: ['h1', 'h2']
    })
  })

  it('does not add 待上笼退示 to load or serve selection', () => {
    const next = toggleSteamerCageSelection({
      awaitingIds: ['11'],
      steamingIds: [],
      holdIds: [],
      orderId: 'n1',
      phase: '待上笼退示'
    })
    expect(next).toEqual({
      awaitingIds: ['11'],
      steamingIds: [],
      holdIds: []
    })
    expect(
      steamerBasketServeIntent({
        awaitingIds: next.awaitingIds,
        steamingIds: next.steamingIds
      })
    ).toEqual({ orderIds: ['11'] })
    expect(
      steamerHoleTapIntent({
        awaitingIds: next.awaitingIds,
        steamingIds: next.steamingIds,
        steamerId: '1',
        portIndex: 3,
        occupiedOnHole: 0,
        portCapacity: 10
      })
    ).toEqual({
      type: 'load',
      orderIds: ['11'],
      steamerId: '1',
      portIndex: 3
    })
  })
})

describe('selectAllHoleCages', () => {
  const now = Date.parse('2026-08-14T10:12:00+08:00')

  it('is a no-op on an empty hole', () => {
    expect(
      selectAllHoleCages({
        cagesOnHole: [],
        awaitingIds: ['a1'],
        steamingIds: ['s9'],
        holdIds: []
      })
    ).toEqual({
      awaitingIds: ['a1'],
      steamingIds: ['s9'],
      holdIds: []
    })
    expect(isHoleFullySelected({ cagesOnHole: [], steamingIds: ['s9'] })).toBe(false)
  })

  it('selects every 在蒸 cage on the hole and clears 退菜占位 plus 待上笼', () => {
    const cages = [
      steamingCage({ id: 's1' }),
      steamingCage({ id: 's2' })
    ]
    const next = selectAllHoleCages({
      cagesOnHole: cages,
      awaitingIds: ['a1'],
      steamingIds: [],
      holdIds: ['h1'],
      now
    })
    expect(next).toEqual({
      awaitingIds: [],
      steamingIds: ['s1', 's2'],
      holdIds: []
    })
    expect(isHoleFullySelected({ cagesOnHole: cages, steamingIds: next.steamingIds, now })).toBe(true)
  })

  it('keeps other holes and toggles this hole off when already fully selected', () => {
    const cages = [steamingCage({ id: 's1' }), steamingCage({ id: 's2' })]
    const next = selectAllHoleCages({
      cagesOnHole: cages,
      steamingIds: ['s9', 's1', 's2'],
      holdIds: [],
      now
    })
    expect(next.steamingIds).toEqual(['s9'])
    expect(isHoleFullySelected({ cagesOnHole: cages, steamingIds: next.steamingIds, now })).toBe(false)
  })

  it('selects 在蒸 only when the hole also has 退菜占位', () => {
    const cages = [
      steamingCage({ id: 's1' }),
      steamingCage({
        id: 'h1',
        dish_status: '已取消',
        status: '退菜'
      })
    ]
    const next = selectAllHoleCages({
      cagesOnHole: cages,
      steamingIds: [],
      holdIds: [],
      now
    })
    expect(next.steamingIds).toEqual(['s1'])
    expect(next.holdIds).toEqual([])
  })

  it('selects every 退菜占位 when the hole has no 在蒸', () => {
    const cages = [
      steamingCage({
        id: 'h1',
        dish_status: '已取消',
        status: '退菜'
      }),
      steamingCage({
        id: 'h2',
        dish_status: '已取消',
        status: '退菜'
      })
    ]
    const next = selectAllHoleCages({
      cagesOnHole: cages,
      steamingIds: ['s9'],
      holdIds: [],
      now
    })
    expect(next).toEqual({
      awaitingIds: [],
      steamingIds: [],
      holdIds: ['h1', 'h2']
    })
    expect(isHoleFullySelected({ cagesOnHole: cages, holdIds: next.holdIds, now })).toBe(true)
  })
})

describe('listAwaitingSteamerCages', () => {
  const now = Date.parse('2026-08-14T10:05:00+08:00')
  const noticeSeconds = 180

  it('keeps live 待上笼 and in-window 待上笼退示, drops expired notices', () => {
    const awaiting = {
      _id: 'a1',
      dish_status: '待出餐',
      placement: null
    }
    const notice = {
      _id: 'n1',
      dish_status: '已取消',
      status: '退菜',
      placement: null,
      updated_at: '2026-08-14T10:04:00+08:00'
    }
    const expired = {
      _id: 'e1',
      dish_status: '已取消',
      status: '退菜',
      placement: null,
      updated_at: '2026-08-14T10:00:00+08:00'
    }
    expect(
      listAwaitingSteamerCages([awaiting, notice, expired], { now, noticeSeconds }).map((row) => row._id)
    ).toEqual(['a1', 'n1'])
  })
})

describe('groupAwaitingSteamerCages', () => {
  const now = Date.parse('2026-08-14T10:05:00+08:00')
  const noticeSeconds = 180
  const opts = { now, noticeSeconds }

  it('groups 待上笼 by dish_name and puts 加单 in the matching group', () => {
    const bun = {
      _id: 'b1',
      dish_name: '叉烧包',
      dish_status: '待出餐',
      table_number: '3',
      placement: null
    }
    const dumpling = {
      _id: 'd1',
      dish_name: '虾饺',
      dish_status: '待出餐',
      table_number: '1',
      placement: null
    }
    const extraBun = {
      _id: 'b2',
      dish_name: '叉烧包',
      dish_status: '待出餐',
      table_number: '8',
      placement: null
    }

    const groups = groupAwaitingSteamerCages([bun, dumpling, extraBun], opts)
    expect(groups.map((group) => group.dishName)).toEqual(['叉烧包', '虾饺'])
    expect(groups[0].cages.map((cage) => cage._id)).toEqual(['b1', 'b2'])
    expect(groups[1].cages.map((cage) => cage._id)).toEqual(['d1'])
  })

  it('lists 待上笼退示 in the group but excludes them from selectable cages', () => {
    const live = {
      _id: 'a1',
      dish_name: '虾饺',
      dish_status: '待出餐',
      table_number: '1',
      placement: null
    }
    const notice = {
      _id: 'n1',
      dish_name: '虾饺',
      dish_status: '已取消',
      status: '退菜',
      table_number: '2',
      placement: null,
      updated_at: '2026-08-14T10:04:00+08:00'
    }

    const groups = groupAwaitingSteamerCages([live, notice], opts)
    expect(groups).toHaveLength(1)
    expect(groups[0].cages.map((cage) => cage._id)).toEqual(['a1', 'n1'])
    expect(groups[0].selectableCages.map((cage) => cage._id)).toEqual(['a1'])
    expect(groups[0].noticeCages.map((cage) => cage._id)).toEqual(['n1'])
  })

  it('builds load and serve intents from cage ids, not the group dish name', () => {
    const first = {
      _id: 'a1',
      dish_name: '虾饺',
      dish_status: '待出餐',
      placement: null
    }
    const second = {
      _id: 'a2',
      dish_name: '虾饺',
      dish_status: '待出餐',
      placement: null
    }
    const groups = groupAwaitingSteamerCages([first, second], opts)
    const cageIds = groups[0].selectableCages.map((cage) => cage._id)

    expect(cageIds).toEqual(['a1', 'a2'])
    expect(groups[0].dishName).toBe('虾饺')
    expect(
      steamerLoadIntent({
        selectedOrderIds: cageIds,
        steamerId: '1',
        portIndex: 2
      })
    ).toEqual({
      orderIds: ['a1', 'a2'],
      steamerId: '1',
      portIndex: 2
    })
    expect(steamerBasketServeIntent({ selectedOrderIds: cageIds })).toEqual({
      orderIds: ['a1', 'a2']
    })
    expect(steamerBasketServeIntent({ selectedOrderIds: [groups[0].dishName] })).not.toEqual({
      orderIds: cageIds
    })
  })
})

describe('advanceAwaitingGroupSelection', () => {
  const early = { _id: 'a1', order_time: '2026-08-16T08:00:00+08:00' }
  const mid = { _id: 'a2', order_time: '2026-08-16T08:10:00+08:00' }
  const late = { _id: 'a3', order_time: '2026-08-16T08:20:00+08:00' }
  const cages = [late, early, mid]

  it('sorts 待上笼 FIFO by order_time, missing time last', () => {
    const undated = { _id: 'z9' }
    expect(sortAwaitingCagesFifo([late, undated, early]).map((cage) => cage._id)).toEqual([
      'a1',
      'a3',
      'z9'
    ])
  })

  it('each click takes the next earliest cage; wrap clears the group', () => {
    let selected = []
    selected = advanceAwaitingGroupSelection({ selectableCages: cages, selectedIds: selected })
    expect(selected).toEqual(['a1'])
    selected = advanceAwaitingGroupSelection({ selectableCages: cages, selectedIds: selected })
    expect(selected).toEqual(['a1', 'a2'])
    selected = advanceAwaitingGroupSelection({ selectableCages: cages, selectedIds: selected })
    expect(selected).toEqual(['a1', 'a2', 'a3'])
    selected = advanceAwaitingGroupSelection({ selectableCages: cages, selectedIds: selected })
    expect(selected).toEqual([])
  })

  it('keeps other groups selected and snaps this group to earliest prefix', () => {
    const next = advanceAwaitingGroupSelection({
      selectableCages: cages,
      selectedIds: ['other', 'a3']
    })
    expect(next).toEqual(['other', 'a1', 'a2'])
    expect(awaitingGroupSelectedCount(cages, next)).toBe(2)
  })

  it('does nothing when the group has no selectable cages', () => {
    expect(
      advanceAwaitingGroupSelection({
        selectableCages: [],
        selectedIds: ['other']
      })
    ).toEqual(['other'])
  })
})

describe('composeAwaitingSteamerGroups', () => {
  const now = Date.parse('2026-08-16T12:00:00+08:00')
  const opts = { now, noticeSeconds: 180 }

  function cage(id, dishName, orderTime, extra = {}) {
    return {
      _id: id,
      dish_name: dishName,
      dish_status: '待出餐',
      placement: null,
      quantity: 1,
      order_time: orderTime,
      ...extra
    }
  }

  it('N=0 T=0 keeps one group per dish and sorts by oldest order', () => {
    const { groups } = composeAwaitingSteamerGroups(
      [
        cage('s1', '虾饺', '2026-08-16T08:10:00+08:00'),
        cage('b1', '叉烧包', '2026-08-16T08:00:00+08:00')
      ],
      opts,
      { cap: 0, orderGapMinutes: 0 }
    )
    expect(groups.map((group) => group.dishName)).toEqual(['叉烧包', '虾饺'])
    expect(groups.map((group) => group.totalQuantity)).toEqual([1, 1])
  })

  it('applies 菜卡份数上限 like other kitchen cards', () => {
    const cages = [
      cage('s1', '虾饺', '2026-08-16T08:00:00+08:00'),
      cage('s2', '虾饺', '2026-08-16T08:01:00+08:00'),
      cage('s3', '虾饺', '2026-08-16T08:02:00+08:00'),
      cage('s4', '虾饺', '2026-08-16T08:03:00+08:00'),
      cage('s5', '虾饺', '2026-08-16T08:04:00+08:00')
    ]
    const { groups } = composeAwaitingSteamerGroups(cages, opts, { cap: 2, orderGapMinutes: 0 })
    expect(groups.map((group) => group.totalQuantity)).toEqual([2, 2, 1])
    expect(groups.every((group) => group.dishName === '虾饺')).toBe(true)
    expect(new Set(groups.map((group) => group.chunkId)).size).toBe(3)
  })

  it('applies 下单间隔 so a gap starts a new 待上笼组', () => {
    const cages = [
      cage('s1', '虾饺', '2026-08-16T08:00:00+08:00'),
      cage('s2', '虾饺', '2026-08-16T08:05:00+08:00'),
      cage('s3', '虾饺', '2026-08-16T08:20:00+08:00')
    ]
    const { groups } = composeAwaitingSteamerGroups(cages, opts, { cap: 0, orderGapMinutes: 10 })
    expect(groups.map((group) => group.selectableCages.map((row) => row._id))).toEqual([
      ['s1', 's2'],
      ['s3']
    ])
  })

  it('interleaves same-dish groups with other dishes by oldest order', () => {
    const cages = [
      cage('s1', '虾饺', '2026-08-16T10:00:00+08:00', { quantity: 10 }),
      cage('s2', '虾饺', '2026-08-16T11:00:00+08:00', { quantity: 8 }),
      cage('b1', '叉烧包', '2026-08-16T10:30:00+08:00', { quantity: 3 })
    ]
    const { groups } = composeAwaitingSteamerGroups(cages, opts, { cap: 10, orderGapMinutes: 0 })
    expect(groups.map((group) => group.dishName)).toEqual(['虾饺', '叉烧包', '虾饺'])
    expect(groups.map((group) => group.totalQuantity)).toEqual([10, 3, 8])
  })

  it('pins 待上笼退示 on the earliest group of that dish', () => {
    const cages = [
      cage('s1', '虾饺', '2026-08-16T08:00:00+08:00'),
      cage('s2', '虾饺', '2026-08-16T08:01:00+08:00'),
      cage('s3', '虾饺', '2026-08-16T08:02:00+08:00'),
      {
        _id: 'n1',
        dish_name: '虾饺',
        dish_status: '已取消',
        status: '退菜',
        placement: null,
        updated_at: '2026-08-16T11:59:00+08:00'
      }
    ]
    const { groups } = composeAwaitingSteamerGroups(cages, opts, { cap: 2, orderGapMinutes: 0 })
    expect(groups[0].noticeCages.map((row) => row._id)).toEqual(['n1'])
    expect(groups.slice(1).every((group) => group.noticeCages.length === 0)).toBe(true)
    expect(groups[0].selectableCages.map((row) => row._id)).not.toContain('n1')
  })
})

function steamingCage(overrides = {}) {
  return {
    id: 's1',
    dish_name: '虾饺',
    dish_status: '待出餐',
    table_number: '8',
    notes: '',
    priority: 'normal',
    placement: {
      steamer_id: '1',
      port_index: 1,
      stack_order: 1,
      loaded_at: '2026-08-14T10:00:00+08:00'
    },
    ...overrides,
    placement: {
      steamer_id: '1',
      port_index: 1,
      stack_order: 1,
      loaded_at: '2026-08-14T10:00:00+08:00',
      ...(overrides.placement || {})
    }
  }
}

describe('sortHoleDisplay', () => {
  const now = Date.parse('2026-08-14T10:12:00+08:00')

  it('sinks 退菜占位 after live cages', () => {
    const hold = steamingCage({
      id: 'h1',
      dish_status: '已取消',
      status: '退菜',
      placement: { stack_order: 1, loaded_at: '2026-08-14T09:00:00+08:00' }
    })
    const live = steamingCage({
      id: 's1',
      placement: { stack_order: 2, loaded_at: '2026-08-14T10:10:00+08:00' }
    })

    expect(sortHoleDisplay([hold, live], now).map((cage) => cage.id)).toEqual(['s1', 'h1'])
  })

  it('puts 催 cages before other live cages', () => {
    const quiet = steamingCage({
      id: 'q1',
      placement: { stack_order: 1, loaded_at: '2026-08-14T09:00:00+08:00' }
    })
    const byPriority = steamingCage({
      id: 'u1',
      priority: 'urgent',
      placement: { stack_order: 2, loaded_at: '2026-08-14T10:10:00+08:00' }
    })
    const byNotes = steamingCage({
      id: 'n1',
      notes: '催菜',
      placement: { stack_order: 3, loaded_at: '2026-08-14T10:11:00+08:00' }
    })

    expect(sortHoleDisplay([quiet, byPriority, byNotes], now).map((cage) => cage.id)).toEqual([
      'u1',
      'n1',
      'q1'
    ])
  })

  it('orders live cages by longer 蒸制时长 from loaded_at, then lower stack_order', () => {
    const newerShort = steamingCage({
      id: 'new',
      order_time: '2026-08-14T08:00:00+08:00',
      placement: { stack_order: 1, loaded_at: '2026-08-14T10:10:00+08:00' }
    })
    const olderLong = steamingCage({
      id: 'old',
      order_time: '2026-08-14T10:11:00+08:00',
      placement: { stack_order: 2, loaded_at: '2026-08-14T10:00:00+08:00' }
    })
    const sameDurationLowerStack = steamingCage({
      id: 'low',
      placement: { stack_order: 3, loaded_at: '2026-08-14T10:05:00+08:00' }
    })
    const sameDurationHigherStack = steamingCage({
      id: 'high',
      placement: { stack_order: 8, loaded_at: '2026-08-14T10:05:00+08:00' }
    })

    expect(
      sortHoleDisplay(
        [newerShort, sameDurationHigherStack, sameDurationLowerStack, olderLong],
        now
      ).map((cage) => cage.id)
    ).toEqual(['old', 'low', 'high', 'new'])
  })

  it('does not rewrite placement.stack_order', () => {
    const first = steamingCage({
      id: 'a',
      placement: { stack_order: 7, loaded_at: '2026-08-14T10:00:00+08:00' }
    })
    const second = steamingCage({
      id: 'b',
      placement: { stack_order: 2, loaded_at: '2026-08-14T10:08:00+08:00' }
    })

    sortHoleDisplay([first, second], now)
    expect(first.placement.stack_order).toBe(7)
    expect(second.placement.stack_order).toBe(2)
  })
})

describe('fillHoleSlots', () => {
  const now = Date.parse('2026-08-14T10:12:00+08:00')

  it('fills a hole to portCapacity slots and leaves unused slots empty', () => {
    const onHole = steamingCage({
      id: 's1',
      placement: { steamer_id: '1', port_index: 3, stack_order: 1 }
    })
    const otherHole = steamingCage({
      id: 's2',
      placement: { steamer_id: '1', port_index: 4, stack_order: 1 }
    })

    const slots = fillHoleSlots([onHole, otherHole], {
      steamerId: '1',
      portIndex: 3,
      portCapacity: 10,
      now
    })

    expect(slots).toHaveLength(10)
    expect(slots.filter((slot) => slot.empty)).toHaveLength(9)
    expect(slots[0].empty).toBe(false)
    expect(slots[0].cage.id).toBe('s1')
  })

  it('occupies slots in display sort and keeps empty slots unselectable', () => {
    const hold = steamingCage({
      id: 'h1',
      dish_status: '已取消',
      status: '退菜',
      placement: {
        steamer_id: '2',
        port_index: 1,
        stack_order: 1,
        loaded_at: '2026-08-14T09:00:00+08:00'
      }
    })
    const rushed = steamingCage({
      id: 'u1',
      priority: 'urgent',
      placement: {
        steamer_id: '2',
        port_index: 1,
        stack_order: 2,
        loaded_at: '2026-08-14T10:10:00+08:00'
      }
    })
    const quiet = steamingCage({
      id: 'q1',
      placement: {
        steamer_id: '2',
        port_index: 1,
        stack_order: 3,
        loaded_at: '2026-08-14T10:00:00+08:00'
      }
    })

    const slots = fillHoleSlots([hold, quiet, rushed], {
      steamerId: '2',
      portIndex: 1,
      now
    })

    expect(slots.map((slot) => (slot.empty ? null : slot.cage.id))).toEqual([
      'u1',
      'q1',
      'h1',
      null,
      null,
      null,
      null,
      null,
      null,
      null
    ])
    const empty = slots[3]
    expect(empty.empty).toBe(true)
    expect(empty.cage).toBeUndefined()
    expect(empty.id).toBeUndefined()
    expect(empty.orderId).toBeUndefined()
  })
})

describe('formatSteamerTableLabel', () => {
  it('keeps dine-in numbers compact on one line', () => {
    expect(formatSteamerTableLabel('8')).toEqual({ lines: ['8桌'] })
    expect(formatSteamerTableLabel('12')).toEqual({ lines: ['12桌'] })
  })

  it('splits delivery into a short prefix and number so 外·美团7 does not stay one string', () => {
    expect(formatSteamerTableLabel('美团7', 'delivery')).toEqual({ lines: ['外·美团', '7'] })
    expect(formatSteamerTableLabel('美团1', 'delivery')).toEqual({ lines: ['外·美团', '1'] })
    expect(formatSteamerTableLabel('饿了么3', 'delivery')).toEqual({ lines: ['外·饿了么', '3'] })
    expect(formatSteamerTableLabel('外·美团7')).toEqual({ lines: ['外·美团', '7'] })
    expect(formatSteamerTableLabel('淘宝闪购18', 'delivery')).toEqual({ lines: ['外·淘宝', '18'] })
  })

  it('uses a short 包 prefix when the table looks like 包间', () => {
    expect(formatSteamerTableLabel('包间2')).toEqual({ lines: ['包', '2'] })
    expect(formatSteamerTableLabel('包3')).toEqual({ lines: ['包', '3'] })
    expect(formatSteamerTableLabel('包厢5')).toEqual({ lines: ['包', '5'] })
  })
})

describe('formatSteamerCageCard', () => {
  const now = Date.parse('2026-08-14T10:12:00+08:00')

  it('puts dish name first and table plus steam minutes second', () => {
    const card = formatSteamerCageCard(
      steamingCage({
        dish_name: '虾饺',
        table_number: '8',
        placement: { loaded_at: '2026-08-14T10:00:00+08:00' }
      }),
      now
    )
    expect(card.primary).toBe('虾饺')
    expect(card.secondary).toBe('8桌 12分')
    expect(card.steamMinutes).toBe(12)
    expect(card.totalMinutes).toBe(0)
    expect(card.timeLabel).toBe('12分')
    expect(card.rushMark).toBe('')
    expect(card.holdMark).toBe('')
  })

  it('appends total wait from order_time after steam minutes', () => {
    const card = formatSteamerCageCard(
      steamingCage({
        dish_name: '虾饺',
        table_number: '8',
        order_time: '2026-08-14T09:00:00+08:00',
        placement: { loaded_at: '2026-08-14T10:00:00+08:00' }
      }),
      now
    )
    expect(card.steamMinutes).toBe(12)
    expect(card.totalMinutes).toBe(72)
    expect(card.timeLabel).toBe('12分 总72分')
  })

  it('marks 催 on the card without replacing the dish name', () => {
    const card = formatSteamerCageCard(
      steamingCage({
        dish_name: '虾饺',
        table_number: '8',
        notes: '催菜',
        placement: { loaded_at: '2026-08-14T10:00:00+08:00' }
      }),
      now
    )
    expect(card.primary).toBe('虾饺')
    expect(card.rushMark).toBe('催')
  })

  it('keeps 「退」 as a mark and does not replace the dish name', () => {
    const card = formatSteamerCageCard(
      steamingCage({
        dish_name: '凤爪',
        dish_status: '已取消',
        status: '退菜',
        table_number: '3',
        placement: { loaded_at: '2026-08-14T10:00:00+08:00' }
      }),
      now
    )
    expect(card.primary).toBe('凤爪')
    expect(card.holdMark).toBe('退')
    expect(card.primary).not.toBe('退')
  })

  it('keeps delivery table parts split on the second line', () => {
    const card = formatSteamerCageCard(
      steamingCage({
        dish_name: '凤爪',
        table_number: '美团7',
        source: 'delivery',
        placement: { loaded_at: '2026-08-14T10:03:00+08:00' }
      }),
      now
    )
    expect(card.primary).toBe('凤爪')
    expect(card.tableLines).toEqual(['外·美团', '7'])
    expect(card.secondary).toBe('外·美团 7 9分')
    expect(card.secondary).not.toContain('外·美团7')
  })

  it('drops a leading 外卖 prefix so the dish name fits the hole', () => {
    const card = formatSteamerCageCard(
      steamingCage({
        dish_name: '(外卖)金牌禄运虾饺皇',
        table_number: '淘宝闪购18',
        source: 'delivery',
        placement: { loaded_at: '2026-08-14T10:03:00+08:00' }
      }),
      now
    )
    expect(card.primary).toBe('金牌禄运虾饺皇')
    expect(card.tableLines).toEqual(['外·淘宝', '18'])
  })
})

describe('steamUrgencyLevel', () => {
  const now = Date.parse('2026-08-14T10:20:00+08:00')
  const thresholds = {
    warning: 15 * 60 * 1000,
    urgent: 20 * 60 * 1000
  }

  it('uses placement.loaded_at, not order_time', () => {
    const cage = steamingCage({
      order_time: '2026-08-14T09:00:00+08:00',
      placement: { loaded_at: '2026-08-14T10:10:00+08:00' }
    })
    expect(steamUrgencyLevel(cage, now, thresholds)).toBe('normal')
  })

  it('returns warn then urgent as steam time crosses the steam pair', () => {
    const warnCage = steamingCage({
      placement: { loaded_at: '2026-08-14T10:04:00+08:00' }
    })
    const urgentCage = steamingCage({
      placement: { loaded_at: '2026-08-14T09:50:00+08:00' }
    })
    expect(steamUrgencyLevel(warnCage, now, thresholds)).toBe('warn')
    expect(steamUrgencyLevel(urgentCage, now, thresholds)).toBe('urgent')
  })

  it('does not mark 退菜占位 or 待上笼 as steam-urgent', () => {
    const hold = steamingCage({
      dish_status: '已取消',
      status: '退菜',
      placement: { loaded_at: '2026-08-14T09:00:00+08:00' }
    })
    const awaiting = {
      dish_status: '待出餐',
      placement: null,
      order_time: '2026-08-14T09:00:00+08:00'
    }
    expect(steamUrgencyLevel(hold, now, thresholds)).toBe('normal')
    expect(steamUrgencyLevel(awaiting, now, thresholds)).toBe('normal')
  })
})

describe('steamerLayoutFromStations', () => {
  const shopLayout = {
    steamers: [
      { id: '1', port_count: 6 },
      { id: '2', port_count: 6 }
    ],
    port_capacity: 10,
    awaiting_cancel_notice_seconds: 180
  }

  const stationsPayload = [
    { id: 'changfen', name: '肠粉档' },
    { id: 'shulong', name: '熟笼档', steamer_layout: shopLayout }
  ]

  it('reads shulong steamer_layout from /api/stations payload', () => {
    expect(steamerLayoutFromStations(stationsPayload)).toEqual({
      steamers: [
        { id: '1', portCount: 6 },
        { id: '2', portCount: 6 }
      ],
      portCapacity: 10,
      awaitingCancelNoticeSeconds: 180
    })
  })

  it('reads camelCase steamerLayout the same way as snake_case', () => {
    expect(
      steamerLayoutFromStations([
        {
          id: 'shulong',
          steamerLayout: {
            steamers: [{ id: 'A', portCount: 6 }],
            portCapacity: 8,
            awaitingCancelNoticeSeconds: 90
          }
        }
      ])
    ).toEqual({
      steamers: [{ id: 'A', portCount: 6 }],
      portCapacity: 8,
      awaitingCancelNoticeSeconds: 90
    })
  })

  it('returns the same parsed layout for two identical payloads', () => {
    const first = steamerLayoutFromStations(stationsPayload)
    const second = steamerLayoutFromStations(stationsPayload)
    expect(first).toEqual(second)
    expect(first).toEqual(steamerLayoutFromStations({
      shulong: { id: 'shulong', steamer_layout: shopLayout }
    }))
  })

  it('falls back to SHULONG_STEAMER_LAYOUT when layout is missing', () => {
    expect(steamerLayoutFromStations(null)).toBe(SHULONG_STEAMER_LAYOUT)
    expect(steamerLayoutFromStations([])).toBe(SHULONG_STEAMER_LAYOUT)
    expect(steamerLayoutFromStations([{ id: 'changfen' }])).toBe(SHULONG_STEAMER_LAYOUT)
    expect(steamerLayoutFromStations([{ id: 'shulong', name: '熟笼档' }])).toBe(
      SHULONG_STEAMER_LAYOUT
    )
  })
})

describe('steamerAwaitingPlacement', () => {
  it('puts 待上笼 on the left side panel, never a covering drawer', () => {
    expect(steamerAwaitingPlacement()).toBe('side')
    expect(steamerAwaitingPlacement()).not.toBe('drawer')
  })
})

const memory = new Map()

vi.stubGlobal('uni', {
  getStorageSync(key) {
    return memory.has(key) ? memory.get(key) : ''
  },
  setStorageSync(key, value) {
    memory.set(key, value)
  },
  removeStorageSync(key) {
    memory.delete(key)
  }
})

const { ScreenSettingsManager } = await import('../storage.js')

describe('ScreenSettingsManager steamer thresholds', () => {
  beforeEach(() => {
    memory.clear()
  })

  it('defaults steamWarnMin / steamUrgentMin to 15/20 without overwriting wait mins', () => {
    const alert = ScreenSettingsManager.getAlertParams()
    expect(alert.steamWarnMin).toBe(15)
    expect(alert.steamUrgentMin).toBe(20)
    expect(alert.warnMin).toBe(15)
    expect(alert.urgentMin).toBe(20)
  })

  it('persists the steam pair independently of wait warnMin / urgentMin', () => {
    expect(ScreenSettingsManager.setAlertParams({ warnMin: 9, urgentMin: 11 })).toBe(true)
    expect(ScreenSettingsManager.setAlertParams({ steamWarnMin: 8, steamUrgentMin: 12 })).toBe(true)
    const alert = ScreenSettingsManager.getAlertParams()
    expect(alert.steamWarnMin).toBe(8)
    expect(alert.steamUrgentMin).toBe(12)
    expect(alert.warnMin).toBe(9)
    expect(alert.urgentMin).toBe(11)
  })
})
