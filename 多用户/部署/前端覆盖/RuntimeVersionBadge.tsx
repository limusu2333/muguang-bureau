import { useEffect, useState } from 'react'

type RuntimeIdentity = {
  display_name?: unknown
  version_name?: unknown
}

export default function RuntimeVersionBadge() {
  const [versionName, setVersionName] = useState('版本读取中')

  useEffect(() => {
    let active = true
    void fetch('/auth/runtime-context', { credentials: 'same-origin', cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) throw new Error('运行身份读取失败')
        return response.json() as Promise<RuntimeIdentity>
      })
      .then((identity) => {
        const displayName = typeof identity.display_name === 'string' ? identity.display_name.trim() : ''
        const versionName = typeof identity.version_name === 'string' ? identity.version_name.trim() : ''
        const value = displayName || versionName
        if (active) setVersionName(value || '版本无法确认')
      })
      .catch(() => {
        if (active) setVersionName('版本无法确认')
      })
    return () => { active = false }
  }, [])

  return <span className="runtime-version-badge" aria-label={`当前运行版本：${versionName}`}>{versionName}</span>
}
