/**
 * 出餐小票打印队列
 *
 * 背景：`printDishTicket` 直连蓝牙打印机，出餐操作若直接 await/并发调用打印，
 * 会阻塞出餐主流程且可能造成蓝牙连接并发冲突；打印失败此前也只是 toast 一下，
 * 无重试、无补打、无持久化。
 *
 * 本模块把"打印一张小票"封装为队列任务：
 * - 串行处理（同一时刻最多一个任务在打印，避免蓝牙连接并发冲突）
 * - 失败自动重试（有限次数，指数退避），仍失败则转入"失败任务"保留
 * - 队列状态（待处理数/失败数/失败任务详情）通过订阅回调对外广播，供页面展示提示与角标
 * - 队列内容持久化到 storage，App 进程重启后可尽力恢复（保留失败任务，待处理任务恢复排队）
 *
 * 平台 skip（H5/非 APP-PLUS）与打印开关 `isPrintEnabled` 判断保持在 `printDishTicket` 内部，
 * 本模块不重复判断，只是把"跳过"视为任务成功完成（无需重试、不计入失败）。
 */

import { printDishTicket } from './dishTicketPrinter.js'
import { PrintQueueManager } from './storage.js'
import { debugLog } from './debug.js'

// 最多尝试次数（含首次尝试）：超过后不再自动重试，转入失败任务列表等待手动补打
const PRINT_JOB_MAX_ATTEMPTS = 3
// 重试退避基准间隔与倍数：第 N 次重试前等待 BASE_DELAY_MS * MULTIPLIER^(N-1)
const PRINT_RETRY_BASE_DELAY_MS = 2000
const PRINT_RETRY_BACKOFF_MULTIPLIER = 2
// 失败任务最多保留条数，避免长期不补打导致本地存储无限增长（超出后丢弃最旧的失败任务）
const MAX_FAILED_JOBS_KEPT = 50

/** @type {Array<Object>} 内存中的队列，元素形如 { id, ticket, status, attempts, nextAttemptAt, lastError, createdAt, lastAttemptAt } */
let queue = []
let isProcessing = false
let pendingTimer = null
let pendingWakeResolve = null

/** @type {Set<Function>} 队列状态订阅者 */
const subscribers = new Set()

function generateJobId() {
  return `print_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function getRetryDelayMs(attemptsSoFar) {
  // attemptsSoFar 为已尝试次数（第 1 次失败后 attemptsSoFar=1，即将进行第 2 次尝试）
  const retryIndex = attemptsSoFar - 1
  return PRINT_RETRY_BASE_DELAY_MS * Math.pow(PRINT_RETRY_BACKOFF_MULTIPLIER, retryIndex)
}

function pruneFailedJobs() {
  const failedJobs = queue.filter((job) => job.status === 'failed')
  const excess = failedJobs.length - MAX_FAILED_JOBS_KEPT
  if (excess <= 0) return
  // 按创建时间升序丢弃最旧的若干条失败任务
  const toDrop = new Set(
    failedJobs
      .slice()
      .sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime())
      .slice(0, excess)
      .map((job) => job.id)
  )
  queue = queue.filter((job) => !toDrop.has(job.id))
}

function persistQueue() {
  PrintQueueManager.saveQueue(queue)
}

function getQueueSnapshot() {
  const failedJobs = queue.filter((job) => job.status === 'failed')
  const pendingCount = queue.length - failedJobs.length
  return {
    pendingCount,
    failedCount: failedJobs.length,
    failedJobs: failedJobs.map((job) => ({ ...job }))
  }
}

function notifySubscribers() {
  const snapshot = getQueueSnapshot()
  subscribers.forEach((callback) => {
    try {
      callback(snapshot)
    } catch (error) {
      console.error('[打印队列] 订阅回调执行失败:', error)
    }
  })
}

function removeJob(jobId) {
  queue = queue.filter((job) => job.id !== jobId)
  persistQueue()
  notifySubscribers()
}

function findNextReadyJob() {
  const now = Date.now()
  return queue.find((job) => job.status === 'pending' && job.nextAttemptAt <= now)
}

function findNextWaitMs() {
  const now = Date.now()
  const waitingJobs = queue.filter((job) => job.status === 'pending' && job.nextAttemptAt > now)
  if (waitingJobs.length === 0) return null
  const earliest = Math.min(...waitingJobs.map((job) => job.nextAttemptAt))
  return Math.max(0, earliest - now)
}

function sleep(ms) {
  return new Promise((resolve) => {
    pendingWakeResolve = resolve
    pendingTimer = setTimeout(() => {
      pendingTimer = null
      pendingWakeResolve = null
      resolve()
    }, ms)
  })
}

/** 唤醒正在等待退避间隔的处理循环，让其立即重新检查队列（新任务入队/手动补打时调用） */
function wakePendingLoop() {
  if (pendingTimer) {
    clearTimeout(pendingTimer)
    pendingTimer = null
  }
  if (pendingWakeResolve) {
    const resolve = pendingWakeResolve
    pendingWakeResolve = null
    resolve()
  }
}

async function attemptJob(job) {
  job.status = 'processing'
  job.attempts += 1
  job.lastAttemptAt = new Date().toISOString()
  notifySubscribers()

  try {
    const result = await printDishTicket(job.ticket)

    if (result && result.skipped) {
      // H5 或未启用打印：属于预期内的"无需打印"，不计入失败
      debugLog('[打印队列] 跳过打印:', job.ticket.dishName, result.message)
      removeJob(job.id)
      return
    }

    if (!result || !result.success) {
      throw new Error(result?.message || '打印未成功')
    }

    debugLog('[打印队列] 打印成功:', job.ticket.dishName, job.ticket.tableNumber)
    removeJob(job.id)
  } catch (error) {
    const message = error?.message || String(error) || '打印失败'
    job.lastError = message

    if (job.attempts >= PRINT_JOB_MAX_ATTEMPTS) {
      job.status = 'failed'
      job.nextAttemptAt = null
      console.error(
        `[打印队列] "${job.ticket.dishName}"(${job.ticket.tableNumber}) 打印失败，已达最大尝试次数(${PRINT_JOB_MAX_ATTEMPTS})，转入失败任务待手动补打:`,
        message
      )
      pruneFailedJobs()
    } else {
      const delay = getRetryDelayMs(job.attempts)
      job.status = 'pending'
      job.nextAttemptAt = Date.now() + delay
      console.warn(
        `[打印队列] "${job.ticket.dishName}"(${job.ticket.tableNumber}) 第 ${job.attempts} 次打印失败，${delay}ms 后自动重试:`,
        message
      )
    }

    persistQueue()
    notifySubscribers()
  }
}

async function runProcessingLoop() {
  if (isProcessing) return
  isProcessing = true

  try {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const job = findNextReadyJob()
      if (job) {
        await attemptJob(job)
        continue
      }

      const waitMs = findNextWaitMs()
      if (waitMs === null) {
        break
      }
      await sleep(waitMs)
    }
  } finally {
    isProcessing = false
  }
}

/**
 * 将一份出餐小票加入打印队列，立即返回（不等待打印完成），不阻塞出餐主流程。
 * @param {Object} ticket 见 dishTicketPrinter.printDishTicket 的参数说明
 * @returns {string} 任务 id
 */
export function enqueuePrintTicket(ticket) {
  const job = {
    id: generateJobId(),
    ticket,
    status: 'pending',
    attempts: 0,
    nextAttemptAt: Date.now(),
    lastError: null,
    createdAt: new Date().toISOString(),
    lastAttemptAt: null
  }

  queue.push(job)
  persistQueue()
  notifySubscribers()
  wakePendingLoop()
  runProcessingLoop()

  return job.id
}

/**
 * 订阅队列状态变化（待处理数/失败数/失败任务详情）。
 * 订阅后立即收到一次当前快照。
 * @param {(snapshot: { pendingCount: number, failedCount: number, failedJobs: Array }) => void} callback
 * @returns {() => void} 取消订阅函数
 */
export function subscribeQueueState(callback) {
  subscribers.add(callback)
  callback(getQueueSnapshot())
  return () => subscribers.delete(callback)
}

export function getQueueState() {
  return getQueueSnapshot()
}

/**
 * 手动补打单个失败任务：重置为待处理并立即重新排队
 * @param {string} jobId
 * @returns {boolean} 是否找到并重新入队
 */
export function retryFailedJob(jobId) {
  const job = queue.find((item) => item.id === jobId && item.status === 'failed')
  if (!job) return false

  job.status = 'pending'
  job.attempts = 0
  job.nextAttemptAt = Date.now()
  job.lastError = null

  persistQueue()
  notifySubscribers()
  wakePendingLoop()
  runProcessingLoop()
  return true
}

/**
 * 手动补打所有失败任务
 * @returns {number} 重新入队的任务数
 */
export function retryAllFailedJobs() {
  const failedJobs = queue.filter((job) => job.status === 'failed')
  if (failedJobs.length === 0) return 0

  failedJobs.forEach((job) => {
    job.status = 'pending'
    job.attempts = 0
    job.nextAttemptAt = Date.now()
    job.lastError = null
  })

  persistQueue()
  notifySubscribers()
  wakePendingLoop()
  runProcessingLoop()
  return failedJobs.length
}

/**
 * 从持久化存储恢复队列（App 启动时调用一次）。
 * 恢复时若任务处于 'processing'（上次进程异常终止导致的中间态），重置为 'pending' 并立即可重试；
 * 'failed' 任务保持失败状态，等待用户手动补打；'pending' 任务恢复排队继续自动处理。
 */
function restoreQueueFromStorage() {
  const stored = PrintQueueManager.getQueue()
  if (!Array.isArray(stored) || stored.length === 0) return

  const now = Date.now()
  queue = stored.map((job) => {
    if (job.status === 'processing') {
      return { ...job, status: 'pending', nextAttemptAt: now }
    }
    return job
  })

  pruneFailedJobs()
  persistQueue()

  const hasPending = queue.some((job) => job.status === 'pending')
  if (hasPending) {
    runProcessingLoop()
  }
}

restoreQueueFromStorage()

export const PRINT_QUEUE_CONSTANTS = {
  PRINT_JOB_MAX_ATTEMPTS,
  PRINT_RETRY_BASE_DELAY_MS,
  PRINT_RETRY_BACKOFF_MULTIPLIER,
  MAX_FAILED_JOBS_KEPT
}
