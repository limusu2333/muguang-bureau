import type { Hall, HallEvent } from '../api'
import type { View } from '../App'
import { renderLightText } from './文本渲染'

function dateOf(t?: string): string {
  const value = (t || '').slice(0, 10)
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '更早'
}

function timeOf(t?: string): string {
  if (!t) return ''
  const match = t.match(/(\d{1,2}):(\d{2})/)
  return match ? `${match[1].padStart(2, '0')}:${match[2]}` : ''
}

function groupByDay(events: HallEvent[]) {
  const map = new Map<string, HallEvent[]>()
  for (const event of events) {
    const day = dateOf(event.t)
    map.set(day, [...(map.get(day) ?? []), event])
  }
  return Array.from(map.entries())
    .map(([date, list]) => ({ date, list }))
    .sort((a, b) => b.date.localeCompare(a.date))
}

function EventLine({ event, setView }: { event: HallEvent; setView: (v: View) => void }) {
  const hm = timeOf(event.t)
  const openRoom = event.room
    ? () => setView({ t: 'room', id: event.room!, focus: event.event })
    : undefined
  const main = event.k === '话'
    ? `${(event.who || '').includes('船主') ? '你' : event.who || '公司'}${hm ? ` · ${hm}` : ''}`
    : `${event.k}${hm ? ` · ${hm}` : ''}`
  const detail = event.text || event.副 || ''

  return (
    <div className={'event-line ' + (openRoom ? 'clickable' : '')} onClick={openRoom}>
      <div className="line-main">{main}</div>
      {detail && <div className="line-detail">{renderLightText(detail)}</div>}
      {event.副 && event.text && <div className="line-meta">{event.副}</div>}
    </div>
  )
}

export default function ScenesView({
  hall,
  dayEvents,
  view,
  setView,
}: {
  hall: Hall | null
  dayEvents: HallEvent[]
  view: View
  setView: (v: View) => void
}) {
  const selectedDate = view.t === 'day' ? view.date : undefined
  const current = groupByDay(hall?.事件 ?? [])
  const lines = dayEvents.length
    ? dayEvents
    : current.find((item) => item.date === selectedDate)?.list ?? []
  const scene = hall?.历史场次?.find((item) => item.日期 === selectedDate)

  return (
    <main className="stage page-stage scene-day-stage">
      <div className="stage-pad holo-page scene-day-page" key={selectedDate || 'day'}>
        <button className="scene-back" type="button" onClick={() => setView({ t: 'hall' })}>‹ 返回现在</button>
        <div className="scene-day-head">
          <div className="page-k">SCENE · 场次回看</div>
          <h1>{selectedDate || '历史场次'}</h1>
          <p>{scene?.摘要 || '这一天的大厅原始记录。'}</p>
          <small>{scene?.记录数 ?? lines.length} 条真实记录{scene?.已整理 ? ' · 已完成摘要' : ' · 临时概括'}</small>
        </div>
        <div className="event-ledger scene-events">
          {lines.map((event, index) => <EventLine key={index} event={event} setView={setView} />)}
          {!lines.length && <div className="scene-empty">这一天没有可读记录。</div>}
        </div>
      </div>
    </main>
  )
}
