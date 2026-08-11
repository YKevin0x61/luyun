export function formatDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function parseLocalDate(dateText) {
  const [year, month, day] = String(dateText).split('-').map(Number)
  return new Date(year, month - 1, day)
}

export function addDays(date, dayCount) {
  const next = new Date(date)
  next.setDate(next.getDate() + dayCount)
  return next
}

export function dayDiff(start, end) {
  const s = parseLocalDate(start)
  const e = parseLocalDate(end)
  return Math.max(1, Math.round((e - s) / 86400000) + 1)
}

export function quickRange(type) {
  const now = new Date()
  const start = new Date(now)
  const end = new Date(now)
  if (type === 'yesterday') {
    start.setDate(start.getDate() - 1)
    end.setDate(end.getDate() - 1)
  } else if (type === 'week') {
    start.setDate(start.getDate() - 6)
  } else if (type === 'month') {
    start.setDate(1)
  } else if (type === 'lastWeek') {
    const day = now.getDay() || 7
    start.setDate(now.getDate() - day - 6)
    end.setDate(now.getDate() - day)
  } else if (type === 'lastMonth') {
    start.setMonth(now.getMonth() - 1, 1)
    end.setDate(0)
  }
  return { start: formatDate(start), end: formatDate(end) }
}
