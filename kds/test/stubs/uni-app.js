/**
 * Test-only stand-in for the `@dcloudio/uni-app` compiler-macro package
 * (provided by HBuilderX / uni-app tooling at build time, not an npm dependency).
 * Aliased in vitest.config.mjs so page SFCs can be mounted under vitest.
 */

export function onShow(callback) {
  callback()
}
