import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { compileScript, compileTemplate, parse } from 'vue/compiler-sfc'

const here = dirname(fileURLToPath(import.meta.url))
const srcRoot = join(here, '..')

/** 这个目录下所有 .vue（跳过测试目录：那里没有组件）。 */
function vueFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) {
      if (name === '__tests__' || name === 'node_modules') continue
      vueFiles(full, out)
    } else if (name.endsWith('.vue')) {
      out.push(full)
    }
  }
  return out
}

describe('每个 .vue 都编译得过', () => {
  it('SFC 的 script 与 template 都能编译（改完前端忘了跑 build 的那种错）', () => {
    // 这一条是因为**真出过事**：一个 computed 和一个函数起了同一个名字，`npm test`
    // 全绿（仓库里的页面测试是「读源码 + 正则」的契约断言，看不见编译期错误），而
    // `npm run build` 直接失败。CI 的 admin-web job 会 build，但本地改完只跑
    // `npm test` 就提交的话，坏代码会先进库 —— 那次已经进了，靠第二次提交才补回来。
    //
    // 所以这里把「能不能编译」也变成一条测试：不替代 build（产物与 PWA 那部分它不管），
    // 只挡住语法/绑定层面的错，让本地那条 `npm test` 也能发现。
    const files = vueFiles(srcRoot)
    expect(files.length).toBeGreaterThan(50)

    const broken = []
    for (const file of files) {
      const label = relative(srcRoot, file)
      const source = readFileSync(file, 'utf8')
      const { descriptor, errors } = parse(source, { filename: file })
      if (errors && errors.length) {
        broken.push(`${label}: ${errors[0].message}`)
        continue
      }
      try {
        if (descriptor.scriptSetup || descriptor.script) {
          compileScript(descriptor, { id: label })
        }
        if (descriptor.template) {
          const result = compileTemplate({
            source: descriptor.template.content,
            filename: file,
            id: label,
          })
          if (result.errors && result.errors.length) {
            const first = result.errors[0]
            broken.push(`${label}: ${first.message || first}`)
          }
        }
      } catch (err) {
        broken.push(`${label}: ${err.message}`)
      }
    }

    expect(broken).toEqual([])
  })
})
