import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  READER_DRAG_BLOCK_MQ,
  applySubsetOrder,
  eventElement,
  readerDragAllowed,
} from '../recipeReaderDrag.js'

const here = dirname(fileURLToPath(import.meta.url))

describe('readerDragAllowed', () => {
  it('only logged-in desktop fine pointers may drag', () => {
    expect(readerDragAllowed({ canEdit: true, blocked: false })).toBe(true)
    expect(readerDragAllowed({ canEdit: false, blocked: false })).toBe(false)
    expect(readerDragAllowed({ canEdit: true, blocked: true })).toBe(false)
    expect(readerDragAllowed({ canEdit: false, blocked: true })).toBe(false)
  })
})

describe('READER_DRAG_BLOCK_MQ', () => {
  it('treats the 900px reader breakpoint and coarse pointers as mobile', () => {
    expect(READER_DRAG_BLOCK_MQ).toBe('(max-width: 900px), (pointer: coarse)')
  })
})

describe('eventElement', () => {
  it('walks up from a text node to its parent element', () => {
    expect(eventElement({ target: { nodeType: 3, parentElement: { nodeType: 1 } } })).toEqual({ nodeType: 1 })
    expect(eventElement({ target: { nodeType: 1, parentElement: null } })).toEqual({ nodeType: 1, parentElement: null })
    expect(eventElement({ target: null })).toBeNull()
  })
})

describe('applySubsetOrder', () => {
  it('rewrites only the subset slots and keeps other ids in place', () => {
    expect(applySubsetOrder([10, 20, 30, 40], [30, 10])).toEqual([30, 20, 10, 40])
  })

  it('fills the API full-id list when filters hide some cards', () => {
    expect(applySubsetOrder([1, 2, 3, 4], [3, 1])).toEqual([3, 2, 1, 4])
  })

  it('ignores unknown or duplicate subset ids', () => {
    expect(applySubsetOrder([1, 2, 3], [3, 99, 3, 1])).toEqual([3, 2, 1])
  })

  it('coerces string ids to integers', () => {
    expect(applySubsetOrder(['1', '2', '3'], ['3', '1'])).toEqual([3, 2, 1])
  })

  it('returns the full list unchanged when the subset is empty', () => {
    expect(applySubsetOrder([4, 5], [])).toEqual([4, 5])
  })
})

describe('RecipeDetailView reader drag wiring', () => {
  const src = readFileSync(join(here, '../../views/recipe/RecipeDetailView.vue'), 'utf8')

  it('binds card drag after render and syncs when login or viewport changes', () => {
    expect(src).toContain('bindReaderDrag')
    expect(src).toContain('syncCardDraggable')
    expect(src).toContain('READER_DRAG_BLOCK_MQ')
    expect(src).toContain('applySubsetOrder')
    expect(src).toContain('readerDragAllowed')
  })
})
