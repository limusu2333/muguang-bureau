import { useMemo, type ReactNode } from 'react'
import type { Board, Hall, Member, SceneSummary } from '../api'
import type { View } from '../App'

function memberName(member: Member) {
  return member.名字 || member.title || member.name || '未命名'
}

function sceneFallback(hall: Hall | null): SceneSummary[] {
  return (hall?.历史日期 ?? []).map((日期) => ({ 日期, 摘要: '历史场次', 记录数: 0 }))
}

function shortDate(value: string) {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  return match ? `${match[2]}.${match[3]}` : value
}

function progress(member: Member) {
  const current = Number(member.晋升分 ?? 0)
  const target = Number(member.晋升达标 ?? 0)
  if (!target) return 100
  return Math.max(0, Math.min(100, (current / target) * 100))
}

export default function HallWorkspace({
  hall,
  board,
  view,
  setView,
  children,
}: {
  hall: Hall | null
  board: Board | null
  view: View
  setView: (view: View) => void
  children: ReactNode
}) {
  const scenes = useMemo(
    () => (hall?.历史场次?.length ? hall.历史场次 : sceneFallback(hall)).slice().reverse(),
    [hall],
  )
  const members = board?.成员 ?? []

  return (
    <div className="hall-workspace">
      <aside className="hall-side hall-scenes-rail workspace-reveal" aria-label="历史场次">
        <div className="side-heading">
          <span className="side-index">01</span>
          <div>
            <strong>场次</strong>
            <small>发生过什么</small>
          </div>
        </div>
        <button
          type="button"
          className={'scene-now' + (view.t === 'hall' ? ' is-active' : '')}
          onClick={() => setView({ t: 'hall' })}
        >
          <span className="scene-now-dot" />
          <span>现在</span>
          <small>回到大厅</small>
        </button>
        <div className="scene-rail-list">
          {scenes.map((scene) => {
            const selected = view.t === 'day' && view.date === scene.日期
            return (
              <button
                type="button"
                className={'scene-rail-item workspace-reveal' + (selected ? ' is-active' : '')}
                key={scene.日期}
                onClick={() => setView({ t: 'day', date: scene.日期 })}
                aria-current={selected ? 'page' : undefined}
              >
                <span className="scene-rail-date">{shortDate(scene.日期)}</span>
                <span className="scene-rail-summary">{scene.摘要}</span>
                <span className="scene-rail-meta">
                  {scene.记录数 ? `${scene.记录数} 条` : '归档'}
                  {!scene.已整理 && <i>临时概括</i>}
                </span>
              </button>
            )
          })}
        </div>
      </aside>

      <div className="hall-workspace-center">{children}</div>

      <aside className="hall-side hall-crew-rail workspace-reveal" aria-label="班子实时状态">
        <div className="side-heading">
          <span className="side-index">02</span>
          <div>
            <strong>班子</strong>
            <small>每 2.5 秒更新</small>
          </div>
        </div>
        <div className="crew-rail-list">
          {members.map((member) => {
            const name = memberName(member)
            const target = Number(member.晋升达标 ?? 0)
            const score = Number(member.晋升分 ?? 0)
            return (
              <button
                type="button"
                className="crew-rail-item workspace-reveal"
                key={member.name || name}
                onClick={() => setView({ t: 'crew', focus: name })}
              >
                <span className="crew-rail-topline">
                  <strong>{name}</strong>
                  <i className={'trust-mark trust-' + (member.信任等级 || '中')}>{member.信任等级 || '中'}</i>
                </span>
                <span className="crew-rail-role">{member.职称 || member.title || member.name}</span>
                <span className="crew-score-line">
                  <span>信誉 <b>{Number(member.信誉分 ?? 0).toFixed(1)}</b></span>
                  <span>
                    {member.是经理
                      ? '现任经理'
                      : member.晋升试用
                        ? '晋升试用中'
                        : `晋升 ${Math.round(score)}/${target || '—'}`}
                  </span>
                </span>
                <span className="crew-progress" aria-hidden>
                  <span className="crew-progress-fill" style={{ width: `${progress(member)}%` }} />
                </span>
              </button>
            )
          })}
        </div>
        <button type="button" className="crew-detail-link" onClick={() => setView({ t: 'crew' })}>
          查看完整班子档案 <span>›</span>
        </button>
      </aside>
    </div>
  )
}
