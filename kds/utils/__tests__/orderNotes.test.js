import { describe, expect, it } from 'vitest'
import { canonicalOrderNotes, serveTicketNotesLine } from '../orderNotes.js'

describe('canonicalOrderNotes', () => {
  it('keeps 做法 copy and treats empty / 外卖平台: as no 备注', () => {
    expect(canonicalOrderNotes('免葱')).toBe('免葱')
    expect(canonicalOrderNotes('  免葱  ')).toBe('免葱')
    expect(canonicalOrderNotes('')).toBe('')
    expect(canonicalOrderNotes('   ')).toBe('')
    expect(canonicalOrderNotes(null)).toBe('')
    expect(canonicalOrderNotes(undefined)).toBe('')
    expect(canonicalOrderNotes('外卖平台:美团|来源:美团1')).toBe('')
  })
})

describe('serveTicketNotesLine', () => {
  it('prints 「备注: …」 only when canonical notes are present', () => {
    expect(serveTicketNotesLine('免葱')).toBe('备注: 免葱')
    expect(serveTicketNotesLine('  免葱  ')).toBe('备注: 免葱')
    expect(serveTicketNotesLine('')).toBe('')
    expect(serveTicketNotesLine(null)).toBe('')
    expect(serveTicketNotesLine('外卖平台:美团|来源:美团1')).toBe('')
  })
})
