import { useEffect, useRef, useState } from 'react'
import { instanceIdentity } from '../multiuserIdentity'

type Announcement = {
  announcement_id: number
  title: string
  body: string
  body_rich?: string
  published_at: string
}

function publishedText(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return ''
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
}

export default function AnnouncementModal({ ready }: { ready: boolean }) {
  const requested = useRef(false)
  const [announcements, setAnnouncements] = useState<Announcement[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!ready || requested.current || !instanceIdentity.accountId) return
    requested.current = true
    let active = true
    void fetch('/auth/announcements', { credentials: 'same-origin', cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) throw new Error('公告读取失败')
        return response.json() as Promise<{ announcements?: Announcement[] }>
      })
      .then((result) => {
        if (!active) return
        const rows = Array.isArray(result.announcements) ? result.announcements : []
        if (rows.length) {
          setAnnouncements(rows)
          setOpen(true)
        }
      })
      .catch(() => {
        requested.current = false
      })
    return () => { active = false }
  }, [ready])

  if (!open || announcements.length === 0) return null
  const close = () => setOpen(false)

  return (
    <div className="announcement-overlay" role="presentation">
      <section className="announcement-dialog" role="dialog" aria-modal="true" aria-labelledby="announcement-title">
        <div className="announcement-frame">
          <header>
            <div><span>开发公司</span><strong id="announcement-title">公告栏</strong></div>
            <small>共 {announcements.length} 条</small>
            <button type="button" onClick={close} aria-label="关闭" title="关闭">×</button>
          </header>
          <main className="announcement-content">
            {announcements.map((announcement, index) => (
              <article className="announcement-entry" key={announcement.announcement_id}>
                <div className="announcement-mark" aria-hidden><i /><i /><i /></div>
                <p>{publishedText(announcement.published_at)}{index === 0 ? <em>最新</em> : null}</p>
                <h2>{announcement.title}</h2>
                {announcement.body_rich ? (
                  <div className="announcement-body announcement-rich-body"
                    dangerouslySetInnerHTML={{ __html: announcement.body_rich }} />
                ) : (
                  <div className="announcement-body">{announcement.body}</div>
                )}
              </article>
            ))}
          </main>
          <footer>
            <span>最新公告在最上方</span>
            <button className="announcement-acknowledge" type="button" onClick={close}>关闭公告</button>
          </footer>
        </div>
      </section>
    </div>
  )
}
