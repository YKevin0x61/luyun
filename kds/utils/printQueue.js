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

// 恢复时写进 lastError 的文案：这两类任务都不会被自动重打，只等人工确认
const RESTORE_BROKEN_JOB_ERROR = '本地缓存的打印任务已损坏，未自动补打，请确认是否漏打'
const RESTORE_INTERRUPTED_JOB_ERROR = '上次打印中断（应用被关闭或崩溃），请确认这张小票是否已经打出'
// 缓存里连 ticket 都没有时的占位：补打列表要能看见"有这么一条坏记录"
const BROKEN_TICKET_PLACEHOLDER = Object.freeze({ dishName: '（任务记录已损坏）', tableNumber: '' })

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
  // 先把"处理中"落盘，再真正调打印。这一步之后进程若被杀，磁盘上留下的是 processing
  // 而不是一条从没打印过的 pending —— 否则重启后会把同一张小票再打一遍
  // （`restoreQueueFromStorage` 见到 processing 会转成待人工确认的失败任务）。
  // 打印动作本身（打印 → 切纸）的顺序由 dishTicketPrinter 的票面模板 / 蓝牙指令序列决定，
  // 这里只是多写一次本地存储，不改动作次序，也不参与打印。仓库里没有约束打印顺序的 ADR。
  persistQueue()
  notifySubscribers()

  try {
    const result = await printDishTicket(job.ticket)

    if (result && result.skipped) {
      // H5 或未启用打印：属于预期内的"无需打印"，不计入失败
      debugLog('[打印队列] 跳过打印:', job.ticket?.dishName ?? job.id, result.message)
      removeJob(job.id)
      return
    }

    if (!result || !result.success) {
      throw new Error(result?.message || '打印未成功')
    }

    debugLog('[打印队列] 打印成功:', job.ticket?.dishName ?? job.id, job.ticket?.tableNumber)
    removeJob(job.id)
  } catch (error) {
    const message = error?.message || String(error) || '打印失败'
    job.lastError = message

    if (job.attempts >= PRINT_JOB_MAX_ATTEMPTS) {
      job.status = 'failed'
      job.nextAttemptAt = null
      // ticket 可能缺失（缓存损坏、调用方传空），日志里不能直接解引用 job.ticket，
      // 否则失败路径自己再抛一次异常，任务永远转不成 failed
      console.error(
        `[打印队列] "${job.ticket?.dishName ?? job.id}"(${job.ticket?.tableNumber ?? '-'}) 打印失败，已达最大尝试次数(${PRINT_JOB_MAX_ATTEMPTS})，转入失败任务待手动补打:`,
        message
      )
      pruneFailedJobs()
    } else {
      const delay = getRetryDelayMs(job.attempts)
      job.status = 'pending'
      job.nextAttemptAt = Date.now() + delay
      console.warn(
        `[打印队列] "${job.ticket?.dishName ?? job.id}"(${job.ticket?.tableNumber ?? '-'}) 第 ${job.attempts} 次打印失败，${delay}ms 后自动重试:`,
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

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/**
 * 把一条从 storage 读回来的记录归一化成队列内部约定形状的任务。
 *
 * 恢复是本地数据进入队列的**唯一**入口，而 storage 里的内容是上一次运行时
 * `JSON.stringify` 写下的：可能被截断、可能来自旧版本、也可能被外部改坏。不做校验直接
 * 放进队列，`attempts` 缺失会得到 `NaN`（`NaN >= PRINT_JOB_MAX_ATTEMPTS` 恒为 false，
 * 永远不会转 failed，无限重试），`nextAttemptAt` 缺失会得到 `undefined`
 * （`undefined <= now` 恒为 false，任务永久 pending 又不进补打列表）。这类"僵尸任务"
 * 既打不出来，用户也看不见。
 *
 * 归一化后的不变量：id 非空字符串、ticket 是对象、status ∈ pending/failed、
 * attempts 是 >= 0 的有限数、nextAttemptAt 是有限数或 null（failed）。
 *
 * @param {*} raw storage 里的一条记录
 * @param {number} now 本次恢复的时刻（显式传入，避免与真实时钟/fake timer 纠缠）
 * @returns {Object|null} 合法任务；null 表示这条记录连任务都算不上，只能丢弃
 */
function normalizeStoredJob(raw, now) {
  if (!isPlainObject(raw)) return null

  const hasId = typeof raw.id === 'string' && raw.id.trim() !== ''
  const ticket = isPlainObject(raw.ticket) ? raw.ticket : null
  const status =
    raw.status === 'pending' || raw.status === 'failed' || raw.status === 'processing'
      ? raw.status
      : null

  const job = {
    id: hasId ? raw.id : generateJobId(),
    ticket: ticket || BROKEN_TICKET_PLACEHOLDER,
    status: 'failed',
    attempts: Number.isFinite(raw.attempts) ? Math.max(0, Math.floor(raw.attempts)) : 0,
    nextAttemptAt: Number.isFinite(raw.nextAttemptAt) ? raw.nextAttemptAt : now,
    lastError: typeof raw.lastError === 'string' && raw.lastError ? raw.lastError : null,
    createdAt:
      typeof raw.createdAt === 'string' && raw.createdAt
        ? raw.createdAt
        : new Date(now).toISOString(),
    lastAttemptAt:
      typeof raw.lastAttemptAt === 'string' && raw.lastAttemptAt ? raw.lastAttemptAt : null
  }

  if (!hasId || !ticket || !status) {
    // 形状不可用：要么没 id/ticket（补打也打不出这张票），要么 status 不认识。
    // 一律降级为失败任务进补打列表，既不自动打印，也不会消失得无声无息。
    job.nextAttemptAt = null
    job.lastError = RESTORE_BROKEN_JOB_ERROR
    return job
  }

  if (status === 'processing') {
    // 进打印前已经落盘 processing（见 attemptJob），所以磁盘上的 processing 意味着上次
    // 进程在打印中途退出了：这张小票可能已经打出去、也可能只打了一半。自动重打会重复出纸，
    // 因此转成失败任务等人工确认，而不是悄悄再打一遍。
    job.nextAttemptAt = null
    job.lastError = RESTORE_INTERRUPTED_JOB_ERROR
    return job
  }

  job.status = status
  if (status === 'pending') {
    job.nextAttemptAt = Number.isFinite(raw.nextAttemptAt) ? raw.nextAttemptAt : now
  } else {
    job.nextAttemptAt = null
  }
  return job
}

/**
 * 从持久化存储恢复队列（模块加载时调用一次）。
 *
 * 按"上一轮进程在干什么"分类处理：
 * - 'pending'：原样恢复排队，继续自动处理（数值字段缺失的按归一化补齐）；
 * - 'processing'：上次打印中途进程退出 → 转失败任务等人工确认，**不自动重打**；
 * - 'failed'：保持失败状态，等待用户手动补打；
 * - 形状损坏的记录：转失败任务并在 lastError 里说明；连对象都不是的只能丢弃。
 */
function restoreQueueFromStorage() {
  const stored = PrintQueueManager.getQueue()
  if (!Array.isArray(stored) || stored.length === 0) return

  const now = Date.now()
  const restored = []
  let droppedCount = 0

  stored.forEach((raw) => {
    const job = normalizeStoredJob(raw, now)
    if (job) restored.push(job)
    else droppedCount += 1
  })

  queue = restored

  if (droppedCount > 0) {
    console.warn(`[打印队列] 本地缓存的 ${droppedCount} 条记录无法解析，已丢弃`)
  }

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
