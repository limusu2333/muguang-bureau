import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import type { Board, 健康, 活人单元 } from '../api'
import type { View } from '../App'
import type { VisualTheme } from '../theme'
import OwnerAccountMenu from './OwnerAccountMenu'
import { visualFrameRate } from './visualPerformance'
import HaloCanvas, {
  projectHaloPoint,
  ringHue,
  useHaloFrame,
  星球公转速,
  星球径向,
  设星球,
  type HaloMode,
  type HaloPoint,
  type HaloProjectionFrame,
} from './HaloCanvas'

const MistButterflyField = lazy(() => import('./MistButterflyField'))

function pad(n: number) {
  return String(n).padStart(2, '0')
}

function clockText() {
  const d = new Date()
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function sceneText() {
  const d = new Date()
  return `${pad(d.getMonth() + 1)}${pad(d.getDate())}`
}

function todoCount(board: Board | null) {
  const t = board?.待办
  return (
    (t?.待确认?.length ?? 0) +
    (t?.待裁决?.length ?? 0) +
    (t?.待办?.length ?? 0) +
    (t?.待验收?.length ?? 0) +
    (t?.活公司待验收?.length ?? 0)
  )
}

function latestLine(u: 活人单元) {
  const last = u.动作[u.动作.length - 1]
  return (
    u.发言 ||
    last?.结果 ||
    last?.目标 ||
    (u.思考 ? '正在推演：' + u.思考.slice(-42) : '') ||
    '正在判断下一步。'
  )
}

function unitMeta(u: 活人单元) {
  const actions = u.动作.length
  const active = u.状态 === '工作中'
  const secs = active
    ? Math.max(1, Math.round((Date.now() - u.起) / 1000))
    : u.止
      ? Math.round((u.止 - u.起) / 1000)
      : 0
  return `${actions} ACTION${actions === 1 ? '' : 'S'} · ${secs || 1}s`
}

function healthDownCount(health: 健康 | null): number {
  const crew = Object.values(health?.班子 ?? {}).filter((v) => v?.ok === false).length
  const issues = (health?.系统?.问题 ?? []).filter((v) => v?.级别 === '错误' || v?.级别 === '警告').length
  const taskIssues = health?.任务?.近7天失败或中断 ?? 0
  return crew + issues + taskIssues + (health?.系统?.ok === false ? 1 : 0)
}

export function HoloChrome({
  mode,
  recede,
  连着,
  board,
  view,
  setView,
  units,
  health,
  theme,
  setTheme,
  onVisualReady,
}: {
  mode: HaloMode
  连着: boolean
  recede: boolean
  board: Board | null
  view: View
  setView: (v: View) => void
  units: 活人单元[]
  health: 健康 | null
  theme: VisualTheme
  setTheme: (theme: VisualTheme) => void
  onVisualReady: (theme: VisualTheme) => void
}) {
  const [clk, setClk] = useState(clockText)
  useEffect(() => {
    const id = setInterval(() => setClk(clockText()), 1000)
    return () => clearInterval(id)
  }, [])
  useEffect(() => {
    if (theme === 'mist' && view.t !== 'hall' && view.t !== 'settings') onVisualReady('mist')
  }, [onVisualReady, theme, view.t])

  const pending = todoCount(board)
  const crewCount = board?.成员?.length ?? 0
  const downCount = healthDownCount(health)
  const crewReadout = `SCENES ${board?.项目?.length ?? 0} · CREW ${crewCount}${downCount ? ` · ${downCount} DOWN` : ''}`
  const visualForeground = view.t === 'hall' || view.t === 'settings'
  const visualFps = visualFrameRate(visualForeground, mode === 'working')

  return (
    <>
      {theme === 'deep-space' ? (
        <HaloCanvas
          mode={mode}
          recede={recede}
          targetFps={visualFps}
          interactionBoost={visualForeground}
          onReady={onVisualReady}
        />
      ) : null}
      <div className="narrow-guard">这扇窗太窄了，看不出它真实的样子。<br />请在浏览器里全屏打开。</div>
      <div className="holo-hatch left" />
      <div className="holo-hatch right" />
      <div className="holo-watermark">MUNAEA</div>

      <OwnerAccountMenu />

      <nav className="holo-nav" aria-label="主导航">
        <button className={view.t === 'hall' || view.t === 'day' || view.t === 'room' ? 'on' : ''} onClick={() => setView({ t: 'hall' })}>大厅</button>
        <button className={view.t === 'crew' ? 'on' : ''} onClick={() => setView({ t: 'crew' })}>班子</button>
        <button className={view.t === 'memory' ? 'on' : ''} onClick={() => setView({ t: 'memory' })}>记忆库</button>
        <button className={view.t === 'decisions' ? 'on has-alert' : pending ? 'has-alert' : ''} onClick={() => setView({ t: 'decisions' })}>待你拍板{pending ? <i /> : null}</button>
        <button className={view.t === 'settings' ? 'on' : ''} onClick={() => setView({ t: 'settings' })}>设置</button>
      </nav>

      <button
        className="visual-theme-toggle"
        onClick={() => setTheme(theme === 'deep-space' ? 'mist' : 'deep-space')}
        aria-label={theme === 'deep-space' ? '切换到雾中主题' : '切换到深空主题'}
        title={theme === 'deep-space' ? '切换到雾中主题' : '切换到深空主题'}
      >
        <span aria-hidden />
      </button>

      <span className="readout left top">MUNAEA <b>▸</b> {mode === 'working' ? '拍摄中' : mode === 'verdict' ? '判定时刻' : '深夜在线'}</span>
      <span className={'readout right top mono ' + (mode === 'working' ? 'rec' : '')}>{mode === 'working' ? '● REC ' : ''}{clk} · LINK <em className={连着 ? '' : 'down'} title={连着 ? '公司在线' : '连不上公司了——它掉线或在重启，恢复后自动接上'}>●</em></span>
      <span className="readout left bottom">SCENE {sceneText()} · {mode === 'working' ? 'TAKE 01' : mode === 'verdict' ? 'WAITING VERDICT' : 'STANDBY'}</span>
      <span className={'readout right bottom ' + (downCount ? 'down' : '')}>{crewReadout}</span>
      <span className="frame-idx left top2">01</span>
      <span className="frame-idx right top2">01</span>
      <span className="frame-idx left bottom2">02</span>
      <span className="frame-idx right bottom2">02</span>

      {/* 引线标注只属于空场待机：大厅有对话（环已退后）就不挂——否则任务一结束标注又蹦回来，
          船主看到的是"一会有字一会没字"（2026-07-03 抓的虫） */}
      {view.t === 'hall' && (
        <IdleCallouts pending={pending} crewCount={crewCount} setView={setView} hidden={!(mode === 'idle' && !recede)} />
      )}
      {/* 星球层（实例主人的设计）：环上五颗小星球=五个人，被唤醒就呼吸闪烁，点开看他此刻的脑内 */}
      {(view.t === 'hall' || view.t === 'settings') && (
        <CrewPlanets key={`crew-${theme}`} board={board} units={units} viewKey={view.t} theme={theme} targetFps={visualFps} onVisualReady={onVisualReady} />
      )}
      {/* 雾幕必须是 #app 的兄弟层（z13 < #app z14）：塞进文字层内部会反过来压住对话——2026-07-03 深夜的层序事故 */}
      {(view.t === 'hall' || view.t === 'settings') && <div className="hall-veil" aria-hidden />}
    </>
  )
}

function IdleCallouts({
  pending,
  crewCount,
  setView,
  hidden,
}: {
  pending: number
  crewCount: number
  setView: (v: View) => void
  hidden?: boolean
}) {
  const frame = useHaloFrame()
  const callouts = useMemo(() => buildIdleCallouts(frame), [frame])
  const core = callouts?.core
  const crew = callouts?.crew

  return (
    <div className={'idle-callouts' + (hidden ? ' off' : '')} aria-hidden>
      {frame && callouts && (
        <svg viewBox={`0 0 ${frame.width} ${frame.height}`} preserveAspectRatio="none">
          {[core, crew].filter(Boolean).map((c) => (
            <g key={c!.key}>
              <line x1={c!.lineStart.x} y1={c!.lineStart.y} x2={c!.node.x} y2={c!.node.y} />
              <circle cx={c!.node.x} cy={c!.node.y} r="3.2" />
            </g>
          ))}
        </svg>
      )}
      <div className="callout c1" style={core ? { left: core.label.x, top: core.label.y } : undefined}>
        核心 · 在场 <small>CORE / IDLE · BREATH 0.5HZ</small>
      </div>
      {pending > 0 ? (
        <button
          className="callout c2 actionable"
          style={crew ? { left: crew.label.x, top: crew.label.y, right: 'auto' } : undefined}
          onClick={() => setView({ t: 'decisions' })}
        >
          待你拍板 · {pending} 件 <small>OWNER VERDICT / REVIEW QUEUED</small>
        </button>
      ) : (
        <div className="callout c2" style={crew ? { left: crew.label.x, top: crew.label.y, right: 'auto' } : undefined}>
          五人待命 · 记忆同步 <small>CREW {crewCount || 5} STANDBY / MEM SYNCED</small>
        </div>
      )}
    </div>
  )
}

type IdleAnchor = {
  key: 'core' | 'crew'
  node: HaloPoint
  lineStart: HaloPoint
  label: HaloPoint
}

function buildIdleCallouts(frame: HaloProjectionFrame | null): { core: IdleAnchor; crew: IdleAnchor } | null {
  if (!frame) return null
  return {
    core: buildIdleAnchor('core', 2.9, frame),
    crew: buildIdleAnchor('crew', 5.85, frame),
  }
}

function buildIdleAnchor(key: IdleAnchor['key'], u: number, frame: HaloProjectionFrame): IdleAnchor {
  const node = projectHaloPoint(u, frame)
  const vector = normalize({ x: node.x - frame.cx, y: node.y - frame.cy })
  const labelOffset = Math.max(128, Math.min(180, frame.rx * 0.78))
  return {
    key,
    node,
    lineStart: {
      x: node.x + vector.x * (labelOffset - 22),
      y: node.y + vector.y * (labelOffset - 22),
    },
    label: {
      x: node.x + vector.x * labelOffset,
      y: node.y + vector.y * labelOffset,
    },
  }
}

function normalize(p: HaloPoint): HaloPoint {
  const len = Math.hypot(p.x, p.y) || 1
  return { x: p.x / len, y: p.y / len }
}

// 星球层：环上五颗小星球=五个人。没被唤醒=暗点；被唤醒思考中=琥珀呼吸闪；说完=短暂常亮。
// 点击星球=结霜小卡展示他此刻的脑内（思考流/正在做的动作/说的话）。
const 星球角 = [3.5, 2.45, 1.55, 0.55, 5.5]
const 雾中蝶位 = [
  { x: 0.50, y: 0.42, size: 112, opacity: 0.38, routeOffset: 0, timeScale: 1, flightRegion: { left: 0.25, right: 0.61, top: 0.18, bottom: 0.55 } },
  { x: 0.47, y: 0.32, size: 92, opacity: 0.29, routeOffset: 2, timeScale: 0.91, flightRegion: { left: 0.27, right: 0.72, top: 0.14, bottom: 0.58 } },
  { x: 0.58, y: 0.32, size: 78, opacity: 0.34, routeOffset: 4, timeScale: 1.05, flightRegion: { left: 0.44, right: 0.77, top: 0.13, bottom: 0.59 } },
  { x: 0.64, y: 0.43, size: 60, opacity: 0.25, routeOffset: 6, timeScale: 0.96, flightRegion: { left: 0.36, right: 0.78, top: 0.24, bottom: 0.60 } },
  { x: 0.54, y: 0.38, size: 86, opacity: 0.31, routeOffset: 8, timeScale: 1.02, flightRegion: { left: 0.29, right: 0.75, top: 0.17, bottom: 0.58 } },
]
// 颜色不再单配：画布直接采样环在星球所在处的本地色相（球=环的一部分，颜色永不跳）

// 弹卡缩放（抄 re-resizable/MIT 的方向学，机制照搬代码自写）：
// 把手放在"自由角"——左贴边的卡右下角、右贴边的卡左下角，被拖的角永远跟着鼠标走，
// 宽度朝自由边生长；Pointer Capture 防跟丢。原生 resize:both 在右贴边卡上左右是反的，弃用。
function 抓角(e: React.PointerEvent<HTMLSpanElement>, 反: boolean) {
  e.preventDefault()
  e.stopPropagation()
  const grip = e.currentTarget
  const pop = grip.parentElement as HTMLElement
  const r = pop.getBoundingClientRect()
  const sx = e.clientX
  const sy = e.clientY
  const 动 = (ev: PointerEvent) => {
    const w = Math.min(window.innerWidth * 0.72, Math.max(220, r.width + (反 ? sx - ev.clientX : ev.clientX - sx)))
    const h = Math.min(window.innerHeight * 0.72, Math.max(110, r.height + (ev.clientY - sy)))
    pop.style.width = w + 'px'
    pop.style.height = h + 'px'
  }
  const 收 = () => {
    grip.removeEventListener('pointermove', 动)
    grip.removeEventListener('pointerup', 收)
    grip.removeEventListener('pointercancel', 收)
  }
  try { grip.setPointerCapture(e.pointerId) } catch { /* 合成事件无真指针时跳过捕获 */ }
  grip.addEventListener('pointermove', 动)
  grip.addEventListener('pointerup', 收)
  grip.addEventListener('pointercancel', 收)
}

function CrewPlanets({
  board,
  units,
  viewKey,
  theme,
  targetFps,
  onVisualReady,
}: {
  board: Board | null
  units: 活人单元[]
  viewKey: View['t']
  theme: VisualTheme
  targetFps: number
  onVisualReady: (theme: VisualTheme) => void
}) {
  const frame = useHaloFrame()
  const [开, set开] = useState<string | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => set开(null), [viewKey])
  // 点空白关弹卡（实例主人：只能再点球太别扭）
  useEffect(() => {
    if (!开) return
    const 关 = (e: PointerEvent) => {
      const el = e.target as HTMLElement
      if (el.closest && (el.closest('.planet-pop') || el.closest('.planet-hit'))) return
      set开(null)
    }
    window.addEventListener('pointerdown', 关)
    return () => window.removeEventListener('pointerdown', 关)
  }, [开])
  const 人们 = useMemo(() => (board?.成员 ?? [])
    .map((m) => ({ 名: (m as { 名字?: string }).名字 || m.title || '', 岗: m.title || '' }))
    .filter((r) => r.名)
    .slice(0, 5), [board?.成员])
  const latest = useMemo(() => {
    const next: Record<string, 活人单元> = {}
    for (const u of units) next[u.名字] = u
    return next
  }, [units])
  // 状态灌进画布：星球由环的粒子长出来（同材质），DOM 层只负责点击与名字
  useEffect(() => {
    设星球(
      人们.map((p, i) => {
        const u = latest[p.名]
        return {
          u: 星球角[i] ?? i,
          hue: 0, // 颜色由画布采样环的本地色相，此字段弃用
          mode: (u?.状态 === '工作中' ? 2 : u ? 1 : 0) as 0 | 1 | 2,
        }
      }),
    )
    return () => 设星球([])
  }, [人们, latest])
  const 展示人们 = 人们
  if (人们.length === 0 || (theme === 'deep-space' && !frame)) return null

  return (
    <div className={'crew-planets' + (开 ? ' pop-open' : '')} ref={rootRef}>
      {theme === 'mist' ? (
        <Suspense fallback={null}>
          <MistButterflyField rootRef={rootRef} configs={雾中蝶位} targetFps={targetFps} onReady={onVisualReady} />
        </Suspense>
      ) : null}
      {展示人们.map((p, i) => {
        const 现角 = (星球角[i] ?? i) + (theme === 'deep-space' ? (frame?.time ?? 0) * 星球公转速 : 0)
        const 雾位 = 雾中蝶位[i] ?? 雾中蝶位[0]
        const pt = theme === 'mist'
          ? { x: `${雾位.x * 100}vw`, y: `${雾位.y * 100}vh` }
          : projectHaloPoint(现角, frame!, 星球径向)
        const u = latest[p.名]
        const busy = u?.状态 === '工作中'
        const 亮过 = !!u && !busy
        void 亮过
        const 思 = (u?.思考 || '').trim()
        const 说 = (u?.发言 || '').trim()  // 只用于兜底文案判断，正文在对话流里
        const 末动 = u?.动作.length ? u.动作[u.动作.length - 1] : null
        const 在翻 = 末动 && 末动.状态 === '进行'
        const flip = theme === 'mist' ? 雾位.x > 0.55 : Number(pt.x) > frame!.width * 0.55
        return (
          <div
            className={`planet-wrap${开 === p.名 ? ' open' : ''}${busy ? ' busy' : ''}`}
            key={p.名}
            data-person={p.名}
            style={{
              left: pt.x,
              top: pt.y,
              ['--h' as string]: String(Math.round(ringHue(现角 / (Math.PI * 2)))),
              ['--butterfly-size' as string]: `${雾位.size}px`,
            }}
          >
            <button
              className="planet-hit"
              onPointerDown={theme === 'mist' ? (event) => {
                if (event.button !== 0) return
                event.stopPropagation()
                set开(开 === p.名 ? null : p.名)
              } : undefined}
              onClick={theme === 'deep-space' ? () => set开(开 === p.名 ? null : p.名) : undefined}
              aria-label={`${p.名}${busy ? '，思考中' : '，待机'}`}
            >
              <span className="mist-person-flight" aria-hidden>
                <span className={`mist-person-name ${雾位.x > 0.62 ? 'left' : 'right'}`}>
                  {p.名}
                  <small>{p.岗}{busy ? <em>思考中</em> : null}</small>
                </span>
              </span>
            </button>
            <span className="planet-name planet-name-deep">{p.名}<small>{p.岗}</small></span>
            {开 === p.名 && (
              <div className={'planet-pop' + (flip ? ' flip' : '')}>
                <div className="planet-pop-scroll">
                <div className="planet-pop-k">
                  {p.名} · {p.岗}
                  {busy && <em>{在翻 ? '翻档案中' : '思考中'}</em>}
                </div>
                {!u && <div className="planet-pop-t dim">这轮没被叫醒——没看手机。</div>}
                {u && (
                  <>
                    <div className="planet-pop-sec">脑内</div>
                    <div className="planet-pop-t">
                      {思
                        ? '…' + 思.slice(-300)
                        : 在翻
                          ? `（正在翻档案：${末动!.工具 || '查证'}…）`
                          : busy
                            ? '（正在想……这轮思维流还没送到。）'
                            : 说
                              ? '（这轮思维流没外露；他说的话在下面对话里。）'
                              : '（这轮听完选择了沉默。）'}
                    </div>
                  </>
                )}
                </div>
                <span className="pop-grip" onPointerDown={(e) => 抓角(e, flip)} />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function CrewConstellation({ units }: { units: 活人单元[] }) {
  const shown = useMemo(() => {
    const active = units.filter((u) => u.状态 === '工作中')
    const pool = active.length ? active : units
    return pool.slice(0, 3)
  }, [units])

  if (!shown.length) return null
  return (
    <div className="crew-constellation" aria-label="工作中的成员">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none">
        <line className="l1" x1="24" y1="55" x2="43" y2="42" />
        <line className="l2" x1="76" y1="30" x2="57" y2="39" />
        <line className="l3" x1="78" y1="65" x2="58" y2="48" />
      </svg>
      {shown.map((u, i) => (
        <div className={`crew-float p${i + 1}`} key={u.单元id}>
          <div className="crew-name"><span className={u.状态 === '工作中' ? 'signal on' : 'signal'} />{u.名字}<small>{u.岗位}</small></div>
          <div className="crew-line">{latestLine(u).slice(0, 58)}</div>
          <div className={u.状态 === '工作中' ? 'crew-meta hot' : 'crew-meta'}>{unitMeta(u)}</div>
        </div>
      ))}
    </div>
  )
}
