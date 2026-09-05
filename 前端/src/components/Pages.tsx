import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type {
  Board,
  健康,
  健康探测,
  已决项,
  待确认项,
  回炉项,
  活公司验收项,
  裁决项,
  轨迹步,
  验收项,
} from '../api'
import type { View } from '../App'
import type { HallStore } from '../hallStore'
import { 决策, 取消管理, 说清楚点, 确认管理, 活公司验收, 调级 } from '../actions'
import {
  projectHaloPoint,
  useHaloFrame,
  type HaloProjectionFrame,
  type HaloPoint,
} from './HaloCanvas'
import { renderLightText } from './文本渲染'

const CREW_ANGLES: Record<string, number> = {
  老钟: 3.5,
  老梁: 2.45,
  老纪: 1.55,
  阿强: 0.55,
  阿言: 5.5,
}
const CREW_ORDER = ['老钟', '老梁', '老纪', '阿强', '阿言']
const LEADER_LEN = 90
const LABEL_GAP = 10

function hhmm(t?: string): string {
  if (!t) return ''
  const m = t.match(/(\d{1,2}):(\d{2})/)
  return m ? `${m[1].padStart(2, '0')}:${m[2]}` : ''
}

export function CrewView({
  board,
  store,
  health,
  refresh,
}: {
  board: Board | null
  store: HallStore
  health: 健康 | null
  refresh: () => void
}) {
  const members = board?.成员 ?? []
  const card = store.runCard?.kind === '点名' ? store.runCard : null
  const [open, setOpen] = useState<string | null>(null)
  const frame = useHaloFrame()
  const activePeople = useMemo(
    () => new Set(
      store.活人单元
        .filter((u) => u.状态 === '工作中')
        .flatMap((u) => [u.岗位, u.名字])
        .filter(Boolean),
    ),
    [store.活人单元],
  )
  const orbit = useMemo(
    () => buildCrewOrbit(members, activePeople, frame, health),
    [members, activePeople, frame, health],
  )
  const ownerTop = frame ? Math.min(frame.height - 118, frame.cy + frame.ry * 2.35 + 58) : 0

  return (
    <main className="stage page-stage crew-orbit-stage">
      <div className="crew-orbit" aria-label="轨道花名册">
        <div className="crew-readout-left">CREW · 班子</div>
        <button className="crew-readout-action" onClick={store.点名}>点名</button>

        {frame && (
          <svg
            className="crew-orbit-lines"
            viewBox={`0 0 ${frame.width} ${frame.height}`}
            aria-hidden
          >
            {orbit.map((m) => (
              <line
                key={m.key}
                x1={m.node.x}
                y1={m.node.y}
                x2={m.lineEnd.x}
                y2={m.lineEnd.y}
              />
            ))}
          </svg>
        )}

        {orbit.map((m) => (
          <div
            className={'crew-orbit-node status-' + m.status + (m.active ? ' is-live' : '')}
            key={m.key + '-node'}
            style={{ left: m.node.x, top: m.node.y }}
            aria-hidden
          />
        ))}

        {orbit.map((m) => (
          <div
            className={'crew-nameplate side-' + m.side}
            key={m.key}
            style={{ left: m.label.x, top: m.label.y }}
          >
            <div className="crew-nameplate-title">
              <span className={'crew-nameplate-status status-' + m.status} />
              <div className="crew-nameplate-name">{m.person}</div>
            </div>
            <div className="crew-nameplate-meta">{m.role} · {m.model}</div>
            {m.职称 && (
              <div className="crew-nameplate-rank">
                <span className={'rank-badge' + (m.是经理 ? ' is-mgr' : m.是部门头 ? ' is-head' : '')}>
                  {m.是经理 ? '👑 ' : m.是部门头 ? '◆ ' : ''}{m.职称}
                </span>
                {m.信任 && <span className={'trust-badge trust-' + m.信任}>信任·{m.信任}</span>}
                <span className="reputation-badge">信誉·{Number(m.信誉分 ?? 0).toFixed(1)}</span>
                {typeof m.评级 === 'number' && (
                  <span className="rank-adjust" title="船主手调职级（纠正自动升降职）">
                    <button
                      type="button"
                      disabled={(m.评级 ?? 1) <= 1}
                      onClick={() => 调级(m.person, (m.评级 ?? 1) - 1, refresh)}
                    >↓</button>
                    <button
                      type="button"
                      disabled={(m.评级 ?? 5) >= 5}
                      onClick={() => 调级(m.person, (m.评级 ?? 1) + 1, refresh)}
                    >↑</button>
                  </span>
                )}
              </div>
            )}
            {!!(m.晋升达标 && m.晋升达标 > 0) && (
              <div className="crew-nameplate-climb">
                {m.晋升试用
                  ? '晋升试用中 · 保持准确'
                  : `晋升 ${Math.round(m.晋升分 ?? 0)}/${m.晋升达标}`}
              </div>
            )}
            {m.healthLine && <div className="crew-nameplate-health">{m.healthLine}</div>}
            {m.ability && <div className="crew-nameplate-ability">{m.ability}</div>}
          </div>
        ))}

        {frame && (
          <div className="crew-owner-station" style={{ left: frame.cx, top: ownerTop }}>
            <span className="crew-owner-beacon" />
            <span className="crew-owner-title">示例主人 · 船主</span>
            <span className="crew-owner-meta">最终拍板 · 不走模型</span>
          </div>
        )}

        {card && (
          <div className="rollcall-trace crew-orbit-trace">
            {card.lines.map((line, i) => (
              <div className="trace-line" key={i}>{line}</div>
            ))}
          </div>
        )}
      </div>

      <div className="stage-pad holo-page crew-fallback">
        <div className="page-head">
          <div className="page-k">CREW · 班子</div>
          <button className="text-action page-top-action" onClick={store.点名}>点名</button>
        </div>

        <div className="table-list crew-list">
          {(health?.系统?.问题 || []).slice(0, 4).map((issue, i) => (
            <div className="table-row crew-row" key={`system-${i}`}>
              <div className="table-row-main">
                <div className="crew-left">
                  <span className={'status-dot ' + (issue.级别 === '错误' ? 'status-down' : 'status-idle')} />
                  <span className="crew-person">{issue.区域 || '系统'}</span>
                </div>
                <div className="crew-model">{issue.级别 || '提示'}</div>
              </div>
              <div className="crew-health-line">{issue.说明 || ''}</div>
            </div>
          ))}
          {members.map((m) => {
            const model = m.model || m.registered_model || '未知'
            const person = m.名字 || m.title || m.name
            const role = m.title || m.name
            const ability = abilityText(m.abilities)
            const key = m.name
            const active = store.工作中 && (
              activePeople.has(m.name) ||
              activePeople.has(m.title || '') ||
              activePeople.has(m.名字 || '')
            )
            const probe = memberHealth(m, health)
            const status = crewStatus(active, probe)
            const healthLine = status === 'down' ? healthSummary(probe) : ''
            return (
              <button
                className={'table-row crew-row ' + (ability ? 'can-expand ' : '') + (active ? 'is-live' : '')}
                key={key}
                onClick={() => ability && setOpen(open === key ? null : key)}
                aria-expanded={ability ? open === key : undefined}
              >
                <div className="table-row-main">
                  <div className="crew-left">
                    <span className={'status-dot status-' + status} />
                    <span className="crew-person">{person}</span>
                    <span className="crew-role">{role}</span>
                  </div>
                  <div className="crew-model">{model}</div>
                </div>
                {healthLine && <div className="crew-health-line">{healthLine}</div>}
                {ability && open === key && <div className="crew-abilities">{ability}</div>}
              </button>
            )
          })}
          <div className="table-row crew-row crew-owner">
            <div className="table-row-main">
              <div className="crew-left">
                <span className="status-dot owner-dot" />
                <span className="crew-person">示例主人 · 船主</span>
              </div>
              <div className="crew-model">最终拍板 · 不走模型</div>
            </div>
          </div>
        </div>

        {card && (
          <div className="rollcall-trace">
            {card.lines.map((line, i) => (
              <div className="trace-line" key={i}>{line}</div>
            ))}
          </div>
        )}
      </div>
    </main>
  )
}

type CrewStatus = 'live' | 'down' | 'ok' | 'unknown'

type CrewOrbitItem = {
  key: string
  person: string
  role: string
  model: string
  ability: string
  active: boolean
  status: CrewStatus
  healthLine: string
  side: 'left' | 'right'
  node: HaloPoint
  lineEnd: HaloPoint
  label: HaloPoint
  职称?: string
  信任?: string
  是经理?: boolean
  是部门头?: boolean
  晋升分?: number
  晋升达标?: number
  晋升试用?: boolean
  评级?: number
  信誉分?: number
}

function buildCrewOrbit(
  members: Board['成员'],
  activePeople: Set<string | undefined>,
  frame: HaloProjectionFrame | null,
  health: 健康 | null,
): CrewOrbitItem[] {
  if (!frame || !members?.length) return []
  const ordered = orderCrewMembers(members).slice(0, 8)   // F10：跟着实际成员走（不写死5），加人也进轨道
  return ordered.map((m, i) => {
    const person = m.名字 || m.title || m.name
    const role = m.title || m.name
    const model = modelName(m.model || m.registered_model || '未知')
    const angle = CREW_ANGLES[person] ?? CREW_ANGLES[m.name] ?? CREW_ANGLES[role] ?? (i / ordered.length) * Math.PI * 2   // F10：未预设角度的按实际人数均分
    const node = projectHaloPoint(angle, frame)
    const vector = normalize({ x: node.x - frame.cx, y: node.y - frame.cy })
    const lineEnd = {
      x: node.x + vector.x * LEADER_LEN,
      y: node.y + vector.y * LEADER_LEN,
    }
    const label = {
      x: lineEnd.x + vector.x * LABEL_GAP,
      y: lineEnd.y + vector.y * LABEL_GAP,
    }
    const side = node.x < frame.cx ? 'left' : 'right'
    const active = activePeople.has(m.name) || activePeople.has(m.title) || activePeople.has(m.名字)
    const probe = memberHealth(m, health)
    const status = crewStatus(active, probe)
    return {
      key: m.name,
      person,
      role,
      model,
      ability: abilityText(m.abilities),
      active,
      status,
      healthLine: status === 'down' ? healthSummary(probe) : '',
      side,
      node,
      lineEnd,
      label,
      职称: m.职称,
      信任: m.信任等级,
      是经理: m.是经理,
      是部门头: m.是部门头,
      晋升分: m.晋升分,
      晋升达标: m.晋升达标,
      晋升试用: m.晋升试用,
      评级: m.评级,
      信誉分: m.信誉分,
    }
  })
}

function memberHealth(m: NonNullable<Board['成员']>[number], health: 健康 | null): 健康探测 | undefined {
  const h = health?.班子
  if (!h) return undefined
  const keys = [m.title, m.name, m.tier, m.名字].filter(Boolean) as string[]
  for (const k of keys) {
    if (h[k]) return h[k]
  }
  return undefined
}

function crewStatus(active: boolean, probe?: 健康探测): CrewStatus {
  if (active) return 'live'
  if (probe?.ok === false) return 'down'
  if (probe?.ok === true) return 'ok'
  return 'unknown'
}

function healthSummary(probe?: 健康探测): string {
  const reason = probe?.原因 || '探测失败'
  const time = hhmm(probe?.时间)
  return time ? `${reason} · ${time}` : reason
}

function orderCrewMembers(members: NonNullable<Board['成员']>) {
  const used = new Set<number>()
  const byFixedName = CREW_ORDER
    .map((name) => {
      const idx = members.findIndex((m, i) => !used.has(i) && (m.名字 === name || m.name === name || m.title === name))
      if (idx < 0) return null
      used.add(idx)
      return members[idx]
    })
    .filter(Boolean) as NonNullable<Board['成员']>
  const rest = members.filter((_, i) => !used.has(i))
  return [...byFixedName, ...rest]
}

function normalize(p: HaloPoint): HaloPoint {
  const len = Math.hypot(p.x, p.y) || 1
  return { x: p.x / len, y: p.y / len }
}

function modelName(v: string) {
  return v
    .replace(/^gpt/i, 'GPT')
    .replace(/^glm/i, 'GLM')
    .replace(/^qwen/i, 'Qwen')
    .replace(/^deepseek/i, 'DeepSeek')
}

export function DecisionsView({
  board,
  setView,
  refresh,
}: {
  board: Board | null
  setView: (v: View) => void
  refresh: () => void
}) {
  const td = board?.待办 ?? {}
  const 确 = td.待确认 ?? []
  const 裁 = td.待裁决 ?? []
  const 回 = td.待办 ?? []
  const 旧验 = td.待验收 ?? []
  const 活验 = td.活公司待验收 ?? []
  const [flash, setFlash] = useState(false)
  const flashTimers = useRef<number[]>([])
  const 确组: Record<string, 待确认项[]> = {}
  确.forEach((x) => {
    ;(确组[x.会议] = 确组[x.会议] ?? []).push(x)
  })
  const hasItems = Boolean(
    Object.keys(确组).length ||
    裁.length ||
    回.length ||
    活验.length ||
    旧验.length,
  )
  useEffect(() => () => flashTimers.current.forEach((id) => window.clearTimeout(id)), [])
  const triggerVerdictFlash = () => {
    flashTimers.current.forEach((id) => window.clearTimeout(id))
    flashTimers.current = []
    setFlash(true)
    flashTimers.current.push(window.setTimeout(() => setFlash(false), 60))
  }

  return (
    <main className="stage page-stage decision-stage">
      <div className="verdict-flash" data-on={flash ? '1' : '0'} aria-hidden />
      <div className="stage-pad holo-page">
        <div className="page-head">
          <div className="page-k">VERDICT · 待你拍板</div>
        </div>
        {hasItems ? (
          <div className="decision-list">
            {Object.entries(确组).map(([mid, items]) => (
              <OldConfirmLine key={mid} mid={mid} items={items} setView={setView} />
            ))}
            {裁.map((x) => <DecisionLine key={x.id} item={x} refresh={refresh} />)}
            {回.map((x) => <ReworkLine key={x.name} item={x} setView={setView} />)}
            {活验.map((x) => <LiveReviewLine key={x.id} item={x} refresh={refresh} onApproved={triggerVerdictFlash} />)}
            {旧验.map((x) => <OldReviewLine key={x.name} item={x} setView={setView} />)}
          </div>
        ) : (
          <div className="decision-empty">此刻没有要你拍的板</div>
        )}
        <SettledRecall items={td.已决 ?? []} />
      </div>
    </main>
  )
}

function SettledRecall({ items }: { items: 已决项[] }) {
  const [open, setOpen] = useState(false)
  if (!items.length) return null
  return (
    <div className="settled-recall">
      <button type="button" className="settled-head" onClick={() => setOpen(!open)}>
        <span className="li-chev">{open ? '▾' : '▸'}</span>
        <span className="settled-k">已决回看</span>
        <span className="settled-n">{items.length}</span>
      </button>
      {open && (
        <div className="settled-list">
          {items.map((x) => {
            const bad = /打回|驳/.test(x.裁决 || '')
            return (
              <div className="settled-row" key={(x.类 || '') + x.id}>
                <span className="settled-kind">{x.类}</span>
                <span className="settled-title" title={x.标题}>{x.标题}</span>
                <span className={'settled-verdict' + (bad ? ' r' : ' g')}>{x.裁决}</span>
                <span className="settled-t">{到你显示(x.时间)}</span>
                {x.理由 && <span className="settled-why" title={x.理由}>{x.理由}</span>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function OldConfirmLine({
  mid,
  items,
  setView,
}: {
  mid: string
  items: 待确认项[]
  setView: (v: View) => void
}) {
  return (
    <div className="decision-line">
      <div className="line-main">{items[0]?.标题 || mid}</div>
      <div className="line-meta">旧工单待确认 / {items.length} 张子工单</div>
      <div className="line-detail">{items.map((x) => x.角色).filter(Boolean).join('、')}</div>
      <LineActions>
        <button className="text-action" onClick={() => setView({ t: 'room', id: mid })}>
          打开场次
        </button>
      </LineActions>
    </div>
  )
}

function 到你显示(t?: string): string {
  if (!t) return ''
  const d = t.match(/\d{4}-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})/)
  return d ? `${d[1]}-${d[2]} ${d[3].padStart(2, '0')}:${d[4]}` : hhmm(t)
}

function 摘要(s?: string): string {
  return (s || '').replace(/\s+/g, ' ').trim()
}

function 时间轴({ 轨迹, action, 到你时间 }: { 轨迹?: 轨迹步[]; action?: ReactNode; 到你时间?: string }) {
  let steps = (轨迹 ?? []).slice().reverse()
  if (!steps.length) {
    // 存量记录（早于轨迹字段）没过程轨迹——兜底给一个船主节点，别让操作按钮跟着消失、船主没法拍板
    steps = [{ 层级: '船主', 谁: '你', 时间: 到你时间, 动作: '待你拍板（存量记录·无过程轨迹）' }]
  }
  return (
    <div className="tl">
      {steps.map((s, i) => {
        const 动作 = s.动作 || ''
        const now = s.层级 === '船主' && 动作.includes('待')
        const skip = 动作.includes('跳过')
        const good = /放行|通过|批准/.test(动作)
        const bad = /打回|驳/.test(动作)
        const cls = now ? 'tl-n now' : skip ? 'tl-n skip' : 'tl-n done'
        return (
          <div key={i} className={cls}>
            <div className="tl-top">
              <span className="tl-lvl">{s.层级 || ''}</span>
              <span className="tl-who">{s.谁 || ''}</span>
              <span className="tl-t">{hhmm(s.时间)}</span>
            </div>
            <div className={'tl-do' + (good ? ' g' : bad ? ' r' : '')}>{动作}</div>
            {now && action ? <div className="tl-act">{action}</div> : null}
          </div>
        )
      })}
    </div>
  )
}

// 船主自然语言直控·确认卡（浮窗）：像 Claude 的权限弹窗——屏幕正中弹出、背景压暗，不处理它就杵在那。
// 一有待确认卡就自动弹（不管你在哪个页），一张一张来。三键：确认执行=办 / 说清楚点=纠正重解 / 取消=作罢。
// 确认门双保险：只有你能点(防越权，本就"谁都不能靠嘴改账") + 模型理解错你不点就啥也不发生(防误解伤账)。
export function ManageConfirmModal({ board, refresh }: { board: Board | null; refresh: () => void }) {
  const 管确 = board?.待办?.管理待确认 ?? []
  if (!管确.length) return null
  const item = 管确[0]
  const 缺 = item.缺参 ?? []
  const 余 = 管确.length - 1
  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(3,5,10,0.66)', backdropFilter: 'blur(4px)', WebkitBackdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}
    >
      <div style={{ width: 'min(540px, 94vw)', maxHeight: '84vh', overflowY: 'auto', background: 'linear-gradient(180deg, rgba(22,27,40,0.98), rgba(13,16,26,0.98))', border: '1px solid rgba(150,180,255,0.20)', borderRadius: 14, boxShadow: '0 24px 90px rgba(0,0,0,0.6)', padding: '20px 22px', color: 'rgba(226,232,246,0.92)' }}>
        <div style={{ fontSize: 12, letterSpacing: 2, color: 'rgba(255,196,120,0.92)', marginBottom: 12 }}>
          要你确认 · 管理动作{余 > 0 ? `　（后面还有 ${余} 张）` : ''}
        </div>
        {item.原话 ? <div style={{ fontSize: 13, color: 'rgba(180,190,210,0.7)', marginBottom: 10 }}>你说：{item.原话}</div> : null}
        <div style={{ fontSize: 15, lineHeight: 1.7, marginBottom: 8 }}>{renderLightText('我理解你要：' + (item.释义 || ''))}</div>
        {item.影响 ? <div style={{ fontSize: 13.5, lineHeight: 1.7, color: 'rgba(200,208,224,0.82)', marginBottom: 8 }}>{renderLightText('影响：' + item.影响)}</div> : null}
        {缺.length ? <div style={{ fontSize: 13, color: 'rgba(255,180,120,0.92)', margin: '8px 0' }}>⚠️ 信息还不全（缺 {缺.join('、')}），点「说清楚点」补上再确认。</div> : null}
        <div style={{ display: 'flex', gap: 18, alignItems: 'center', marginTop: 18, flexWrap: 'wrap' }}>
          {缺.length ? null : <button className="text-action" onClick={() => 确认管理(item.id, refresh)}>确认执行</button>}
          <ReasonAction label="说清楚点" placeholder="补一句：到底是谁给谁、哪一次" onSubmit={(v) => 说清楚点(item.id, v, refresh)} />
          <button className="text-action" onClick={() => 取消管理(item.id, refresh)}>取消</button>
        </div>
      </div>
    </div>
  )
}

function DecisionLine({ item, refresh }: { item: 裁决项; refresh: () => void }) {
  const [open, setOpen] = useState(true)
  const action = (
    <>
      <button className="text-action" onClick={() => 决策(item.id, '批', '', refresh)}>批准</button>
      <ReasonAction label="驳回" placeholder="驳回理由" onSubmit={(v) => 决策(item.id, '驳', v, refresh)} />
    </>
  )
  return (
    <div className="decision-line hot">
      <button type="button" className="li-head" onClick={() => setOpen(!open)}>
        <span className="li-chev">{open ? '▾' : '▸'}</span>
        <span className="li-kind">请示</span>
        <span className="li-title">{摘要(item.内容) || `${item.岗位 || ''} 的请示`}</span>
        <span className="li-t">到你 {到你显示(item.到你时间)}</span>
        <span className="li-st">待你裁决</span>
      </button>
      {open ? (
        item.轨迹?.length ? (
          <时间轴 轨迹={item.轨迹} action={action} />
        ) : (
          <div className="li-fallback">
            <div className="line-detail">{renderLightText(item.内容 || '')}</div>
            <LineActions>{action}</LineActions>
          </div>
        )
      ) : null}
    </div>
  )
}

function ReworkLine({ item, setView }: { item: 回炉项; setView: (v: View) => void }) {
  const report = item.经理报告 ?? {}
  const artifact = (item.产物 ?? []).map((p) => `${p.路径}：${p.状态}`).join('；')
  return (
    <div className="decision-line">
      <div className="line-main">{item.标题 || item.name}</div>
      <div className="line-meta">旧工单回炉 / {item.角色 || '岗位'} / {item.步数 ?? 0}/{item.预算 ?? '?'} 步</div>
      {artifact && <div className="line-detail">{artifact}</div>}
      {report.理由 && <div className="line-detail">{renderLightText(`经理报告：${report.理由}`)}</div>}
      {report.建议 && <div className="line-detail">{renderLightText(`建议：${report.建议}`)}</div>}
      {!!item.会议 && (
        <LineActions>
          <button className="text-action" onClick={() => setView({ t: 'room', id: item.会议 })}>
            打开场次
          </button>
        </LineActions>
      )}
    </div>
  )
}

function LiveReviewLine({
  item,
  refresh,
  onApproved,
}: {
  item: 活公司验收项
  refresh: () => void
  onApproved: () => void
}) {
  const [open, setOpen] = useState(true)
  const 可选 = (item.来源 || '').includes('可选')
  const approve = async () => {
    const ok = await 活公司验收(item.id, '批准', '', refresh)
    if (ok) onApproved()
  }
  const action = (
    <>
      <button className="text-action" onClick={approve}>批准 · 打板</button>
      <ReasonAction label="打回" placeholder="打回理由" onSubmit={(v) => 活公司验收(item.id, '打回', v, refresh)} />
    </>
  )
  const 详情 = (
    <>
      {item.产物 && <div className="line-detail li-prod">{renderLightText(item.产物)}</div>}
      {!!(item.文件 ?? []).length && (
        <div className="line-detail">动了的文件：{(item.文件 ?? []).join('、')}</div>
      )}
    </>
  )
  return (
    <div className="decision-line hot">
      <button type="button" className="li-head" onClick={() => setOpen(!open)}>
        <span className="li-chev">{open ? '▾' : '▸'}</span>
        <span className={'li-kind' + (可选 ? ' li-kind-opt' : '')}>{可选 ? '可选验收' : '交付'}</span>
        <span className="li-title">{摘要(item.任务)}</span>
        <span className="li-t">到你 {到你显示(item.到你时间 || item.时间)}</span>
        <span className="li-st">{可选 ? '可看可不看' : '待你拍板'}</span>
      </button>
      {open ? (
        item.轨迹?.length ? (
          <>
            <时间轴 轨迹={item.轨迹} action={action} 到你时间={item.到你时间} />
  {详情}
          </>
        ) : (
          <div className="li-fallback">
            {详情}
            <LineActions>{action}</LineActions>
          </div>
        )
      ) : null}
    </div>
  )
}

function OldReviewLine({ item, setView }: { item: 验收项; setView: (v: View) => void }) {
  return (
    <div className="decision-line">
      <div className="line-main">{item.标题 || item.name}</div>
      <div className="line-meta">旧工单待验收 / {item.角色 || '岗位'}</div>
      {!!item.会议 && (
        <LineActions>
          <button className="text-action" onClick={() => setView({ t: 'room', id: item.会议 })}>
            打开场次
          </button>
        </LineActions>
      )}
    </div>
  )
}

function LineActions({ children }: { children: ReactNode }) {
  return <div className="line-actions">{children}</div>
}

function abilityText(v?: string) {
  return (v || '')
    .split(/[，,]/)
    .map((x) => x.trim())
    .filter(Boolean)
    .join(' · ')
}

export function ReasonAction({
  label,
  placeholder,
  onSubmit,
}: {
  label: string
  placeholder: string
  onSubmit: (v: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [v, setV] = useState('')
  const submit = () => {
    onSubmit(v)
    setOpen(false)
    setV('')
  }
  if (!open) {
    return <button className="text-action muted-action" onClick={() => setOpen(true)}>{label}</button>
  }
  return (
    <span className="line-input">
      <input
        autoFocus
        value={v}
        placeholder={placeholder}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit()
          if (e.key === 'Escape') setOpen(false)
        }}
      />
      <button className="text-action" onClick={submit}>确定</button>
      <button className="text-action muted-action" onClick={() => setOpen(false)}>取消</button>
    </span>
  )
}
