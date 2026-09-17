import { describe, expect, it } from 'vitest'
import { buildLogsCopyText, normalizeLogLevel, selectLogsForCopy } from '../logCopy.js'

const logs = [
  { level: 'INFO', logger: 'worker', message: 'ready', timestamp: '2026-09-17 10:00:00' },
  { level: 'WARNING', logger: 'worker', message: 'slow', timestamp: '2026-09-17 10:01:00' },
  { level: 'ERROR', logger: 'api', message: 'failed', timestamp: '2026-09-17 10:02:00' },
  { level: 'critical', logger: 'db', message: 'offline', timestamp: '2026-09-17 10:03:00' },
]

describe('selectLogsForCopy', () => {
  it('selects only warning logs', () => {
    expect(selectLogsForCopy(logs, 'warning').map((item) => item.message)).toEqual(['slow'])
  })

  it('selects error and critical logs', () => {
    expect(selectLogsForCopy(logs, 'error').map((item) => item.message)).toEqual(['failed', 'offline'])
  })

  it('keeps all logs for the default scope', () => {
    expect(selectLogsForCopy(logs, 'all')).toEqual(logs)
    expect(selectLogsForCopy(null, 'all')).toEqual([])
  })
})

describe('buildLogsCopyText', () => {
  it('formats the selected logs in display order', () => {
    const text = buildLogsCopyText(
      selectLogsForCopy(logs, 'error'),
      (value) => value,
    )

    expect(text).toBe(
      '2026-09-17 10:02:00 [ERROR] api failed\n2026-09-17 10:03:00 [CRITICAL] db offline',
    )
  })

  it('includes exception details on the following lines', () => {
    expect(buildLogsCopyText(
      [{
        level: 'ERROR',
        logger: 'worker',
        message: 'failed',
        exception: 'DatabaseError: locked\n  at writeOrder()',
        timestamp: '2026-09-17 10:02:00',
      }],
      (value) => value,
    )).toBe(
      '2026-09-17 10:02:00 [ERROR] worker failed\nDatabaseError: locked\n  at writeOrder()',
    )
  })

  it('defaults missing levels to INFO', () => {
    expect(normalizeLogLevel(null)).toBe('INFO')
    expect(buildLogsCopyText(
      [{ logger: 'worker', message: 'ready' }],
      () => '',
    )).toBe(' [INFO] worker ready')
  })
})
