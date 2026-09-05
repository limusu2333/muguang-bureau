import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { 活人单元, 工作动作 } from '../api'
import type { RunCard } from '../hallStore'
import { renderLightText } from './文本渲染'

export const __演示units: 活人单元[] = [
  {
    单元id: 'demo-pm',
    父单元: null,
    岗位: '项目经理',
    名字: '老钟',
    场合: '主事',
    状态: '工作中',
    思考: '先判断这是不是需要开会。老板要的是快速看见工作现场，不该把任务复杂化。需要找首席确认前端边界。',
    动作: [
      {
        动作id: 'demo-a1',
        工具: 'Read',
        目标: '前端/src/components/HallView.tsx',
        结果: '确认大厅舞台由 HallView 负责，工作现场应挂在对话栈上方。',
        轮: 1,
        状态: '完成',
        成功: true,
      },
    ],
    发言: '我先看清大厅布局，再让老梁接一下组件边界。',
    起: Date.now() - 18000,
  },
  {
    单元id: 'demo-chief',
    父单元: 'demo-pm',
    岗位: '首席工程师',
    名字: '老梁',
    场合: '参与',
    状态: '工作中',
    思考: '组件只读 store 数据，不能碰 hallStore。树形层级按父单元排，单元 id 不能按人名合并。',
    动作: [
      {
        动作id: 'demo-a2',
        工具: 'Grep',
        目标: '搜索 .mem-panel 和现有 worksite 样式',
        结果: '找到结霜玻璃配方和旧 worksite 类，新样式应追加在 holographic.css 尾部覆盖。',
        轮: 1,
        状态: '进行',
        成功: true,
      },
    ],
    发言: '',
    起: Date.now() - 9000,
  },
  {
    单元id: 'demo-eng',
    父单元: 'demo-chief',
    岗位: '工程师',
    名字: '阿强',
    场合: '参与',
    状态: '完成',
    思考: '',
    动作: [
      {
        动作id: 'demo-a3',
        工具: 'npm run build',
        目标: '前端生产构建',
        结果: 'Vite 构建通过。',
        轮: 2,
        状态: '完成',
        成功: true,
      },
    ],
    发言: '我这边构建口径没问题。',
    起: Date.now() - 6000,
    止: Date.now() - 1000,
  },
]

type Node = {
  unit: 活人单元
  children: Node[]
}

const 人色: Record<string, number> = {
  老钟: 345,
  项目经理: 345,
  老梁: 210,
  首席工程师: 210,
  阿强: 300,
  工程师: 300,
  阿言: 255,
  文案工程师: 255,
  '文案工程师（兼职）': 255,
  老纪: 32,
  测试工程师: 32,
}

export default function Worksite({
  units,
  runCard,
  工作中,
  showMeetingUnits = false,
}: {
  units: 活人单元[]
  runCard: RunCard | null
  工作中: boolean
  showMeetingUnits?: boolean
}) {
  const [openUnits, setOpenUnits] = useState<Record<string, boolean>>({})
  const [openThoughts, setOpenThoughts] = useState<Record<string, boolean>>({})
  const [openSpeech, setOpenSpeech] = useState<Record<string, boolean>>({})
  const [openActions, setOpenActions] = useState<Record<string, boolean>>({})

  const visibleUnits = useMemo(
    () => showMeetingUnits ? units : units.filter((u) => !u.会议id && !String(u.场合 || '').includes('会')),
    [showMeetingUnits, units],
  )
  const tree = useMemo(() => buildTree(visibleUnits), [visibleUnits])
  const latestProgress = runCard?.lines?.[runCard.lines.length - 1] || ''
  const elapsed = runCard ? elapsedSeconds(runCard) : 0

  return (
    <section className="worksite-panel" aria-label="工作现场">
      <div className="worksite-bar">
        <div className="worksite-live">
          <span className={工作中 ? 'worksite-main-dot on' : 'worksite-main-dot'} />
          <span>现场 · {visibleUnits.length}人</span>
        </div>
        {latestProgress && <div className="worksite-progress">{latestProgress}</div>}
        {runCard && <div className="worksite-clock">{formatSeconds(elapsed)}</div>}
      </div>

      <div className="worksite-scroll">
        {visibleUnits.length === 0 ? (
          <div className="worksite-empty">正在唤醒…</div>
        ) : (
          <div className="worksite-tree">
            {tree.map((node) => (
              <WorkUnit
                key={node.unit.单元id}
                node={node}
                depth={0}
                openUnits={openUnits}
                openThoughts={openThoughts}
                openSpeech={openSpeech}
                openActions={openActions}
                toggleUnit={(id) => setOpenUnits((m) => ({ ...m, [id]: !m[id] }))}
                toggleThought={(id) => setOpenThoughts((m) => ({ ...m, [id]: !m[id] }))}
                toggleSpeech={(id) => setOpenSpeech((m) => ({ ...m, [id]: !m[id] }))}
                toggleAction={(id) => setOpenActions((m) => ({ ...m, [id]: !m[id] }))}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function WorkUnit({
  node,
  depth,
  openUnits,
  openThoughts,
  openSpeech,
  openActions,
  toggleUnit,
  toggleThought,
  toggleSpeech,
  toggleAction,
}: {
  node: Node
  depth: number
  openUnits: Record<string, boolean>
  openThoughts: Record<string, boolean>
  openSpeech: Record<string, boolean>
  openActions: Record<string, boolean>
  toggleUnit: (id: string) => void
  toggleThought: (id: string) => void
  toggleSpeech: (id: string) => void
  toggleAction: (id: string) => void
}) {
  const unit = node.unit
  const open = !!openUnits[unit.单元id]
  const hue = 人色[unit.名字] ?? 人色[unit.岗位] ?? 48
  const active = unit.状态 === '工作中'
  const secs = unitSeconds(unit)
  const latest = latestDynamic(unit)

  return (
    <div className={'work-unit depth-' + Math.min(depth, 3)} style={{ ['--actor-h' as string]: String(hue) }}>
      <button className="work-unit-row" onClick={() => toggleUnit(unit.单元id)}>
        <span className={active ? 'work-signal on' : 'work-signal done'} />
        <span className="work-person">{unit.名字}</span>
        <span className="work-role">· {unit.岗位}</span>
        <span className="work-dynamic">{latest}</span>
        <span className="work-stats">{unit.动作.length} 动作 · {secs}s</span>
      </button>

      {open && (
        <div className="work-detail">
          <DetailBlock title="脑内">
            <Thought unit={unit} expanded={!!openThoughts[unit.单元id]} onToggle={() => toggleThought(unit.单元id)} />
          </DetailBlock>

          <DetailBlock title="动作">
            {unit.动作.length === 0 ? (
              <div className="work-muted">还没调用工具。</div>
            ) : (
              unit.动作.filter((a) => (a.工具 || a.目标 || '').trim()).map((action, index) => {
                const key = `${unit.单元id}:${action.动作id || `${action.轮}-${action.工具}-${index}`}`
                return (
                  <ActionLine
                    action={action}
                    key={key}
                    open={!!openActions[key]}
                    onToggle={() => toggleAction(key)}
                  />
                )
              })
            )}
          </DetailBlock>

          <DetailBlock title="发言">
            <Speech unit={unit} expanded={!!openSpeech[unit.单元id]} onToggle={() => toggleSpeech(unit.单元id)} />
          </DetailBlock>
        </div>
      )}

      {node.children.length > 0 && (
        <div className="work-children">
          {node.children.map((child) => (
            <WorkUnit
              key={child.unit.单元id}
              node={child}
              depth={depth + 1}
              openUnits={openUnits}
              openThoughts={openThoughts}
              openSpeech={openSpeech}
              openActions={openActions}
              toggleUnit={toggleUnit}
              toggleThought={toggleThought}
              toggleSpeech={toggleSpeech}
              toggleAction={toggleAction}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function DetailBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="work-detail-block">
      <div className="work-detail-title">{title}</div>
      <div className="work-detail-body">{children}</div>
    </div>
  )
}

function Thought({ unit, expanded, onToggle }: { unit: 活人单元; expanded: boolean; onToggle: () => void }) {
  const text = unit.思考.trim()
  if (!text) return <div className="work-muted">（思维未外露，看动作）</div>
  const short = tailText(text, 240)
  const showToggle = text.length > short.length
  return (
    <>
      <pre className="work-pre thought">{expanded ? text : short}</pre>
      {showToggle && (
        <button className="work-text-btn" onClick={onToggle}>{expanded ? '收起' : '看全部'}</button>
      )}
    </>
  )
}

function Speech({ unit, expanded, onToggle }: { unit: 活人单元; expanded: boolean; onToggle: () => void }) {
  const text = unit.发言.trim()
  if (!text) return <div className="work-muted">还没开口。</div>
  const lines = text.split(/\n+/)
  const short = lines.slice(0, 2).join('\n')
  const showToggle = lines.length > 2 || text.length > short.length
  return (
    <>
      <div className="work-speech">{expanded ? renderLightText(text) : short}</div>
      {showToggle && (
        <button className="work-text-btn" onClick={onToggle}>{expanded ? '收起' : '看全文'}</button>
      )}
    </>
  )
}

function ActionLine({
  action,
  open,
  onToggle,
}: {
  action: 工作动作
  open: boolean
  onToggle: () => void
}) {
  const label = `${action.工具 || '工具'} → ${clip(action.目标 || '等待目标…', 80)}`
  return (
    <div className="work-action">
      <button className="work-action-row" onClick={onToggle}>
        <span className={action.状态 === '进行' ? 'work-action-dot on' : action.成功 ? 'work-action-dot' : 'work-action-dot fail'} />
        <span className="work-action-label">{label}</span>
        <span className="work-action-round">R{action.轮 || 0}</span>
      </button>
      {open && (
        <pre className="work-pre action-result">{action.结果 || '（暂无结果）'}</pre>
      )}
    </div>
  )
}

function buildTree(units: 活人单元[]): Node[] {
  const nodes = new Map<string, Node>()
  for (const unit of units) nodes.set(unit.单元id, { unit, children: [] })

  const roots: Node[] = []
  for (const unit of units) {
    const node = nodes.get(unit.单元id)!
    const parent = unit.父单元
    if (parent && parent !== '大厅群聊' && nodes.has(parent)) {
      nodes.get(parent)!.children.push(node)
    } else {
      roots.push(node)
    }
  }
  return roots
}

function latestDynamic(unit: 活人单元) {
  const speech = lastLine(unit.发言)
  const lastAction = unit.动作[unit.动作.length - 1]
  return (
    clip(speech, 72) ||
    clip(lastAction?.目标 || '', 72) ||
    (unit.思考 ? clip(tailText(unit.思考, 40), 72) : '') ||
    (unit.状态 === '在场' ? '在场 · 待发言' : '正在琢磨…')
  )
}

function lastLine(text: string) {
  const lines = text.trim().split(/\n+/).filter(Boolean)
  return lines[lines.length - 1] || ''
}

function clip(text: string, max: number) {
  const s = text.trim().replace(/\s+/g, ' ')
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

function tailText(text: string, max: number) {
  const s = text.trim()
  return s.length > max ? '…' + s.slice(-max) : s
}

function unitSeconds(unit: 活人单元) {
  const end = unit.止 || Date.now()
  return Math.max(1, Math.round((end - unit.起) / 1000))
}

function elapsedSeconds(card: RunCard) {
  const end = card.running ? performance.now() : card.end || performance.now()
  return Math.max(1, Math.round((end - card.start) / 1000))
}

function formatSeconds(sec: number) {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return m ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`
}
