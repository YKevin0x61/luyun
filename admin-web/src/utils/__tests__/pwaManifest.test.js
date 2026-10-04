import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it, vi } from 'vitest'
import { applyPwaManifest, selectPwaManifest } from '../pwaManifest'
import { STAFF_ENTRY_PATH, isStaffLandingPath } from '../staffPaths.js'

const here = dirname(fileURLToPath(import.meta.url))

describe('pwaManifest', () => {
  it('selects role manifests by route', () => {
    expect(selectPwaManifest('/').role).toBe('admin')
    expect(selectPwaManifest('/staff/clean').role).toBe('hygiene')
    expect(selectPwaManifest('/workbench/roster').role).toBe('admin')
    expect(selectPwaManifest('/recipe/manage').role).toBe('recipe')
    expect(selectPwaManifest('/settings').role).toBe('admin')
  })

  it('员工端三页挂员工清单，不是管理端那份', () => {
    // 票 04 起员工端整体在 /staff/*：挂错清单的话手机上装出来的是「厨务管家管理」。
    for (const path of ['/staff/today', '/staff/today/', '/staff/month', '/staff/clean']) {
      expect(selectPwaManifest(path).role, path).toBe('hygiene')
    }
    expect(selectPwaManifest('/staff/today').themeColor).toBe('#16a34a')
    expect(selectPwaManifest('/staff/today').manifest).toBe('/pwa/manifests/hygiene.webmanifest')
  })

  it('旧员工路径已删除：装出来不再是员工清单（旧图标点进去是空壳）', () => {
    // ADR 0091 的取舍：不给旧地址留别名，所以这里也不该再认它们。
    for (const stale of ['/today', '/today/month', '/hygiene']) {
      expect(selectPwaManifest(stale).role, stale).toBe('admin')
    }
  })

  it('自助注册页（票 02 起是顶层 /register）也挂员工清单', () => {
    // 新人在注册页「添加到主屏幕」，装出来还得是青绿的员工应用 —— 这里挂成管理端，
    // 他装完打开是深色的管理后台，反而找不到自己该去哪。
    expect(selectPwaManifest('/register').role).toBe('hygiene')
    expect(selectPwaManifest('/register').themeColor).toBe('#16a34a')
  })

  it('/login 上挂哪份清单看面板身份（票 07）', () => {
    // `/login` 一条路径装两种身份：归属只能由面板当下那一栏决定 —— 员工在员工栏上
    // 「添加到主屏幕」，装出来必须是青绿的员工应用（打开即 /staff/today）。
    expect(selectPwaManifest('/login', 'staff').role).toBe('hygiene')
    expect(selectPwaManifest('/login', 'staff').themeColor).toBe('#16a34a')
    expect(selectPwaManifest('/login', 'staff').manifest).toBe(
      '/pwa/manifests/hygiene.webmanifest',
    )
  })

  it('/login 的管理员栏与「身份未知」都兜底管理端清单', () => {
    // 身份未知 = 调用方没给（首帧还没算出栏位）：保持 index.html 里那份默认清单。
    for (const tab of ['admin', undefined, null, '', 'weird']) {
      expect(selectPwaManifest('/login', tab).role, String(tab)).toBe('admin')
    }
  })

  it('面板身份只对 /login 生效，别的路径照旧按路径选', () => {
    expect(selectPwaManifest('/workbench/roster', 'staff').role).toBe('admin')
    expect(selectPwaManifest('/settings', 'staff').role).toBe('admin')
    expect(selectPwaManifest('/', 'staff').role).toBe('admin')
    expect(selectPwaManifest('/staff/today', 'admin').role).toBe('hygiene')
    expect(selectPwaManifest('/register', 'admin').role).toBe('hygiene')
    expect(selectPwaManifest('/recipe/qr', 'staff').role).toBe('recipe')
  })

  it('员工清单的 start_url 就是员工入口（往返语义，票 07 收口）', () => {
    // 票 04 留下的中间态：`start_url` 还烧在已被删除的 `/today` 上，员工装出来点开是空壳。
    // 这里断往返 —— 清单的 start_url 必须等于 staffPaths.js 里那个员工入口常量，而且这个
    // 地址本身确实是一个员工落点、挂的还是员工清单。
    const manifest = JSON.parse(
      readFileSync(join(here, '../../../public/pwa/manifests/hygiene.webmanifest'), 'utf8'),
    )
    expect(manifest.start_url).toBe(STAFF_ENTRY_PATH)
    expect(isStaffLandingPath(manifest.start_url)).toBe(true)
    expect(selectPwaManifest(manifest.start_url).role).toBe('hygiene')
    // 身份靠 id 区分，两份 scope 都是 `/`（三页同在 /staff/* 下：只管一页的话页间跳转会跳出应用）。
    expect(manifest.id).toBe('/pwa/manifests/hygiene.webmanifest')
    expect(manifest.scope).toBe('/')

    const admin = JSON.parse(
      readFileSync(join(here, '../../../public/pwa/manifests/admin.webmanifest'), 'utf8'),
    )
    expect(admin.id).toBe('/pwa/manifests/admin.webmanifest')
    expect(admin.scope).toBe('/')
    expect(admin.start_url).toBe('/')
  })

  it('updates manifest, apple icon and theme color', () => {
    const elements = {
      manifest: { setAttribute: vi.fn() },
      appleIcon: { setAttribute: vi.fn() },
      theme: { setAttribute: vi.fn() },
    }
    const documentRef = {
      getElementById: (id) => {
        if (id === 'app-manifest') return elements.manifest
        if (id === 'app-apple-touch-icon') return elements.appleIcon
        return null
      },
      querySelector: () => elements.theme,
    }

    const selected = applyPwaManifest('/staff/clean', null, documentRef)

    expect(selected.role).toBe('hygiene')
    expect(elements.manifest.setAttribute).toHaveBeenCalledWith(
      'href',
      '/pwa/manifests/hygiene.webmanifest',
    )
    expect(elements.appleIcon.setAttribute).toHaveBeenCalledWith(
      'href',
      '/pwa/icons/hygiene-192.png',
    )
    expect(elements.theme.setAttribute).toHaveBeenCalledWith('content', '#16a34a')
  })

  it('applyPwaManifest 也认 /login 上的面板身份（DOM 上那个 link 跟着换）', () => {
    const elements = {
      manifest: { setAttribute: vi.fn() },
      appleIcon: { setAttribute: vi.fn() },
      theme: { setAttribute: vi.fn() },
    }
    const documentRef = {
      getElementById: (id) => {
        if (id === 'app-manifest') return elements.manifest
        if (id === 'app-apple-touch-icon') return elements.appleIcon
        return null
      },
      querySelector: () => elements.theme,
    }

    const selected = applyPwaManifest('/login', 'staff', documentRef)

    expect(selected.role).toBe('hygiene')
    expect(elements.manifest.setAttribute).toHaveBeenCalledWith(
      'href',
      '/pwa/manifests/hygiene.webmanifest',
    )
    expect(elements.theme.setAttribute).toHaveBeenCalledWith('content', '#16a34a')
  })

  it('ignores an invalid document-like argument', () => {
    expect(applyPwaManifest('/recipe', null, '/previous-route')).toMatchObject({
      role: 'recipe',
    })
  })
})
