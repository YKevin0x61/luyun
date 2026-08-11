// Do not `import … from 'vite'` here.
// HBuilderX bundles this file with esbuild externalize-deps + require().
// Installing vitest hoists an ESM-only vite into kds/node_modules; requiring
// that package fails. defineConfig is optional — a plain object is enough.
import uni from '@dcloudio/vite-plugin-uni'

export default {
  plugins: [uni()],
}
