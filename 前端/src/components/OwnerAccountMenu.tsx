import { useEffect, useRef, useState } from 'react'

type AccessStatus = {
  ok: boolean
  状态: string
  网址: string
  操作中?: boolean
  说明?: string
  error?: string
  操作阶段?: string
  操作说明?: string
  操作开始时间?: number
  已用秒?: number
  操作步骤?: string[]
  操作步骤索引?: number
}

const ACCESS_OPEN_STAGES = ['准备检查', '容器服务', '用户公司', '网络连接', '公网通道', '外网验证']
const ACCESS_CLOSE_STAGES = ['准备关闭', '关闭公网通道', '确认已关闭']
const OWNER_DEVELOPMENT_ADMIN_URL = 'http://localhost:37660'

function formatElapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return minutes > 0 ? `${minutes}分${String(remainder).padStart(2, '0')}秒` : `${remainder}秒`
}

async function readAccessStatus(): Promise<AccessStatus> {
  const response = await fetch(`/对外访问?t=${Date.now()}`, { cache: 'no-store' })
  const value = await response.json() as AccessStatus
  if (!response.ok) throw new Error(value.error || '状态读取失败')
  return value
}

export default function OwnerAccountMenu() {
  const root = useRef<HTMLDivElement>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [managerOpen, setManagerOpen] = useState(false)
  const [managerView, setManagerView] = useState<'login' | 'management' | 'company'>('login')
  const [managerOnboarding, setManagerOnboarding] = useState(false)
  const [accessOpen, setAccessOpen] = useState(false)
  const [access, setAccess] = useState<AccessStatus | null>(null)
  const [accessBusy, setAccessBusy] = useState(false)
  const [accessError, setAccessError] = useState('')
  const [copied, setCopied] = useState(false)
  const [accessTick, setAccessTick] = useState(() => Date.now())
  const accessRefreshing = useRef(false)

  const refreshAccess = async () => {
    if (accessRefreshing.current) return
    accessRefreshing.current = true
    try {
      const value = await readAccessStatus()
      setAccess(value)
      setAccessError(value.error || '')
    } catch (error) {
      setAccessError(error instanceof Error ? error.message : '状态读取失败')
    } finally {
      accessRefreshing.current = false
    }
  }

  useEffect(() => {
    const closeMenu = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setMenuOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false)
        setManagerOpen(false)
        setAccessOpen(false)
      }
    }
    document.addEventListener('mousedown', closeMenu)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', closeMenu)
      document.removeEventListener('keydown', escape)
    }
  }, [])

  useEffect(() => {
    const receiveManagerMessage = (event: MessageEvent) => {
      if (event.origin !== OWNER_DEVELOPMENT_ADMIN_URL) return
      if (event.data?.type === 'xj-admin-view') {
        setManagerView(event.data.view === 'management' || event.data.view === 'company' ? event.data.view : 'login')
      }
      if (event.data?.type === 'xj-admin-onboarding') setManagerOnboarding(Boolean(event.data.open))
      if (event.data?.type === 'xj-admin-close') {
        setManagerOnboarding(false)
        setManagerOpen(false)
      }
    }
    window.addEventListener('message', receiveManagerMessage)
    return () => window.removeEventListener('message', receiveManagerMessage)
  }, [])

  useEffect(() => {
    void refreshAccess()
  }, [])

  useEffect(() => {
    if (menuOpen || accessOpen) void refreshAccess()
  }, [accessOpen, menuOpen])

  useEffect(() => {
    if (!accessOpen) return
    const busy = accessBusy || Boolean(access?.操作中)
    const id = window.setInterval(() => {
      void refreshAccess()
      if (busy) setAccessTick(Date.now())
    }, busy ? 1200 : 8000)
    return () => window.clearInterval(id)
  }, [accessOpen, accessBusy, access?.操作中])

  useEffect(() => {
    if (!accessOpen || !(accessBusy || access?.操作中)) return
    const id = window.setInterval(() => setAccessTick(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [accessOpen, accessBusy, access?.操作中])

  const changeAccess = async (turnOn: boolean) => {
    const startedAt = Date.now() / 1000
    const stages = turnOn ? ACCESS_OPEN_STAGES : ACCESS_CLOSE_STAGES
    setAccessBusy(true)
    setAccessError('')
    setAccess((current) => ({
      ok: current?.ok ?? false,
      状态: turnOn ? '正在开启' : '正在关闭',
      网址: current?.网址 ?? '',
      操作中: true,
      操作阶段: stages[0],
      操作说明: turnOn ? '正在向本机控制服务发起请求…' : '正在向本机控制服务发起请求…',
      操作开始时间: startedAt,
      已用秒: 0,
      操作步骤: stages,
      操作步骤索引: 0,
    }))
    try {
      const response = await fetch(turnOn ? '/对外访问_开启' : '/对外访问_关闭', {
        method: 'POST',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json', 'X-XJ-Owner-Control': '1' },
        body: '{}',
      })
      const value = await response.json() as AccessStatus
      setAccess(value)
      setAccessError(value.error || (value.状态 === '异常' ? value.说明 || '操作失败。' : ''))
    } catch (error) {
      const message = error instanceof Error ? error.message : '操作失败，请再试一次。'
      await refreshAccess()
      setAccessError(message)
    } finally {
      setAccessBusy(false)
    }
  }

  const accessTone = access?.操作中 || accessBusy
    ? 'busy'
    : access?.ok
      ? 'online'
      : access?.状态 === '异常'
        ? 'error'
        : 'offline'

  const accessOperation = access?.操作中 || accessBusy
  const operationStartedAt = access?.操作开始时间 || accessTick / 1000
  const operationElapsed = accessOperation
    ? Math.max(0, Math.floor(accessTick / 1000 - operationStartedAt))
    : 0
  const operationSteps = access?.操作步骤?.length
    ? access.操作步骤
    : access?.状态 === '正在关闭'
      ? ACCESS_CLOSE_STAGES
      : ACCESS_OPEN_STAGES
  const operationStepIndex = Math.min(
    Math.max(access?.操作步骤索引 ?? 0, 0),
    operationSteps.length - 1,
  )

  return (
    <>
      <div className="owner-account" ref={root}>
        <button
          className="owner-account-trigger"
          type="button"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((value) => !value)}
        >
          <span className="owner-account-avatar" aria-hidden>李</span>
          <span className="owner-account-name">示例主人</span>
          <span className="owner-account-chevron" aria-hidden />
        </button>
        {menuOpen ? (
          <div className="owner-account-dropdown" role="menu">
            <div className="owner-account-summary">
              <strong>示例主人</strong>
              <span>船主</span>
            </div>
            <button type="button" role="menuitem" onClick={() => {
              setMenuOpen(false)
              setManagerView('login')
              setManagerOnboarding(false)
              setManagerOpen(true)
            }}>
              用户管理
            </button>
            <button className="owner-access-menuitem" type="button" role="menuitem" onClick={() => {
              setMenuOpen(false)
              setAccessOpen(true)
            }}>
              <span>对外访问</span>
              <span className={`owner-access-menu-state ${accessTone}`}>
                <i aria-hidden />{access?.状态 || '检查中'}
              </span>
            </button>
          </div>
        ) : null}
      </div>

      {managerOpen ? (
        <div className="owner-manager-overlay" role="presentation">
          <section
            className={`owner-manager-panel ${managerView === 'login' ? 'is-login' : 'is-management'} ${managerOnboarding ? 'has-onboarding' : ''}`}
            role="dialog"
            aria-modal="true"
            aria-label="用户管理"
          >
            {managerView !== 'login' && !managerOnboarding ? (
              <button
                className="owner-manager-close"
                type="button"
                aria-label="关闭用户管理"
                title="关闭"
                onClick={() => {
                  setManagerOnboarding(false)
                  setManagerOpen(false)
                }}
              >×</button>
            ) : null}
            <iframe src={`${OWNER_DEVELOPMENT_ADMIN_URL}/?embedded=1`} title="用户管理" allowTransparency />
          </section>
        </div>
      ) : null}


      {accessOpen ? (
        <div className="owner-manager-overlay" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setAccessOpen(false)
        }}>
          <section className="owner-access-panel" role="dialog" aria-modal="true" aria-labelledby="owner-access-title">
            <header>
              <div>
                <h2 id="owner-access-title">对外访问</h2>
                <span className={`owner-access-heading-state ${accessTone}`}><i aria-hidden />{access?.状态 || '检查中'}</span>
              </div>
              <button type="button" aria-label="关闭" title="关闭" onClick={() => setAccessOpen(false)}>×</button>
            </header>

            <div className="owner-access-content">
              {accessOperation ? (
                <div className="owner-access-progress" aria-live="polite">
                  <div className="owner-access-progress-head">
                    <span className="owner-access-progress-stage"><i aria-hidden />{access?.操作阶段 || access?.状态 || '正在处理'}</span>
                    <span className="owner-access-progress-clock">已用 {formatElapsed(operationElapsed)}</span>
                  </div>
                  <div className="owner-access-progress-steps">
                    {operationSteps.map((step, index) => (
                      <span
                        className={`owner-access-progress-step ${index < operationStepIndex ? 'done' : ''} ${index === operationStepIndex ? 'active' : ''}`}
                        key={step}
                      >
                        <i aria-hidden />{step}
                      </span>
                    ))}
                  </div>
                  <p>{access?.操作说明 || access?.说明 || '后台仍在工作，完成后会自动更新。'}</p>
                </div>
              ) : (
                <p className="owner-access-message">{accessError || access?.说明 || '正在检查当前状态…'}</p>
              )}
              {access?.网址 ? (
                <div className="owner-access-url">
                  <a href={access.网址} target="_blank" rel="noreferrer">{access.网址}</a>
                  <button type="button" onClick={async () => {
                    await navigator.clipboard.writeText(access.网址)
                    setCopied(true)
                    window.setTimeout(() => setCopied(false), 1600)
                  }}>{copied ? '已复制' : '复制网址'}</button>
                </div>
              ) : null}
              <button
                className={`owner-access-action ${access?.ok ? 'stop' : 'start'}`}
                type="button"
                disabled={accessBusy || access?.操作中}
                onClick={() => void changeAccess(!access?.ok)}
              >
                {accessBusy || access?.操作中
                  ? access?.状态 || '处理中'
                  : access?.ok
                    ? '关闭对外访问'
                    : '开启对外访问'}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </>
  )
}
