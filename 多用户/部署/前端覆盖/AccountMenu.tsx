import { FormEvent, useEffect, useRef, useState } from 'react'
import type { VisualTheme } from '../theme'
import { multiuserRequestHeaders } from '../multiuserSession'
import FeedbackModal from './FeedbackModal'

type Account = {
  email: string
  display_name: string
  call_name: string
}

type Profile = {
  display_name: string
  call_name: string
  aliases: string[]
  company_purpose: string
  theme: string
}

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    credentials: 'same-origin',
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...multiuserRequestHeaders(),
      ...(init?.headers ?? {}),
    },
  })
  if (response.status === 401 || response.status === 403) {
    window.location.assign('/login')
    throw new Error('登录已经失效')
  }
  if (!response.ok) {
    let detail = '操作没有完成'
    try {
      const body = await response.json() as { detail?: unknown }
      if (typeof body.detail === 'string' && body.detail) detail = body.detail
    } catch {
      // 保留通用错误文案。
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export default function AccountMenu({
  theme,
  setTheme,
}: {
  theme: VisualTheme
  setTheme: (theme: VisualTheme) => void
}) {
  const root = useRef<HTMLDivElement>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    void api<Account>('/auth/me').then(setAccount).catch(() => undefined)
  }, [])

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setMenuOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false)
        setProfileOpen(false)
      }
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', escape)
    }
  }, [])

  const openProfile = async () => {
    setMenuOpen(false)
    setError('')
    try {
      const value = await api<Profile>('/auth/profile')
      if (value.theme !== 'deep-space' && value.theme !== 'mist') value.theme = theme
      setProfile(value)
      setProfileOpen(true)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '资料没有读取成功')
      setProfileOpen(true)
    }
  }

  const logout = async () => {
    setMenuOpen(false)
    try {
      await api<{ ok: boolean }>('/auth/logout', { method: 'POST', body: '{}' })
    } finally {
      window.location.assign('/login')
    }
  }

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!profile || saving) return
    setSaving(true)
    setError('')
    try {
      const saved = await api<Profile>('/auth/profile', {
        method: 'PATCH',
        body: JSON.stringify(profile),
      })
      if (saved.theme === 'deep-space' || saved.theme === 'mist') setTheme(saved.theme)
      window.location.reload()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '资料没有保存成功')
      setSaving(false)
    }
  }

  const initial = Array.from(account?.display_name || account?.call_name || '我')[0] || '我'

  return (
    <>
      <div className="multiuser-account" ref={root}>
        <button
          className="account-trigger"
          type="button"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((value) => !value)}
        >
          <span className="account-avatar" aria-hidden>{initial}</span>
          <span className="account-name">{account?.call_name || '我的账户'}</span>
          <span className="account-chevron" aria-hidden />
        </button>
        {menuOpen ? (
          <div className="account-dropdown" role="menu">
            <div className="account-summary">
              <strong>{account?.display_name || '我的账户'}</strong>
              <span>{account?.email || ''}</span>
            </div>
            <button type="button" role="menuitem" onClick={() => void openProfile()}>
              <span className="account-menu-icon profile-icon" aria-hidden />
              个人资料
            </button>
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); setFeedbackOpen(true) }}>
              <span className="account-menu-icon feedback-icon" aria-hidden />
              意见反馈
            </button>
            <div className="account-divider" />
            <button type="button" role="menuitem" onClick={() => void logout()}>
              <span className="account-menu-icon logout-icon" aria-hidden />
              退出登录
            </button>
          </div>
        ) : null}
      </div>

      {profileOpen ? (
        <div className="profile-overlay" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !saving) setProfileOpen(false)
        }}>
          <section className="profile-dialog" role="dialog" aria-modal="true" aria-labelledby="profile-title">
            <header>
              <div>
                <span>ACCOUNT</span>
                <h2 id="profile-title">个人资料</h2>
              </div>
              <button className="profile-close" type="button" aria-label="关闭" title="关闭" disabled={saving}
                onClick={() => setProfileOpen(false)}>×</button>
            </header>
            {profile ? (
              <form onSubmit={(event) => void save(event)}>
                <div className="profile-grid">
                  <label>
                    <span>姓名</span>
                    <input value={profile.display_name} maxLength={32} required
                      onChange={(event) => setProfile({ ...profile, display_name: event.target.value })} />
                  </label>
                  <label>
                    <span>公司如何称呼你</span>
                    <input value={profile.call_name} maxLength={32} required
                      onChange={(event) => setProfile({ ...profile, call_name: event.target.value })} />
                  </label>
                </div>
                <label>
                  <span>别名</span>
                  <input value={profile.aliases.join('、')} maxLength={128}
                    onChange={(event) => setProfile({
                      ...profile,
                      aliases: event.target.value.split(/[、,，]/).map((value) => value.trim()).filter(Boolean),
                    })} />
                </label>
                <label>
                  <span>公司目的</span>
                  <textarea value={profile.company_purpose} maxLength={200} required
                    onChange={(event) => setProfile({ ...profile, company_purpose: event.target.value })} />
                </label>
                <fieldset>
                  <legend>大厅主题</legend>
                  <div className="profile-theme-choice">
                    <button type="button" className={profile.theme === 'deep-space' ? 'on' : ''}
                      onClick={() => setProfile({ ...profile, theme: 'deep-space' })}>深空</button>
                    <button type="button" className={profile.theme === 'mist' ? 'on' : ''}
                      onClick={() => setProfile({ ...profile, theme: 'mist' })}>雾中醒来</button>
                  </div>
                </fieldset>
                {error ? <p className="profile-error" role="alert">{error}</p> : null}
                <footer>
                  <button type="button" disabled={saving} onClick={() => setProfileOpen(false)}>取消</button>
                  <button className="profile-save" type="submit" disabled={saving}>{saving ? '保存中…' : '保存'}</button>
                </footer>
              </form>
            ) : <p className="profile-error" role="alert">{error || '资料读取中…'}</p>}
          </section>
        </div>
      ) : null}
      <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
    </>
  )
}
