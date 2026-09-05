// 项目室「一个任务的一生」：立项需求 → 项目会 → 旧工单历史 → 会议协同 → 泳道(过程札记/正式报告/观察/日志/裁决) → 请示决策
// 顶部「项目对话 · 绑定本历史项目」绑定项目记忆。复刻 页面.js 渲染房间。
import { useCallback, useEffect, useRef, useState } from 'react'
import { getJSON } from '../api'
import type { RoomDetail, 会议协同, 室请示, 泳道 } from '../api'
import type { View } from '../App'
import { 会议发言, 决策 } from '../actions'
import { EmptyRow } from './ui'
import { renderLightText } from './文本渲染'

function 态(l: 泳道): { cls: string; txt: string; dot: string } {
  if (l.列 === '进行中') {
    if (l.请示中) return { cls: 'ask', txt: '请示中', dot: 'live' }
    if ((l.控制状态 || '').startsWith('暂停')) return { cls: '', txt: '暂停', dot: 'gray' }
    return { cls: 'run', txt: '运行中', dot: 'live' }
  }
  const m: Record<string, { cls: string; txt: string; dot: string }> = {
    待确认: { cls: '', txt: '旧待确认', dot: 'gray' },
    待办: { cls: '', txt: '旧回炉', dot: 'gray' },
    待验收: { cls: '', txt: '旧待验收', dot: 'gray' },
    已完成: { cls: 'done', txt: '已完成', dot: 'green' },
    已作废: { cls: '', txt: '已作废', dot: 'gray' },
  }
  return m[l.列 || ''] || { cls: '', txt: l.列 || '', dot: 'gray' }
}

export default function Room({
  view,
  setView,
  refresh,
  continueInHall,
}: {
  view: View
  setView: (v: View) => void
  refresh: () => void
  continueInHall: (id: string, title: string, text: string) => void
}) {
  const [d, setD] = useState<RoomDetail | null>(null)
  const id = view.id || ''
  const ref = useRef<HTMLDivElement>(null)

  const 拉 = useCallback(async () => {
    if (!id) return
    try {
      setD(await getJSON<RoomDetail>(`/room?id=${encodeURIComponent(id)}`))
    } catch {
      /* 静默重试 */
    }
  }, [id])

  useEffect(() => {
    setD(null)
    拉()
    const t = setInterval(拉, 2500)
    return () => clearInterval(t)
  }, [拉])

  const reload = useCallback(async () => {
    await 拉()
    refresh()
  }, [拉, refresh])

  // focus 定位：滚到对应事件并高亮
  useEffect(() => {
    if (!view.focus || !d) return
    const el = ref.current?.querySelector(`[data-event="${CSS.escape(view.focus)}"]`)
    if (!el) return
    el.classList.add('focus-hit')
    el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    const t = setTimeout(() => el.classList.remove('focus-hit'), 2600)
    return () => clearTimeout(t)
  }, [view.focus, d])

  if (!d || d.id !== id) {
    return (
      <main className="stage">
        <div className="stage-pad">
          <div className="empty" style={{ marginTop: '14vh' }}>
            正在打开项目室…
          </div>
        </div>
      </main>
    )
  }

  const hasMeetingPlan = Boolean(
    d.项目会 || d.覆盖说明 || (d.泳道 || []).some((l) => l.过程?.会议依据),
  )

  return (
    <main className="stage" ref={ref}>
      <div className="stage-pad">
        <div className="hall-head">
          <h1>{d.标题}</h1>
          <button className="ghost sm" onClick={() => setView({ t: 'hall' })}>
            ← 回大厅
          </button>
        </div>
        <div className="meta">
          立项 {d.时间 || ''} · {d.id}
        </div>

        <RoomTalk d={d} continueInHall={continueInHall} />

        {d.需求 && (
          <div className="card">
            <div className="lbl">立项 · 船主需求</div>
            <div className="quote">{d.需求}</div>
          </div>
        )}

        {d.项目会 && (
          <details className="card meeting-minutes" open>
            <summary className="lbl">项目会纪要 · 方案从这里来</summary>
            <pre>{d.项目会}</pre>
          </details>
        )}

        {(d.覆盖说明 || (d.泳道 || []).length > 0) && (
          <div className="card">
            <div className="lbl">{hasMeetingPlan ? '历史会议结论 · 旧工单' : '历史旧工单'}</div>
            {d.覆盖说明 && (
              <div className="muted" style={{ lineHeight: 1.65, marginBottom: 10 }}>
                {renderLightText(d.覆盖说明)}
              </div>
            )}
            {(d.泳道 || []).map((l) => (
              <div className="claim" key={l.name}>
                <div className="role">
                  {l.模型?.title || l.角色}
                  <small>{l.模型?.model || ''}</small>
                </div>
                <div className="desc">
                  {l.过程?.会议依据 && <small>会议依据：{l.过程.会议依据}</small>}
                  {(l.任务 || '').slice(0, 160)}
                </div>
              </div>
            ))}
          </div>
        )}

        {(d.会议协同 || []).length > 0 && (
          <>
            <div className="lbl" style={{ margin: '24px 0 12px' }}>
              项目会与协同 · 可旁听/参会
            </div>
            {(d.会议协同 || []).map((m) => (
              <MeetCard key={m.id} m={m} reload={reload} />
            ))}
          </>
        )}

        <div className="lbl" style={{ margin: '24px 0 12px' }}>
          泳道 · 各自干各自的
        </div>
        {(d.泳道 || []).map((l) => (
          <Lane key={l.name} l={l} reload={reload} />
        ))}

        {(d.请示 || []).length > 0 && (
          <>
            <div className="lbl" style={{ margin: '24px 0 12px' }}>
              请示与决策 · 谁卡住了、经理怎么判、你怎么裁
            </div>
            {(d.请示 || []).map((c) => (
              <AskCard key={c.id} c={c} reload={reload} />
            ))}
          </>
        )}
      </div>
    </main>
  )
}

function RoomTalk({ d, continueInHall }: { d: RoomDetail; continueInHall: (id: string, title: string, text: string) => void }) {
  const [text, setText] = useState('')
  const mem = d.项目记忆 || []
  const send = () => {
    const v = text.trim()
    if (!v) return
    setText('')
    continueInHall(d.id, d.标题 || d.id, v)
  }
  return (
    <div className="room-talk card">
      <div className="lbl">项目对话 · 绑定本历史项目</div>
      <div className="room-chat-log">
        {mem.length === 0 && (
          <div className="empty" style={{ padding: '8px 0', textAlign: 'left' }}>
            本项目还没有单独对话。这里说的话会进入本项目记忆，相关岗位下次会看到。
          </div>
        )}
        {mem.map((m, i) => {
          const mine = (m.who || '').startsWith('船主')
          return (
            <div className={'room-msg ' + (mine ? 'me' : 'them')} key={i}>
              <div className="nm">{(mine ? '你' : m.who || '') + (m.时间 ? ' · ' + m.时间 : '')}</div>
              <div>{renderLightText(m.text || '')}</div>
            </div>
          )
        })}
      </div>
      <div className="room-say">
        <textarea
          rows={1}
          value={text}
          placeholder="对这个项目说话… @岗位 指名；内容会绑定到本历史项目"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              send()
            }
          }}
        />
        <button className="prime sm" onClick={send}>
          发送
        </button>
      </div>
    </div>
  )
}

function MeetCard({ m, reload }: { m: 会议协同; reload: () => void }) {
  const [text, setText] = useState('')
  const send = () => {
    const v = text.trim()
    if (!v) return
    setText('')
    会议发言(m.id, v, reload)
  }
  return (
    <div className="meet-card">
      <div className="meet-head">
        <div>
          <b>{m.标题 || m.类型 || '会议'}</b>
          <span>
            {m.类型 || ''} · 主持 {m.主席 || ''}
          </span>
        </div>
        <em>{m.状态 || ''}</em>
      </div>
      {(m.参会 || []).length > 0 && <div className="meet-people">参会：{(m.参会 || []).join('、')}</div>}
      <div className="meet-events">
        {(m.事件 || []).map((ev, i) => {
          const mine = ev.发言人 === '船主' ? ' owner' : ev.发言人 === '项目经理' ? ' manager' : ''
          return (
            <div className={'meet-ev' + mine} data-event={ev.id} key={ev.id || i}>
              <div className="meet-meta">
                <b>{ev.类型 || ''}</b>
                <span>
                  {ev.发言人 || ''}
                  {ev.目标 ? ` → ${ev.目标}` : ''}
                  {ev.关联工单 ? ` · ${ev.关联工单}` : ''}
                </span>
                <small>{ev.短时 || ''}</small>
                {ev.需回执 && <span className="need-receipt">需回执</span>}
              </div>
              <div className="meet-body">{renderLightText(ev.内容 || '')}</div>
            </div>
          )
        })}
      </div>
      <div className="meet-say">
        <textarea
          rows={1}
          value={text}
          placeholder="以船主身份参会发言…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              send()
            }
          }}
        />
        <button className="prime sm" onClick={send}>
          发言
        </button>
      </div>
    </div>
  )
}

function Lane({ l, reload }: { l: 泳道; reload: () => void }) {
  const st = 态(l)
  const p = l.过程 || {}
  const 空过程 = !p.任务理解 && !p.执行要求 && !p.白名单
  return (
    <div className="lane">
      <div className="head">
        <div className={'dot ' + st.dot} />
        <div className="role">
          {l.模型?.title || l.角色} <small>{l.模型?.model || ''}</small>
        </div>
        <span className={'pill ' + st.cls}>{st.txt}</span>
        <span className="steps">
          {l.步数}/{l.预算 || '?'} 步
        </span>
      </div>

      <div className="process">
        <div className="pt">过程札记</div>
        {p.任务理解 && (
          <PItem b="理解" text={p.任务理解.slice(0, 420)} />
        )}
        {p.执行要求 && <PItem b="验收" text={p.执行要求.slice(0, 360)} />}
        {p.白名单 && <PItem b="边界" text={p.白名单} />}
        {p.会议依据 && <PItem b="依据" text={p.会议依据} />}
        {(p.预算依据 || p.预计字数 || p.预计token) && (
          <PItem
            b="预算"
            text={[
              p.预算依据,
              p.预计字数 ? '预计字数：' + p.预计字数 : '',
              p.预计token ? '预计token：' + p.预计token : '',
            ]
              .filter(Boolean)
              .join('\n')}
          />
        )}
        {!!p.产物?.length && (
          <PItem b="产物" text={p.产物.map((x) => `${x.路径}：${x.状态}，${x.说明 || ''}`).join('\n')} />
        )}

        {!!p.正式报告?.length && (
          <>
            <div className="pt" style={{ marginTop: 8 }}>
              正式报告
            </div>
            {p.正式报告.map((r, i) => (
              <div className="report-card" key={i}>
                <div className="report-head">
                  <b>{r.类别 || '报告'}</b>
                  <span>{(r.时间 || '').replace('T', ' ')}</span>
                </div>
                <ReportRow b="阶段" text={r.阶段} />
                <ReportRow b="原因" text={r.原因} />
                <ReportRow b="判断" text={r.判断} />
                <ReportRow b="下一步" text={r.下一步} />
              </div>
            ))}
          </>
        )}

        {p.事件?.length ? (
          <>
            <div className="pt" style={{ marginTop: 8 }}>
              过程事件
            </div>
            {p.事件.map((ev, i) => (
              <div className="pitem event-item" data-event={ev.id} key={ev.id || i}>
                <b>{ev.类别}</b>
                <span>
                  <strong>{ev.标题}</strong>
                  {ev.说明 && (
                    <>
                      <br />
                      {renderLightText(ev.说明)}
                    </>
                  )}
                  <small>{ev.短时 || ''}</small>
                </span>
              </div>
            ))}
          </>
        ) : p.步骤?.length ? (
          <PItem b="正在做" text={p.步骤.map((x) => '· ' + x).join('\n')} />
        ) : null}

        {空过程 && <PItem b="记录" text="这条泳道还没有写出可追溯过程。" />}
      </div>

      {l.列 === '进行中' && <LaneActs />}
      {l.列 === '待验收' && <VerdictActs />}

      {!!p.观察?.length && (
        <div className="raw-log obs-list">
          <div className="raw-title">观察摘要与原文（员工下一步会看到）</div>
          {p.观察.map((o, idx) => (
            <details className="obs-summary" key={idx}>
              <summary>{o.摘要 || `${o.动作}：${o.目标}`}</summary>
              <pre>{o.原文 || ''}</pre>
            </details>
          ))}
        </div>
      )}

      {l.日志 && (
        <details className="raw-log">
          <summary>完整过程日志</summary>
          <pre data-log={l.name}>{l.日志.trim()}</pre>
        </details>
      )}

      {(l.裁决 || []).map((v, i) => (
        <div className="verdict" key={i}>
          {renderLightText(v.replace(/^>\s*/, ''))}
        </div>
      ))}
    </div>
  )
}

function PItem({ b, text }: { b: string; text: string }) {
  return (
    <div className="pitem">
      <b>{b}</b>
      <span>{renderLightText(text)}</span>
    </div>
  )
}
function ReportRow({ b, text }: { b: string; text?: string }) {
  return (
    <div className="report-row">
      <b>{b}</b>
      <span>{renderLightText(text || '')}</span>
    </div>
  )
}

function LaneActs() {
  return (
    <div className="acts">
      <span className="muted">旧工单执行动作已归档；新的修改请回大厅重新说需求。</span>
    </div>
  )
}

function VerdictActs() {
  return (
    <div className="acts">
      <span className="muted">旧工单验收动作已归档；当前只保留历史查看。</span>
    </div>
  )
}

function AskCard({ c, reload }: { c: 室请示; reload: () => void }) {
  const [rejecting, setRejecting] = useState(false)
  const [v, setV] = useState('')
  return (
    <div className="ask-card">
      <div className="who">
        {c.岗位 || ''} ｜ {c.桶}
      </div>
      <div className="body">
        {renderLightText((c.内容 || '') + (c.建议 ? '\n建议：' + c.建议 : ''))}
      </div>
      {c.经理理由 && (
        <div className="mgr">
          {renderLightText(`项目经理预审（${c.经理裁决 || ''}）：${c.经理理由}`)}
        </div>
      )}
      {c.船主理由 && (
        <div className="mgr">
          {renderLightText(`你的裁决（${c.状态}）：${c.船主理由}`)}
        </div>
      )}
      {c.桶 === '待船主' && (
        <div className="acts">
          {!rejecting ? (
            <>
              <button className="ok sm" onClick={() => 决策(c.id, '批', '', reload)}>
                批
              </button>
              <button className="bad sm" onClick={() => setRejecting(true)}>
                驳
              </button>
            </>
          ) : (
            <div className="inline-in">
              <input
                autoFocus
                value={v}
                placeholder="驳回理由…"
                onChange={(e) => setV(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    决策(c.id, '驳', v, reload)
                    setRejecting(false)
                    setV('')
                  }
                  if (e.key === 'Escape') setRejecting(false)
                }}
              />
              <button
                className="prime sm"
                onClick={() => {
                  决策(c.id, '驳', v, reload)
                  setRejecting(false)
                  setV('')
                }}
              >
                确定
              </button>
              <button className="ghost sm" onClick={() => setRejecting(false)}>
                取消
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
