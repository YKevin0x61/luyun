import { describe, expect, it } from 'vitest'
import { hubOverviewPresentation } from '../hubOverviewPresentation.js'

describe('hubOverviewPresentation', () => {
  it('keeps numbers primary when realtime is connected', () => {
    expect(hubOverviewPresentation('connected')).toEqual({
      alertPrimary: false,
      numbersTrusted: true
    })
  })

  it('switches to alert-primary when disconnected or reconnecting', () => {
    expect(hubOverviewPresentation('disconnected')).toEqual({
      alertPrimary: true,
      numbersTrusted: false
    })
    expect(hubOverviewPresentation('reconnecting')).toEqual({
      alertPrimary: true,
      numbersTrusted: false
    })
  })

  it('treats unknown status as alert-primary', () => {
    expect(hubOverviewPresentation(undefined)).toEqual({
      alertPrimary: true,
      numbersTrusted: false
    })
  })
})
