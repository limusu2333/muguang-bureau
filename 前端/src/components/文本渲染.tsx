import { Fragment } from 'react'
import type { ReactNode } from 'react'

export function renderLightText(text: string): ReactNode {
  if (!text) return null
  const paragraphs = text.split(/\n{2,}/)
  return paragraphs.map((paragraph, pi) => {
    const lines = paragraph.split('\n')
    const out: ReactNode[] = []
    let i = 0
    let seg = 0
    while (i < lines.length) {
      // markdown 表格块：连续 | 开头的行 → 排版成真正的小表格（gpt们的公文病，渲染端消化掉）
      if (lines[i].trim().startsWith('|') && i + 1 < lines.length && lines[i + 1].trim().startsWith('|')) {
        const 块: string[] = []
        while (i < lines.length && lines[i].trim().startsWith('|')) {
          块.push(lines[i].trim())
          i += 1
        }
        out.push(<MsgTable key={`${pi}-t-${seg++}`} rows={块} />)
        continue
      }
      const line = lines[i]
      out.push(
        <Fragment key={`${pi}-${i}`}>
          {out.length > 0 && <br />}
          {renderLightLine(line, `${pi}-${i}`)}
        </Fragment>,
      )
      i += 1
    }
    return (
      <div className="dialogue-p" key={pi}>
        {out}
      </div>
    )
  })
}

function MsgTable({ rows }: { rows: string[] }) {
  const 拆 = (r: string) => r.replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
  const 是分隔 = (r: string) => /^\|[\s:|-]+\|?$/.test(r)
  const 体 = rows.filter((r) => !是分隔(r)).map(拆)
  if (体.length === 0) return null
  const [头, ...行们] = 体
  return (
    <table className="msg-table">
      <thead>
        <tr>{头.map((h, i) => <th key={i}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {行们.map((r, i) => (
          <tr key={i}>{r.map((c, j) => <td key={j}>{renderInline(c, `mt-${i}-${j}`)}</td>)}</tr>
        ))}
      </tbody>
    </table>
  )
}

function renderLightLine(line: string, key: string): ReactNode {
  const t = line.trim()
  // ### 标题 → 小节题；--- 水平线 → 细分隔（不再裸奔井号和横杠）
  const h = t.match(/^#{1,4}\s*(.+)$/)
  if (h) return <span className="msg-h">{renderInline(h[1], key)}</span>
  if (/^[-*_]{3,}$/.test(t)) return <span className="msg-hr" aria-hidden />
  const bullet = line.startsWith('- ') || line.startsWith('• ')
  if (!bullet) return renderInline(line, key)
  return (
    <span className="dialogue-bullet">
      <span className="dialogue-bullet-dot" aria-hidden>•</span>
      <span>{renderInline(line.slice(2), key)}</span>
    </span>
  )
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = []
  let buf = ''
  let i = 0
  const flush = () => {
    if (!buf) return
    out.push(buf)
    buf = ''
  }
  while (i < text.length) {
    if (text[i] === '`') {
      const end = text.indexOf('`', i + 1)
      if (end !== -1) {
        flush()
        out.push(<code key={`${keyPrefix}-c-${i}`}>{text.slice(i + 1, end)}</code>)
        i = end + 1
        continue
      }
    }
    if (text.startsWith('**', i)) {
      const end = text.indexOf('**', i + 2)
      if (end !== -1) {
        flush()
        out.push(<b key={`${keyPrefix}-b-${i}`}>{renderInline(text.slice(i + 2, end), `${keyPrefix}-b-${i}`)}</b>)
        i = end + 2
        continue
      }
    }
    buf += text[i]
    i += 1
  }
  flush()
  return out
}
