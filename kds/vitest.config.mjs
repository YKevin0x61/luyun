import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

/**
 * `kds/uni_modules/kds-bluetooth-printer` 是自研 UTS 插件：仓库里只有 `utssdk/*.uts` 源码，
 * 没有可被 Vite 解析的 JS 入口（各平台代码由 HBuilderX 在构建期编译）。
 * `utils/bluetoothPrinter.js` 里那 8 个按名导入位于 `// #ifdef APP-PLUS` 之下——H5 构建会把
 * 它们剥掉，vitest 不做条件编译，所以是一段活 import。这里用虚拟模块顶上（等价于 H5 的
 * 「没有实现」），打印链路 printQueue → dishTicketPrinter → bluetoothPrinter 才能在 vitest
 * 里被 import；真去调用任何一个 UTS API 都会立刻抛错，避免测试里误以为它能工作。
 */
function utsPrintPluginStub() {
  const VIRTUAL_ID = '\0kds-bluetooth-printer-stub'
  const API_NAMES = [
    'getPairedDevices',
    'stopDiscovery',
    'connect',
    'disconnect',
    'isConnected',
    'printText',
    'printNewLine',
    'cutPaper',
  ]
  const source = [
    'const unavailable = (name) => () => {',
    "  throw new Error('kds-bluetooth-printer.' + name + ' 只在 APP-PLUS 构建里可用：自研 UTS 插件没有可用 JS 入口')",
    '}',
    ...API_NAMES.map((name) => `export const ${name} = unavailable('${name}')`),
    `export default { ${API_NAMES.join(', ')} }`,
    '',
  ].join('\n')

  // 别名可能先把 `@/uni_modules/…` 改写成仓库内路径，所以 resolveId 与 load 两处都认这个后缀。
  const isUtsPluginId = (id) => id.includes('uni_modules/kds-bluetooth-printer')

  return {
    name: 'kds-uts-print-plugin-stub',
    enforce: 'pre',
    resolveId(id) {
      if (isUtsPluginId(id)) return VIRTUAL_ID
    },
    load(id) {
      if (id === VIRTUAL_ID || isUtsPluginId(id)) return source
    },
  }
}

// Separate from vite.config.js so unit tests do not load @dcloudio/vite-plugin-uni
// (that plugin is supplied by HBuilderX / local uni tooling, not this package's npm deps).
// @vitejs/plugin-vue here only compiles .vue SFCs for the page-mount test seam below —
// it does not replace vite.config.js for the actual uni-app H5 build.
export default defineConfig({
  plugins: [
    utsPrintPluginStub(),
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
      // 与 jsconfig.json 的 `"@/*": ["./*"]` 保持一致：`@/…` 是仓库里在用的导入写法
      // （components/SvgIcon/SvgIcon.vue → @/utils/iconPaths.js），vitest 必须同样能解析，
      // 否则页面级用例只能把 SvgIcon 整个 mock 掉。自研 UTS 插件的 `@/uni_modules/…`
      // 由上面的 utsPrintPluginStub() 插件接住（虚拟模块，优先于本条泛化别名）。
      '@': fileURLToPath(new URL('.', import.meta.url)).replace(/\/$/, ''),
    },
  },
  test: {
    // Default stays 'node' to match the existing pure-logic test style; page-mount
    // tests opt into a DOM per-file via a `// @vitest-environment happy-dom` docblock.
    environment: 'node',
    include: ['**/__tests__/**/*.test.js'],
  },
})
