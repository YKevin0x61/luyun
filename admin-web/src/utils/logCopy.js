const ERROR_LEVELS = new Set(['ERROR', 'CRITICAL'])

export function normalizeLogLevel(level) {
  return String(level || 'INFO').toUpperCase()
}

export function selectLogsForCopy(items, scope) {
  const list = Array.isArray(items) ? items : []
  if (scope === 'error') {
    return list.filter((item) => ERROR_LEVELS.has(normalizeLogLevel(item?.level)))
  }
  if (scope === 'warning') {
    return list.filter((item) => normalizeLogLevel(item?.level) === 'WARNING')
  }
  return list
}

export function buildLogsCopyText(items, formatTimestamp) {
  return (Array.isArray(items) ? items : []).map((item) => {
    const level = normalizeLogLevel(item?.level)
    const line = `${formatTimestamp(item?.timestamp || item?.ts)} [${level}] ${item?.logger || ''} ${item?.message || ''}`
    return item?.exception ? `${line}\n${item.exception}` : line
  }).join('\n')
}
