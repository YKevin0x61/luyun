import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * 出餐小票打印队列（utils/printQueue.js）回归用例。
 *
 * 模块在 import 时就会跑一次 restoreQueueFromStorage()，队列与处理循环状态又都是模块级变量，
 * 所以每个用例都先 vi.resetModules() 再重新 import 一份干净模块。
 * 外部接缝只有两个：打印器 dishTicketPrinter.printDishTicket 与持久化
 * storage.PrintQueueManager，都用 vi.mock 换掉；
 * 串行、退避重试、失败保留 + 补打、重启恢复这些被测逻辑全部走真实代码。
 */

const mocks = vi.hoisted(() => ({
  printDishTicket: vi.fn(),
  getQueue: vi.fn(),
  saveQueue: vi.fn(),
}))

vi.mock('../dishTicketPrinter.js', () => ({
  printDishTicket: mocks.printDishTicket,
}))

vi.mock('../storage.js', () => ({
  PrintQueueManager: {
    getQueue: mocks.getQueue,
    saveQueue: mocks.saveQueue,
    clearQueue: vi.fn(),
  },
}))

const OK_RESULT = { success: true, skipped: false, message: '打印成功' }

function makeTicket(dishName) {
  return {
    tableNumber: '8',
    dishName,
    orderTime: '2026-09-22T10:00:00+08:00',
    readyTime: '2026-09-22T10:05:00+08:00',
    notes: '',
  }
}

/** 重新 import 一份干净模块：模块级队列与 import 时的恢复逻辑都随之重置 */
async function loadPrintQueue(storedQueue = []) {
  mocks.getQueue.mockReturnValue(storedQueue)
  vi.resetModules()
  return import('../printQueue.js')
}

/** fake timers 不接管 Promise，微任务得手动放行 */
async function settle(rounds = 25) {
  for (let i = 0; i < rounds; i += 1) {
    await Promise.resolve()
  }
}

/** 推进假时钟（含期间到点的退避重试）并放行微任务 */
async function advance(ms) {
  await vi.advanceTimersByTimeAsync(ms)
  await settle()
}

const EMPTY_STATE = { pendingCount: 0, failedCount: 0, failedJobs: [] }

let consoleErrorSpy
let consoleWarnSpy

beforeEach(() => {
  vi.useFakeTimers()
  mocks.printDishTicket.mockReset()
  mocks.getQueue.mockReset()
  mocks.saveQueue.mockReset()
  // 失败路径的 console.warn/error 是预期内的，别把测试输出淹掉
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => {
  consoleErrorSpy.mockRestore()
  consoleWarnSpy.mockRestore()
  vi.useRealTimers()
  vi.resetModules()
})

describe('printQueue 串行处理', () => {
  it('runs queued tickets strictly one at a time (peak concurrency 1)', async () => {
    const started = []
    const releases = []
    let inFlight = 0
    let peakInFlight = 0

    mocks.printDishTicket.mockImplementation((ticket) => {
      started.push(ticket.dishName)
      inFlight += 1
      peakInFlight = Math.max(peakInFlight, inFlight)
      return new Promise((resolve) => {
        releases.push(() => {
          inFlight -= 1
          resolve(OK_RESULT)
        })
      })
    })

    const queue = await loadPrintQueue()

    queue.enqueuePrintTicket(makeTicket('A'))
    queue.enqueuePrintTicket(makeTicket('B'))
    queue.enqueuePrintTicket(makeTicket('C'))
    await settle()

    // 三张都在队列里，但只有第一张进了打印器
    expect(started).toEqual(['A'])
    expect(releases).toHaveLength(1)
    expect(peakInFlight).toBe(1)

    releases.shift()()
    await settle()
    expect(started).toEqual(['A', 'B'])
    expect(peakInFlight).toBe(1)

    releases.shift()()
    await settle()
    expect(started).toEqual(['A', 'B', 'C'])
    expect(peakInFlight).toBe(1)

    releases.shift()()
    await settle()

    expect(mocks.printDishTicket).toHaveBeenCalledTimes(3)
    expect(queue.getQueueState()).toEqual(EMPTY_STATE)
  })
})

describe('printQueue 退避重试', () => {
  it('retries after 2000ms then 4000ms and clears the queue on success', async () => {
    mocks.printDishTicket
      .mockRejectedValueOnce(new Error('蓝牙未连接'))
      .mockRejectedValueOnce(new Error('蓝牙未连接'))
      .mockResolvedValueOnce(OK_RESULT)

    const queue = await loadPrintQueue()
    queue.enqueuePrintTicket(makeTicket('肠粉'))
    await settle()

    // 第 1 次尝试：立即执行
    expect(mocks.printDishTicket).toHaveBeenCalledTimes(1)

    // 第 1 次失败 → 基准退避 2000ms，未到点不得重试
    await advance(1999)
    expect(mocks.printDishTicket).toHaveBeenCalledTimes(1)

    await advance(1)
    expect(mocks.printDishTicket).toHaveBeenCalledTimes(2)

    // 第 2 次失败 → 退避翻倍到 4000ms
    await advance(3999)
    expect(mocks.printDishTicket).toHaveBeenCalledTimes(2)

    await advance(1)
    expect(mocks.printDishTicket).toHaveBeenCalledTimes(3)

    // 第 3 次成功：出队，不存在失败任务
    expect(queue.getQueueState()).toEqual(EMPTY_STATE)
  })
})

describe('printQueue 失败保留与手动补打', () => {
  it('keeps the job with lastError after 3 failures and requeues it on retryAllFailedJobs', async () => {
    mocks.printDishTicket.mockRejectedValue(new Error('蓝牙写入超时'))

    const queue = await loadPrintQueue()
    queue.enqueuePrintTicket(makeTicket('凤爪'))
    await settle()

    await advance(2000) // 第 2 次尝试
    await advance(4000) // 第 3 次尝试
    expect(mocks.printDishTicket).toHaveBeenCalledTimes(3)

    const failedState = queue.getQueueState()
    expect(failedState.pendingCount).toBe(0)
    expect(failedState.failedCount).toBe(1)
    expect(failedState.failedJobs).toHaveLength(1)
    expect(failedState.failedJobs[0]).toMatchObject({
      status: 'failed',
      attempts: 3,
      lastError: '蓝牙写入超时',
      ticket: { dishName: '凤爪' },
    })

    // 打印机恢复后手动补打
    mocks.printDishTicket.mockReset()
    mocks.printDishTicket.mockResolvedValue(OK_RESULT)

    expect(queue.retryAllFailedJobs()).toBe(1)
    await settle()

    expect(mocks.printDishTicket).toHaveBeenCalledTimes(1)
    expect(mocks.printDishTicket).toHaveBeenCalledWith(expect.objectContaining({ dishName: '凤爪' }))
    expect(queue.getQueueState()).toEqual(EMPTY_STATE)

    // 没有失败任务时不再重复入队
    expect(queue.retryAllFailedJobs()).toBe(0)
  })
})

describe('printQueue 持久化恢复', () => {
  it('resets a stored processing job to pending and prints it on import', async () => {
    mocks.printDishTicket.mockResolvedValue(OK_RESULT)
    // 落盘参数是队列里活的任务对象引用，处理循环随后会把 status 改成 processing，
    // 所以要在调用当场留一份深拷贝快照
    const persistedSnapshots = []
    mocks.saveQueue.mockImplementation((jobs) => {
      persistedSnapshots.push(JSON.parse(JSON.stringify(jobs)))
    })
    const stored = [
      {
        id: 'print_restored_1',
        ticket: makeTicket('恢复菜品'),
        status: 'processing',
        attempts: 1,
        nextAttemptAt: null,
        lastError: null,
        createdAt: '2026-09-22T02:00:00.000Z',
        lastAttemptAt: '2026-09-22T02:00:01.000Z',
      },
    ]

    const queue = await loadPrintQueue(stored)
    await settle()

    // 恢复时必须先把 processing 重置成 pending 再落盘，否则下次崩溃会永远卡在中间态
    expect(persistedSnapshots[0]).toEqual([
      expect.objectContaining({
        id: 'print_restored_1',
        status: 'pending',
        nextAttemptAt: expect.any(Number),
      }),
    ])

    // 并且不需要任何外部触发就继续执行
    expect(mocks.printDishTicket).toHaveBeenCalledTimes(1)
    expect(mocks.printDishTicket).toHaveBeenCalledWith(expect.objectContaining({ dishName: '恢复菜品' }))
    expect(queue.getQueueState()).toEqual(EMPTY_STATE)
  })

  it('restores a stored failed job as failed without reprinting it', async () => {
    mocks.printDishTicket.mockResolvedValue(OK_RESULT)
    const stored = [
      {
        id: 'print_failed_1',
        ticket: makeTicket('补打菜品'),
        status: 'failed',
        attempts: 3,
        nextAttemptAt: null,
        lastError: '蓝牙写入超时',
        createdAt: '2026-09-22T02:00:00.000Z',
        lastAttemptAt: '2026-09-22T02:00:05.000Z',
      },
    ]

    const queue = await loadPrintQueue(stored)
    await settle()

    expect(mocks.printDishTicket).not.toHaveBeenCalled()

    const state = queue.getQueueState()
    expect(state.pendingCount).toBe(0)
    expect(state.failedCount).toBe(1)
    expect(state.failedJobs[0]).toMatchObject({
      id: 'print_failed_1',
      status: 'failed',
      lastError: '蓝牙写入超时',
    })
  })
})
