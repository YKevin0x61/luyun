// 测试的浏览器存储垫片（Node 22+ 与 jsdom 的组合坑）。
//
// 起因：`npx vitest run src/components/workbench` 有 11 个用例报
// 「Cannot read properties of undefined (reading 'clear')」，对应测试里那句裸的
// `localStorage.clear()`；`src/views/workbench` 那组也是同一根因，加起来 50 多个。
//
// 两层原因叠在一起：
//  1. Node 22 起把 `localStorage` / `sessionStorage` 做成了**全局访问器**，默认未启用
//     （`--localstorage-file` 没给），取出来是 `undefined`，还会打一条
//     `ExperimentalWarning: localStorage is not available because --localstorage-file was not provided`；
//  2. 本仓库的 vitest 跑在 `environment: 'node'` 下，靠各测试文件头部的
//     `// @vitest-environment jsdom` 单独切到 DOM —— 这一档里 `window` 在，但
//     `window.localStorage` 也不可用（实测同一个 worker 里两者都是 undefined）。
//
// 修法：**不依赖 jsdom 的 storage**，给测试装一份够用的内存实现（Map 支撑的
// Storage 子集：length / key / getItem / setItem / removeItem / clear）。只补缺失的，
// 已经可用的环境不动；node 环境的用例没有 `window`，直接跳过。
function makeMemoryStorage() {
  const map = new Map()
  return {
    get length() {
      return map.size
    },
    key(index) {
      return [...map.keys()][index] ?? null
    },
    getItem(key) {
      const value = map.get(String(key))
      return value === undefined ? null : value
    },
    setItem(key, value) {
      map.set(String(key), String(value))
    },
    removeItem(key) {
      map.delete(String(key))
    },
    clear() {
      map.clear()
    },
  }
}

if (typeof window !== 'undefined') {
  for (const name of ['localStorage', 'sessionStorage']) {
    let storage = window[name]
    if (!storage) {
      storage = makeMemoryStorage()
      try {
        Object.defineProperty(window, name, { configurable: true, value: storage })
      } catch {
        // jsdom 的 window 属性写不动时只挂 globalThis（下面的 getter 回退到这份内存实现）。
      }
    }
    if (!globalThis[name]) {
      try {
        Object.defineProperty(globalThis, name, {
          configurable: true,
          get: () => window[name] || storage,
        })
      } catch {
        // 装不上就让测试自己去发现 —— 至少不把整个文件搞坏。
      }
    }
  }
}
