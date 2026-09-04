/** Debounced unsaved structured-field preview. HTML comes from POST /api/recipes/preview-body. */

export const STRUCTURED_PREVIEW_DEBOUNCE_MS = 200

export function createStructuredBodyPreview({ postPreview, debounceMs = STRUCTURED_PREVIEW_DEBOUNCE_MS }) {
  let html = ''
  let timer = null
  let seq = 0
  let disposed = false
  const listeners = new Set()

  function notify() {
    for (const fn of listeners) fn(html)
  }

  async function run(body) {
    const mySeq = ++seq
    try {
      const data = await postPreview(body)
      if (disposed || mySeq !== seq) return
      html = typeof data?.html === 'string' ? data.html : ''
      notify()
    } catch {
      // Keep last successful HTML (including never-previewed empty).
    }
  }

  return {
    subscribe(fn) {
      listeners.add(fn)
      fn(html)
      return () => listeners.delete(fn)
    },
    schedule(body) {
      if (disposed) return
      if (timer != null) clearTimeout(timer)
      const payload = {
        ingredients: body?.ingredients || [],
        steps: body?.steps || [],
        tips: body?.tips || [],
      }
      timer = setTimeout(() => {
        timer = null
        if (!disposed) run(payload)
      }, debounceMs)
    },
    dispose() {
      disposed = true
      if (timer != null) clearTimeout(timer)
      timer = null
      listeners.clear()
    },
  }
}
