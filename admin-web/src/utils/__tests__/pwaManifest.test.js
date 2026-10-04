import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it, vi } from 'vitest'
import { applyPwaManifest, selectPwaManifest } from '../pwaManifest'
import { WORKBENCH_ROOT } from '../workbenchPaths.js'

const here = dirname(fileURLToPath(import.meta.url))
const manifestDir = join(here, '../../../public/pwa/manifests')
const iconDir = join(here, '../../../public/pwa/icons')

/** 票 09 收口后的归属表：只有两份清单，工作台前缀归工作台、其余归管理端。 */
describe('pwaManifest：App 归属判据（票 09 收敛）', () => {
  it('工作台前缀（含子路径）归工作台清单', () => {
    for (const path of [
      '/workbench',
      '/workbench/',
      WORKBENCH_ROOT,
      '/workbench/hr/calendar',
      '/workbench/hr/roster',
      '/workbench/floor/daily',
      '/workbench/me/today',
      '/workbench/me/clean',
      '/workbench/kitchen/recipe',
      '/workbench/kitchen/recipe/detail',
      '/workbench/kitchen/recipe/manage',
      '/workbench/kitchen/prep-plan',
      '/workbench/forbidden',
    ]) {
      expect(selectPwaManifest(path).role, path).toBe('workbench')
    }
  })

  it('登录页与系统管理面归管理端清单', () => {
    for (const path of [
      '/login',
      '/',
      '/index.html',
      '/admin',
      '/admin/',
      '/sales-report',
      '/wecom-push',
      '/logs',
      '/settings',
      '/prep-plan',
    ]) {
      expect(selectPwaManifest(path).role, path).toBe('admin')
    }
  })

  it('/login 不再随面板栏位变清单：装出来的就是管理端那一份', () => {
    // 票 07 起 `/login` 一条路径挂两份清单（看当下停在哪一栏）。票 09 收敛成
    // 「登录页归管理端」—— 判据只看路径，不再吃「面板身份」这个参数。
    for (const tab of ['staff', 'admin', undefined, null, '', 'weird']) {
      expect(selectPwaManifest('/login', tab).role, String(tab)).toBe('admin')
    }
  })

  it('员工自助注册页仍归工作台清单（新人装出来要是员工要用的那个应用）', () => {
    // `/register` 不在工作台前缀里，但它是员工侧界面（`staffPaths.js` 的
    // STAFF_PHONE_EXACT），装成深色管理端只会让人找不到自己该去哪。
    expect(selectPwaManifest('/register').role).toBe('workbench')
    expect(selectPwaManifest('/register').manifest).toBe(
      '/pwa/manifests/workbench.webmanifest',
    )
  })

  it('旧地址（/staff/*、/recipe*、/hygiene/*）不再有自己的清单，一律落管理端', () => {
    for (const stale of [
      '/staff/today',
      '/staff/clean',
      '/recipe',
      '/recipe/qr',
      '/hygiene',
      '/scheduling',
    ]) {
      expect(selectPwaManifest(stale).role, stale).toBe('admin')
    }
  })
})

describe('pwaManifest：两份清单的内容', () => {
  it('工作台清单把 start_url 与 scope 都锁在工作台根', () => {
    const manifest = JSON.parse(
      readFileSync(join(manifestDir, 'workbench.webmanifest'), 'utf8'),
    )
    expect(manifest.name).toBe('厨务管家工作台')
    expect(manifest.short_name).toBe('厨务管家工作台')
    expect(manifest.start_url).toBe(WORKBENCH_ROOT)
    // scope 不带尾斜杠：`/workbench/` 不覆盖 `/workbench`（本仓 start_url 就是它），
    // 实测见 .scratch/workbench-subapp/issues/09-workbench-pwa.md 票尾。
    expect(manifest.scope).toBe(WORKBENCH_ROOT)
    expect(manifest.display).toBe('standalone')
    expect(manifest.id).toBe('/workbench/')
    // 深青墨外壳（--hy-bg）与薄荷信号色（--hy-mint）：与页面里真正渲染的一致。
    expect(manifest.background_color).toBe('#0a1719')
    expect(manifest.theme_color).toBe('#0a1719')
    for (const icon of manifest.icons) {
      expect(icon.src.startsWith('/pwa/icons/workbench-'), icon.src).toBe(true)
      expect(existsSync(join(iconDir, icon.src.split('/').pop())), icon.src).toBe(true)
    }
    expect(manifest.icons.map((icon) => icon.sizes)).toEqual(['192x192', '512x512', '512x512'])
    expect(manifest.icons.some((icon) => icon.purpose === 'maskable')).toBe(true)
  })

  it('管理端清单还在，且不再抢工作台前缀', () => {
    const manifest = JSON.parse(
      readFileSync(join(manifestDir, 'admin.webmanifest'), 'utf8'),
    )
    expect(manifest.start_url).toBe('/')
    expect(manifest.scope).toBe('/')
  })

  it('三份旧清单只剩管理端那一份：hygiene / recipe 已删', () => {
    expect(existsSync(join(manifestDir, 'hygiene.webmanifest'))).toBe(false)
    expect(existsSync(join(manifestDir, 'recipe.webmanifest'))).toBe(false)
    expect(existsSync(join(manifestDir, 'workbench.webmanifest'))).toBe(true)
    expect(existsSync(join(manifestDir, 'admin.webmanifest'))).toBe(true)
  })

  it('判据给的两份清单确实存在，且各自的 worker 从自己的路径下提供', () => {
    for (const path of ['/workbench/hr/calendar', '/settings']) {
      const selected = selectPwaManifest(path)
      expect(existsSync(join(manifestDir, selected.manifest.split('/').pop())), path).toBe(
        true,
      )
      expect(selected.serviceWorker.startsWith('/'), selected.serviceWorker).toBe(true)
    }
    expect(selectPwaManifest('/workbench').serviceWorker).toBe('/workbench/sw.js')
    expect(selectPwaManifest('/workbench').serviceWorkerScope).toBe(WORKBENCH_ROOT)
    expect(selectPwaManifest('/settings').serviceWorker).toBe('/sw.js')
    expect(selectPwaManifest('/settings').serviceWorkerScope).toBe('/')
  })
})

describe('pwaManifest：DOM 落点', () => {
  function fakeDocument() {
    const elements = {
      manifest: { setAttribute: vi.fn() },
      appleIcon: { setAttribute: vi.fn() },
      theme: { setAttribute: vi.fn() },
    }
    return {
      elements,
      documentRef: {
        getElementById: (id) => {
          if (id === 'app-manifest') return elements.manifest
          if (id === 'app-apple-touch-icon') return elements.appleIcon
          return null
        },
        querySelector: () => elements.theme,
      },
    }
  }

  it('工作台路径换工作台清单、图标与主题色', () => {
    const { elements, documentRef } = fakeDocument()

    const selected = applyPwaManifest('/workbench/hr/roster', documentRef)

    expect(selected.role).toBe('workbench')
    expect(elements.manifest.setAttribute).toHaveBeenCalledWith(
      'href',
      '/pwa/manifests/workbench.webmanifest',
    )
    expect(elements.appleIcon.setAttribute).toHaveBeenCalledWith(
      'href',
      '/pwa/icons/workbench-192.png',
    )
    expect(elements.theme.setAttribute).toHaveBeenCalledWith('content', '#0a1719')
  })

  it('管理端页面换回深色管理端清单', () => {
    const { elements, documentRef } = fakeDocument()

    const selected = applyPwaManifest('/settings', documentRef)

    expect(selected.role).toBe('admin')
    expect(elements.manifest.setAttribute).toHaveBeenCalledWith(
      'href',
      '/pwa/manifests/admin.webmanifest',
    )
    expect(elements.appleIcon.setAttribute).toHaveBeenCalledWith(
      'href',
      '/pwa/icons/admin-192.png',
    )
    expect(elements.theme.setAttribute).toHaveBeenCalledWith('content', '#0a0d16')
  })

  it('ignores an invalid document-like argument', () => {
    expect(applyPwaManifest('/workbench/hr/roster', '/previous-route')).toMatchObject({
      role: 'workbench',
    })
  })
})
