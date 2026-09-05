import { ClipboardEvent, FormEvent, useEffect, useRef, useState } from 'react'
import { multiuserRequestHeaders } from '../multiuserSession'

type FeedbackKind = '发现 Bug' | '不好用' | '功能建议' | '其他'

const kinds: FeedbackKind[] = ['发现 Bug', '不好用', '功能建议', '其他']
const allowedImages = new Set(['image/png', 'image/jpeg', 'image/webp'])
const maximumImageBytes = 5 * 1024 * 1024

async function imageData(file: File): Promise<string> {
  if (!allowedImages.has(file.type)) throw new Error('截图只支持 PNG、JPEG 或 WebP')
  if (file.size > maximumImageBytes) throw new Error('截图不能超过 5MB')
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('截图没有读取成功'))
    reader.onerror = () => reject(new Error('截图没有读取成功'))
    reader.readAsDataURL(file)
  })
}

async function submitFeedback(payload: object): Promise<void> {
  const response = await fetch('/auth/feedback', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...multiuserRequestHeaders() },
    body: JSON.stringify(payload),
  })
  if (response.status === 401 || response.status === 403) {
    window.location.assign('/login')
    throw new Error('登录已经失效')
  }
  if (!response.ok) {
    const result = await response.json().catch(() => ({})) as { detail?: unknown }
    throw new Error(typeof result.detail === 'string' ? result.detail : '反馈没有提交成功')
  }
}

export default function FeedbackModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const fileInput = useRef<HTMLInputElement>(null)
  const [kind, setKind] = useState<FeedbackKind>('发现 Bug')
  const [body, setBody] = useState('')
  const [screenshot, setScreenshot] = useState('')
  const [screenshotName, setScreenshotName] = useState('')
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  useEffect(() => {
    if (!open) return
    setKind('发现 Bug')
    setBody('')
    setScreenshot('')
    setScreenshotName('')
    setError('')
    setSending(false)
    setSubmitted(false)
  }, [open])

  useEffect(() => {
    if (!open) return
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !sending) onClose()
    }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [onClose, open, sending])

  const attach = async (file?: File) => {
    if (!file) return
    setError('')
    try {
      setScreenshot(await imageData(file))
      setScreenshotName(file.name || '粘贴的截图')
    } catch (reason) {
      setScreenshot('')
      setScreenshotName('')
      setError(reason instanceof Error ? reason.message : '截图没有读取成功')
    }
  }

  const pasteImage = (event: ClipboardEvent<HTMLElement>) => {
    const file = Array.from(event.clipboardData.items)
      .find((item) => item.kind === 'file' && item.type.startsWith('image/'))
      ?.getAsFile()
    if (file) {
      event.preventDefault()
      void attach(file)
    }
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (sending || !body.trim()) return
    setSending(true)
    setError('')
    try {
      await submitFeedback({
        kind,
        body: body.trim(),
        page: window.location.pathname,
        screenshot,
      })
      setSubmitted(true)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '反馈没有提交成功')
    } finally {
      setSending(false)
    }
  }

  if (!open) return null

  return (
    <div className="feedback-overlay" role="presentation" onPaste={pasteImage} onMouseDown={(event) => {
      if (event.target === event.currentTarget && !sending) onClose()
    }}>
      <section className="feedback-dialog" role="dialog" aria-modal="true" aria-labelledby="feedback-title">
        <header>
          <div><span>FEEDBACK</span><h2 id="feedback-title">意见反馈</h2></div>
          <button className="feedback-close" type="button" aria-label="关闭" title="关闭" disabled={sending} onClick={onClose}>×</button>
        </header>
        {submitted ? (
          <div className="feedback-success">
            <span aria-hidden>✓</span>
            <h3>已经收到</h3>
            <p>这条反馈已进入管理中心。</p>
            <button type="button" onClick={onClose}>完成</button>
          </div>
        ) : (
          <form onSubmit={(event) => void submit(event)}>
            <fieldset>
              <legend>反馈类型</legend>
              <div className="feedback-kind">
                {kinds.map((item) => <button key={item} type="button" className={kind === item ? 'on' : ''} onClick={() => setKind(item)}>{item}</button>)}
              </div>
            </fieldset>
            <label className="feedback-copy">
              <span>具体情况</span>
              <textarea autoFocus value={body} maxLength={5000} required placeholder="发生了什么，或者哪里不好用？"
                onChange={(event) => setBody(event.target.value)} />
              <small>{body.length} / 5000</small>
            </label>
            <div className="feedback-attachment">
              <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(event) => void attach(event.target.files?.[0])} />
              {screenshot ? (
                <div><span className="feedback-thumbnail"><img src={screenshot} alt="待提交截图" /></span><strong>{screenshotName}</strong><button type="button" aria-label="移除截图" title="移除截图" onClick={() => { setScreenshot(''); setScreenshotName('') }}>×</button></div>
              ) : <button type="button" onClick={() => fileInput.current?.click()}><span aria-hidden>+</span>添加截图</button>}
            </div>
            {error ? <p className="feedback-error" role="alert">{error}</p> : null}
            <footer>
              <button type="button" disabled={sending} onClick={onClose}>取消</button>
              <button className="feedback-submit" type="submit" disabled={sending || !body.trim()}>{sending ? '提交中…' : '提交反馈'}</button>
            </footer>
          </form>
        )}
      </section>
    </div>
  )
}
