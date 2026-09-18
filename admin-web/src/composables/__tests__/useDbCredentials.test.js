import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
const apiPost = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
  },
}))

const { useDbCredentials } = await import('../useDbCredentials.js')

function makeHarness() {
  return useDbCredentials({ showAlert: vi.fn(), clearAlert: vi.fn() })
}

function statusPayload(overrides = {}) {
  return {
    backend: 'postgres',
    is_postgres: true,
    user: 'luyun',
    host: 'postgres',
    port: '5432',
    database: 'luyun',
    dsn: 'postgresql://luyun:***@postgres:5432/luyun',
    password_length: 32,
    env_file: '/srv/luyun/app/deploy/env.production',
    env_file_writable: true,
    env_override: false,
    env_override_target: null,
    ...overrides,
  }
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
})

describe('useDbCredentials 加载连接信息', () => {
  it('读取 GET /api/admin/db-credentials 并展示为 Postgres 连接', async () => {
    apiGet.mockResolvedValue(statusPayload())
    const h = makeHarness()

    await h.loadDbCred()

    expect(apiGet).toHaveBeenCalledWith('/api/admin/db-credentials')
    expect(h.dbCred.value.backend).toBe('postgres')
    expect(h.dbIsPostgres.value).toBe(true)
    expect(h.dbPasswordLengthLabel.value).toBe('32 位')
    expect(h.dbPasswordWarning.value).toBe('')
    expect(h.dbResetDisabled.value).toBe(false)
  })

  it('加载失败时保留错误文案', async () => {
    apiGet.mockRejectedValue(new Error('后端未初始化'))
    const h = makeHarness()

    await h.loadDbCred()

    expect(h.dbCredError.value).toBe('后端未初始化')
    expect(h.dbCred.value).toBeNull()
  })
})

describe('useDbCredentials 密码强度与禁用条件', () => {
  it('3 位密码：给出醒目警示且允许重置', async () => {
    apiGet.mockResolvedValue(statusPayload({ password_length: 3 }))
    const h = makeHarness()
    await h.loadDbCred()

    expect(h.dbPasswordWarning.value).toBe('当前数据库密码仅 3 位，建议重置为 32 位随机密码。')
    expect(h.dbPasswordWarningType.value).toBe('error')
    expect(h.dbResetDisabled.value).toBe(false)
  })

  it('password_length 为 0（trust 认证）：说明无需也无法重置并禁用按钮', async () => {
    apiGet.mockResolvedValue(statusPayload({ password_length: 0 }))
    const h = makeHarness()
    await h.loadDbCred()

    // 不能对 0 位报「建议重置」，否则会点出一个必然失败的按钮。
    expect(h.dbPasswordWarning.value).toBe('当前连接串未包含密码（本机 trust 认证等），无需也无法在此重置。')
    expect(h.dbResetDisabled.value).toBe(true)
    expect(h.dbResetDisabledReason.value).toContain('无法在此重置')

    // 禁用状态下点击不应打开确认弹窗。
    h.openDbReset()
    expect(h.dbResetConfirm.open).toBe(false)
  })

  it('env_override 为 true：红色警示并禁用重置', async () => {
    apiGet.mockResolvedValue(
      statusPayload({ password_length: 3, env_override: true, env_override_target: 'LUYUN_POSTGRES_DSN' }),
    )
    const h = makeHarness()
    await h.loadDbCred()

    expect(h.dbEnvOverride.value).toBe(true)
    expect(h.dbResetDisabled.value).toBe(true)
    expect(h.dbResetDisabledReason.value).toContain('环境变量')
  })

  it('SQLite 后端：提示无需密码并禁用重置', async () => {
    apiGet.mockResolvedValue(
      statusPayload({ backend: 'sqlite', is_postgres: false, password_length: null, dsn: null, env_file: null }),
    )
    const h = makeHarness()
    await h.loadDbCred()

    expect(h.dbIsPostgres.value).toBe(false)
    expect(h.dbResetDisabled.value).toBe(true)
    expect(h.dbResetDisabledReason.value).toContain('SQLite')
  })
})

describe('useDbCredentials 重置流程', () => {
  it('确认后提交管理员密码，成功时记录结果并更新密码长度', async () => {
    apiGet.mockResolvedValue(statusPayload({ password_length: 3 }))
    apiPost.mockResolvedValue({
      ok: true,
      user: 'luyun',
      host: 'postgres',
      database: 'luyun',
      password_length: 32,
      env_file: '/srv/luyun/app/deploy/env.production',
      restart_triggered: true,
      restart_error: null,
    })
    const h = makeHarness()
    await h.loadDbCred()

    h.openDbReset()
    expect(h.dbResetConfirm.open).toBe(true)
    h.dbResetConfirm.password = 'admin-secret'
    await h.dbResetSubmit()

    expect(apiPost).toHaveBeenCalledWith('/api/admin/db-credentials/reset', {
      confirm_password: 'admin-secret',
    })
    expect(h.dbResetConfirm.open).toBe(false)
    expect(h.dbResetResult.show).toBe(true)
    expect(h.dbResetResult.envFile).toBe('/srv/luyun/app/deploy/env.production')
    expect(h.dbResetResult.passwordLength).toBe(32)
    expect(h.dbResetResult.restartTriggered).toBe(true)
    expect(h.dbResetResult.restartError).toBe('')
    // 面板上的密码长度直接采用响应里的权威值，不再回查（重启期间接口可能不可用）。
    expect(h.dbCred.value.password_length).toBe(32)
    expect(apiGet).toHaveBeenCalledTimes(1)
  })

  it('未输入管理员密码时不发请求', async () => {
    apiGet.mockResolvedValue(statusPayload())
    const h = makeHarness()
    await h.loadDbCred()

    h.openDbReset()
    await h.dbResetSubmit()

    expect(apiPost).not.toHaveBeenCalled()
    expect(h.dbResetConfirm.error).toBe('请输入当前后台管理员密码')
    expect(h.dbResetConfirm.open).toBe(true)
  })

  it('403 时原文展示后端 detail 并保留弹窗以便重试', async () => {
    apiGet.mockResolvedValue(statusPayload())
    const h = makeHarness()
    await h.loadDbCred()

    h.openDbReset()
    h.dbResetConfirm.password = 'wrong'
    apiPost.mockRejectedValue(Object.assign(new Error('后台密码不正确，已拒绝本次重置'), { status: 403 }))
    await h.dbResetSubmit()

    expect(h.dbResetConfirm.error).toBe('后台密码不正确，已拒绝本次重置')
    expect(h.dbResetConfirm.open).toBe(true)
    expect(h.dbResetResult.show).toBe(false)
    expect(h.dbResetting.value).toBe(false)
  })

  it('400 环境变量优先时展示后端原因', async () => {
    apiGet.mockResolvedValue(statusPayload())
    const h = makeHarness()
    await h.loadDbCred()

    h.openDbReset()
    h.dbResetConfirm.password = 'admin-secret'
    const http400 = new Error(
      '检测到环境变量 LUYUN_POSTGRES_DSN 被显式设置，优先级高于 deploy/env.production',
    )
    http400.status = 400 // 真实请求里 client.js 会带上状态码
    apiPost.mockRejectedValue(http400)
    await h.dbResetSubmit()

    expect(h.dbResetConfirm.error).toContain('优先级高于')
  })

  it('502 / 网络错误时提示应用可能正在重启，而不是贴反代 HTML', async () => {
    apiGet.mockResolvedValue(statusPayload())
    const h = makeHarness()
    await h.loadDbCred()

    h.openDbReset()
    h.dbResetConfirm.password = 'admin-secret'
    const gateway = new Error('<html><head><title>502 Bad Gateway</title></head>...</html>')
    gateway.status = 502
    apiPost.mockRejectedValue(gateway)
    await h.dbResetSubmit()

    expect(h.dbResetConfirm.error).toContain('可能正在重启')
    expect(h.dbResetConfirm.error).not.toContain('502 Bad Gateway')
  })

  it('restart_error 非空时记录原文供提示手动重启', async () => {
    apiGet.mockResolvedValue(statusPayload())
    apiPost.mockResolvedValue({
      ok: true,
      user: 'luyun',
      password_length: 32,
      env_file: '/srv/luyun/app/deploy/env.production',
      restart_triggered: true,
      restart_error: 'systemd 重启失败：Unit luyun.service not found',
    })
    const h = makeHarness()
    await h.loadDbCred()

    h.openDbReset()
    h.dbResetConfirm.password = 'admin-secret'
    await h.dbResetSubmit()

    expect(h.dbResetResult.restartError).toBe('systemd 重启失败：Unit luyun.service not found')
    expect(h.dbResetResult.restartTriggered).toBe(true)
  })
})
