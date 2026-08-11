import { defineConfig } from 'vitest/config'

// Separate from vite.config.js so unit tests do not load @dcloudio/vite-plugin-uni
// (that plugin is supplied by HBuilderX / local uni tooling, not this package's npm deps).
export default defineConfig({
  test: {
    environment: 'node',
    include: ['**/__tests__/**/*.test.js'],
  },
})
