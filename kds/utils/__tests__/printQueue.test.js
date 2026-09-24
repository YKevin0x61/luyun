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
  it('keeps an interrupted (processing) job as failed instead of reprinting it on import', async () => {
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

    // 磁盘上的 processing 意味着上次进程在打印中途退出：这张小票可能已经出纸了，
    // 自动重打就是重复出纸 → 转失败任务等人工确认
    expect(mocks.printDishTicket).not.toHaveBeenCalled()

    // 修正后的队列立刻写回磁盘，避免下次启动又把这条 processing 读进来
    expect(persistedSnapshots[0]).toEqual([
      expect.objectContaining({
        id: 'print_restored_1',
        status: 'failed',
        nextAttemptAt: null,
        lastError: '上次打印中断（应用被关闭或崩溃），请确认这张小票是否已经打出',
      }),
    ])

    const state = queue.getQueueState()
    expect(state.pendingCount).toBe(0)
    expect(state.failedCount).toBe(1)
    expect(state.failedJobs[0]).toMatchObject({
      id: 'print_restored_1',
      status: 'failed',
      ticket: { dishName: '恢复菜品' },
    })
  })

  it('persists the job as processing before handing it to the printer', async () => {
    const persistedSnapshots = []
    const statusWhenPrinting = []
    mocks.saveQueue.mockImplementation((jobs) => {
      persistedSnapshots.push(JSON.parse(JSON.stringify(jobs)))
    })
    mocks.printDishTicket.mockImplementation(() => {
      // 打印器被调用的那一刻，磁盘上必须已经写着 processing
      statusWhenPrinting.push((persistedSnapshots.at(-1) || []).map((job) => job.status))
      return Promise.resolve(OK_RESULT)
    })

    const queue = await loadPrintQueue()
    queue.enqueuePrintTicket(makeTicket('肠粉'))
    await settle()

    expect(statusWhenPrinting).toEqual([['processing']])
    // attempts 也已经计入落盘，崩溃重启后不会从 0 重新数
    expect(persistedSnapshots[1]).toEqual([
      expect.objectContaining({ status: 'processing', attempts: 1 }),
    ])
    expect(queue.getQueueState()).toEqual(EMPTY_STATE)
  })

  it('normalizes damaged stored records instead of leaving NaN zombies', async () => {
    mocks.printDishTicket.mockResolvedValue(OK_RESULT)
    const stored = [
      // 旧版本落盘：没有 attempts / nextAttemptAt
      { id: 'print_legacy', ticket: makeTicket('老数据'), status: 'pending' },
      // 字段类型损坏：attempts / nextAttemptAt 都不是数字
      {
        id: 'print_bad_number',
        ticket: makeTicket('坏数值'),
        status: 'pending',
        attempts: '3',
        nextAttemptAt: 'later',
      },
      // 不认识的 status
      { id: 'print_status', ticket: makeTicket('坏状态'), status: 'printing', attempts: 1 },
      // 没有 ticket
      { id: 'print_noticket', status: 'pending', attempts: 0, nextAttemptAt: null },
      // 没有 id
      { ticket: makeTicket('无 id'), status: 'pending', attempts: 0, nextAttemptAt: null },
      // 连对象都不是
      'garbage',
      null,
    ]

    const queue = await loadPrintQueue(stored)
    await settle()

    // 能补齐的两条照常补打（NaN attempts 会无限重试，NaN nextAttemptAt 会永久 pending）
    expect(mocks.printDishTicket.mock.calls.map(([ticket]) => ticket.dishName)).toEqual([
      '老数据',
      '坏数值',
    ])

    // 其余三条降级成失败任务进补打列表：既不自动打印，也不会静默消失
    const state = queue.getQueueState()
    expect(state.pendingCount).toBe(0)
    expect(state.failedCount).toBe(3)
    expect(state.failedJobs.map((job) => job.id)).toEqual(
      expect.arrayContaining(['print_status', 'print_noticket'])
    )
    expect(state.failedJobs.map((job) => job.ticket.dishName)).toEqual(
      expect.arrayContaining(['坏状态', '（任务记录已损坏）', '无 id'])
    )
    for (const job of state.failedJobs) {
      expect(Number.isFinite(job.attempts)).toBe(true)
      expect(job.nextAttemptAt).toBeNull()
      expect(job.lastError).toBe('本地缓存的打印任务已损坏，未自动补打，请确认是否漏打')
    }

    // 连对象都不是的两条只能丢弃，但必须留下痕迹
    expect(consoleWarnSpy).toHaveBeenCalledWith(expect.stringContaining('2 条记录无法解析'))
  })

  it('survives the retry storm of a job whose ticket is missing', async () => {
    mocks.printDishTicket.mockRejectedValue(new Error('蓝牙写入超时'))

    const queue = await loadPrintQueue()
    const jobId = queue.enqueuePrintTicket(undefined)
    await settle()

    await advance(2000) // 第 2 次尝试
    await advance(4000) // 第 3 次尝试

    expect(mocks.printDishTicket).toHaveBeenCalledTimes(3)

    // ticket 缺失时失败日志不能再解引用 job.ticket：改前这里会二次抛错，
    // 任务永远转不成 failed（还变成 unhandled rejection）
    const state = queue.getQueueState()
    expect(state.pendingCount).toBe(0)
    expect(state.failedCount).toBe(1)
    expect(state.failedJobs[0]).toMatchObject({
      id: jobId,
      status: 'failed',
      lastError: '蓝牙写入超时',
    })
    expect(consoleErrorSpy.mock.calls.map((args) => String(args[0])).join('\n')).toContain(jobId)
  })

  it('fails a stored record with missing/damaged attempts once it reaches the retry limit', async () => {
    mocks.printDishTicket.mockRejectedValue(new Error('蓝牙写入超时'))
    const stored = [
      // 旧版本落盘：没有 attempts。归一化若被改回 raw.attempts，这里是 NaN，
      // `NaN >= 上限` 恒 false —— 任务会永久 pending 而不是转 failed
      { id: 'print_legacy_fail', ticket: makeTicket('老数据'), status: 'pending' },
      // 字段类型损坏：attempts 是字符串。不归一化时 `'3' + 1 === '31'`（字符串拼接），
      // 第一次就"到上限"，第二轮起也不会再重试
      {
        id: 'print_bad_attempts',
        ticket: makeTicket('坏尝试数'),
        status: 'pending',
        attempts: '3',
        nextAttemptAt: 'later',
      },
    ]

    const queue = await loadPrintQueue(stored)
    await settle()
    const { PRINT_JOB_MAX_ATTEMPTS } = queue.PRINT_QUEUE_CONSTANTS

    // 两条记录都持续失败：每条各跑满上限（首次 + 2000ms/4000ms 退避）后必须转 failed
    await advance(2000) // 老数据 第 2 次
    await advance(4000) // 老数据 第 3 次 → 到上限；循环接着处理坏尝试数 第 1 次
    await advance(2000) // 坏尝试数 第 2 次
    await advance(4000) // 坏尝试数 第 3 次 → 到上限
    await advance(12000) // 兜底：真变成僵尸任务的话，这里会继续重试/继续 pending

    const state = queue.getQueueState()
    // 断言落在 attempts 上：NaN（缺字段）、'31'（字符串拼接）都会让这里红
    const attemptsById = Object.fromEntries(state.failedJobs.map((job) => [job.id, job.attempts]))
    expect(attemptsById).toEqual({
      print_legacy_fail: PRINT_JOB_MAX_ATTEMPTS,
      print_bad_attempts: PRINT_JOB_MAX_ATTEMPTS,
    })
    expect(state.failedCount).toBe(2)
    for (const job of state.failedJobs) {
      expect(Number.isFinite(job.attempts)).toBe(true)
      expect(job.status).toBe('failed')
      expect(job.nextAttemptAt).toBeNull()
      expect(job.lastError).toBe('蓝牙写入超时')
    }

    // 到上限即停：两条加起来正好 2 × 上限 次，pending 清零（僵尸会留下一条 pending）
    expect(mocks.printDishTicket).toHaveBeenCalledTimes(2 * PRINT_JOB_MAX_ATTEMPTS)
    const printedDishNames = mocks.printDishTicket.mock.calls.map(([ticket]) => ticket.dishName)
    expect(printedDishNames.filter((name) => name === '老数据')).toHaveLength(PRINT_JOB_MAX_ATTEMPTS)
    expect(printedDishNames.filter((name) => name === '坏尝试数')).toHaveLength(
      PRINT_JOB_MAX_ATTEMPTS
    )
    expect(state.pendingCount).toBe(0)
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

describe('打印链路导入（vitest 的 @ 别名）', () => {
  it('imports the real bluetooth printer chain through the @ alias', async () => {
    // bluetoothPrinter.js 在 APP-PLUS 条件编译块里 import '@/uni_modules/kds-bluetooth-printer'，
    // 而 vitest 不做条件编译，这行在测试里是活代码：@ 别名缺失时这里会直接 "Failed to resolve import"。
    const printer = await vi.importActual('../bluetoothPrinter.js')

    expect(typeof printer.printText).toBe('function')
    expect(printer.PrintAlign.CENTER).toBe(1)
    expect(printer.FontSize.NORMAL).toBe(0)
    // 源码里的 APP-PLUS 分支还在（H5 构建才会被剥掉），所以平台判断在 vitest 下是 true ——
    // 这正是打印链路在测试环境也必须能解析 UTS 插件导入的原因
    expect(printer.isPrinterPlatformSupported()).toBe(true)

    // 别名指向最小 stub：只有真的调用插件能力时才抛"测试环境没有实现"
    const plugin = await import('@/uni_modules/kds-bluetooth-printer')
    expect(typeof plugin.printText).toBe('function')
    expect(() => plugin.printText('测试')).toThrow(/APP-PLUS/)
  })
})
