import { describe, expect, it } from 'vitest'
import { nextRecipeDrawerState } from '../recipeDrawer.js'

describe('nextRecipeDrawerState', () => {
  it('Escape on an open panel closes it and names that trigger to refocus', () => {
    expect(nextRecipeDrawerState({ open: 'toc' }, { type: 'escape' })).toEqual({
      open: null,
      restoreFocus: 'toc',
    })
  })

  it('Escape when already closed is a no-op', () => {
    expect(nextRecipeDrawerState({ open: null }, { type: 'escape' })).toEqual({
      open: null,
      restoreFocus: null,
    })
  })

  it('backdrop click matches Escape', () => {
    expect(nextRecipeDrawerState({ open: 'more' }, { type: 'backdrop' })).toEqual({
      open: null,
      restoreFocus: 'more',
    })
  })

  it('toggle of the open panel closes it and restores that trigger', () => {
    expect(nextRecipeDrawerState({ open: 'font' }, { type: 'toggle', id: 'font' })).toEqual({
      open: null,
      restoreFocus: 'font',
    })
  })

  it('toggle of a different panel switches without restoring focus', () => {
    expect(nextRecipeDrawerState({ open: 'toc' }, { type: 'toggle', id: 'more' })).toEqual({
      open: 'more',
      restoreFocus: null,
    })
  })

  it('toggle from closed opens the named panel', () => {
    expect(nextRecipeDrawerState({ open: null }, { type: 'toggle', id: 'search' })).toEqual({
      open: 'search',
      restoreFocus: null,
    })
  })

  it('close from a TOC link restores the toc trigger', () => {
    expect(nextRecipeDrawerState({ open: 'toc' }, { type: 'close' })).toEqual({
      open: null,
      restoreFocus: 'toc',
    })
  })
})
