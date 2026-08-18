import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// Separate from vite.config.js so unit tests do not load @dcloudio/vite-plugin-uni
// (that plugin is supplied by HBuilderX / local uni tooling, not this package's npm deps).
// @vitejs/plugin-vue here only compiles .vue SFCs for the page-mount test seam below —
// it does not replace vite.config.js for the actual uni-app H5 build.
export default defineConfig({
  plugins: [
    vue({
      // uni-app's `view`/`text`/`scroll-view` tags aren't real components; without this
      // Vue's compiler still renders them (falling back to a plain element) but warns.
      template: { compilerOptions: { isCustomElement: (tag) => tag.includes('-') } },
    }),
  ],
  resolve: {
    alias: {
      // '@dcloudio/uni-app' is a compiler-macro package supplied by HBuilderX /
      // uni-app tooling, not an npm dependency — stub it so page SFCs resolve under vitest.
      '@dcloudio/uni-app': fileURLToPath(new URL('./test/stubs/uni-app.js', import.meta.url)),
    },
  },
  test: {
    // Default stays 'node' to match the existing pure-logic test style; page-mount
    // tests opt into a DOM per-file via a `// @vitest-environment happy-dom` docblock.
    environment: 'node',
    include: ['**/__tests__/**/*.test.js'],
  },
})
