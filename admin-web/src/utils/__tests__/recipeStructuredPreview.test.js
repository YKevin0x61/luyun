import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  STRUCTURED_PREVIEW_DEBOUNCE_MS,
  createStructuredBodyPreview,
} from '../recipeStructuredPreview.js'

function lastHtml(seen) {
  return seen[seen.length - 1]
}

describe('createStructuredBodyPreview', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not POST until debounce elapses, then publishes html', async () => {
    const postPreview = vi.fn(async () => ({ html: '<p>x</p>' }))
    const preview = createStructuredBodyPreview({ postPreview })
    const seen = []
    preview.subscribe((html) => seen.push(html))

    preview.schedule({
      ingredients: [{ name: '面粉', amount: '200', unit: 'g' }],
      steps: [],
      tips: [],
    })
    expect(postPreview).not.toHaveBeenCalled()
    expect(lastHtml(seen)).toBe('')

    await vi.advanceTimersByTimeAsync(STRUCTURED_PREVIEW_DEBOUNCE_MS)
    expect(postPreview).toHaveBeenCalledTimes(1)
    expect(lastHtml(seen)).toBe('<p>x</p>')
    preview.dispose()
  })

  it('coalesces bursts and POSTs the latest body once', async () => {
    const postPreview = vi.fn(async () => ({ html: '<p>b</p>' }))
    const preview = createStructuredBodyPreview({ postPreview })
    const first = {
      ingredients: [{ name: 'A', amount: '', unit: '' }],
      steps: [],
      tips: [],
    }
    const second = {
      ingredients: [{ name: 'B', amount: '', unit: '' }],
      steps: ['混合'],
      tips: [],
    }
    preview.schedule(first)
    preview.schedule(second)
    await vi.advanceTimersByTimeAsync(STRUCTURED_PREVIEW_DEBOUNCE_MS)
    expect(postPreview).toHaveBeenCalledTimes(1)
    expect(postPreview).toHaveBeenCalledWith(second)
    preview.dispose()
  })

  it('keeps last html when POST throws', async () => {
    const postPreview = vi.fn()
      .mockResolvedValueOnce({ html: '<p>ok</p>' })
      .mockRejectedValueOnce(new Error('nope'))
    const preview = createStructuredBodyPreview({ postPreview })
    const seen = []
    preview.subscribe((html) => seen.push(html))

    preview.schedule({ ingredients: [], steps: ['一步'], tips: [] })
    await vi.advanceTimersByTimeAsync(STRUCTURED_PREVIEW_DEBOUNCE_MS)
    expect(lastHtml(seen)).toBe('<p>ok</p>')

    preview.schedule({ ingredients: [], steps: ['两步'], tips: [] })
    await vi.advanceTimersByTimeAsync(STRUCTURED_PREVIEW_DEBOUNCE_MS)
    expect(lastHtml(seen)).toBe('<p>ok</p>')
    preview.dispose()
  })

  it('clears html after a successful empty preview', async () => {
    const postPreview = vi.fn()
      .mockResolvedValueOnce({ html: '<p>ok</p>' })
      .mockResolvedValueOnce({ html: '' })
    const preview = createStructuredBodyPreview({ postPreview })
    const seen = []
    preview.subscribe((html) => seen.push(html))

    preview.schedule({ ingredients: [], steps: ['一步'], tips: [] })
    await vi.advanceTimersByTimeAsync(STRUCTURED_PREVIEW_DEBOUNCE_MS)
    expect(lastHtml(seen)).toBe('<p>ok</p>')

    preview.schedule({ ingredients: [], steps: [], tips: [] })
    await vi.advanceTimersByTimeAsync(STRUCTURED_PREVIEW_DEBOUNCE_MS)
    expect(lastHtml(seen)).toBe('')
    preview.dispose()
  })

  it('ignores a stale slower response', async () => {
    const resolvers = []
    const postPreview = vi.fn(() => new Promise((resolve) => {
      resolvers.push(resolve)
    }))
    const preview = createStructuredBodyPreview({ postPreview })
    const seen = []
    preview.subscribe((html) => seen.push(html))

    preview.schedule({ ingredients: [{ name: 'A', amount: '', unit: '' }], steps: [], tips: [] })
    await vi.advanceTimersByTimeAsync(STRUCTURED_PREVIEW_DEBOUNCE_MS)
    preview.schedule({ ingredients: [{ name: 'B', amount: '', unit: '' }], steps: [], tips: [] })
    await vi.advanceTimersByTimeAsync(STRUCTURED_PREVIEW_DEBOUNCE_MS)
    expect(postPreview).toHaveBeenCalledTimes(2)

    resolvers[1]({ html: 'B' })
    await Promise.resolve()
    expect(lastHtml(seen)).toBe('B')

    resolvers[0]({ html: 'A' })
    await Promise.resolve()
    expect(lastHtml(seen)).toBe('B')
    preview.dispose()
  })

  it('does not POST after dispose', async () => {
    const postPreview = vi.fn(async () => ({ html: '<p>x</p>' }))
    const preview = createStructuredBodyPreview({ postPreview })
    preview.schedule({ ingredients: [], steps: ['一步'], tips: [] })
    preview.dispose()
    await vi.advanceTimersByTimeAsync(STRUCTURED_PREVIEW_DEBOUNCE_MS)
    expect(postPreview).not.toHaveBeenCalled()
  })
})
