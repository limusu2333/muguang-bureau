import { Component, StrictMode, useCallback, useRef, useState, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import SoloButterfly, { type Mood, type Telemetry } from './SoloButterfly'
import './solo.css'

/** A crash must say what broke, on the page — never leave a blank canvas. */
class Boundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  state: { err: Error | null } = { err: null }
  static getDerivedStateFromError(err: Error) { return { err } }
  componentDidCatch(err: Error) { console.error('[butterfly] crashed', err) }
  render() {
    if (!this.state.err) return this.props.children
    return (
      <pre className="solo-crash">{this.state.err.message}{'\n\n'}{this.state.err.stack}</pre>
    )
  }
}

/** Runs the acceptance checks from the brief against the recorded flight. */
function audit(log: Telemetry[]) {
  if (log.length < 60) return null
  const lines: { name: string; pass: boolean; detail: string }[] = []

  // 1. A downstroke must be followed by the body actually rising.
  const downs: number[] = []
  for (let i = 1; i < log.length; i++) {
    if (log[i].downstroke > 0.02 && log[i - 1].downstroke <= 0.02) downs.push(i)
  }
  let rose = 0
  for (const i of downs) {
    const t0 = log[i].t
    let best = log[i].vel[1]
    for (let j = i; j < log.length && log[j].t - t0 < 0.15; j++) best = Math.max(best, log[j].vel[1])
    if (best > log[i].vel[1]) rose++
  }
  lines.push({
    name: '下拍后 150ms 内垂直速度向上变化',
    pass: downs.length > 0 && rose / downs.length > 0.8,
    detail: `${rose}/${downs.length} 次下拍后垂直速度上抬`,
  })

  // 2. Gliding must lose height.
  const glide = log.filter((l) => l.mode === 'glide')
  const glideAccel = glide.reduce((s, l) => s + l.accelY, 0) / Math.max(1, glide.length)
  lines.push({
    name: '滑翔段平均垂直加速度向下',
    pass: glide.length > 10 && glideAccel < 0,
    detail: `滑翔 ${glide.length} 帧，平均垂直加速度 ${glideAccel.toFixed(2)} m/s²`,
  })

  // 3. Speed must actually vary.
  const speeds = log.map((l) => l.speed)
  const sMin = Math.min(...speeds), sMax = Math.max(...speeds)
  const sAvg = speeds.reduce((a, b) => a + b, 0) / speeds.length
  lines.push({
    name: '速度有清楚的快慢变化（非匀速）',
    pass: sMax - sMin > sAvg * 0.25 && sMax < 5,
    detail: `${sMin.toFixed(2)} – ${sMax.toFixed(2)} m/s，均值 ${sAvg.toFixed(2)}`,
  })

  // 4. The head must point where it is going.
  const errs = log.map((l) => l.headingErrorDeg).sort((a, b) => a - b)
  const median = errs[Math.floor(errs.length / 2)]
  const p95 = errs[Math.floor(errs.length * 0.95)]
  lines.push({
    name: '身体前向与速度方向：中位 <12°，绝大多数 <35°',
    pass: median < 12 && p95 < 35,
    detail: `中位 ${median.toFixed(1)}°，95 分位 ${p95.toFixed(1)}°`,
  })

  // 5. The circuit must use the frame.
  const xs = log.map((l) => l.pos[0]), ys = log.map((l) => l.pos[1])
  const spanX = Math.max(...xs) - Math.min(...xs)
  const spanY = Math.max(...ys) - Math.min(...ys)
  const screenW = window.innerWidth / 900, screenH = window.innerHeight / 900
  lines.push({
    name: '航线覆盖 ≥35% 宽、≥25% 高',
    pass: spanX / screenW > 0.35 && spanY / screenH > 0.25,
    detail: `宽 ${((spanX / screenW) * 100).toFixed(0)}%，高 ${((spanY / screenH) * 100).toFixed(0)}%`,
  })

  // 6. No teleporting.
  let maxHop = 0, medHop = 0
  const hops: number[] = []
  for (let i = 1; i < log.length; i++) {
    const a = log[i].pos, b = log[i - 1].pos
    hops.push(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]))
  }
  hops.sort((a, b) => a - b)
  medHop = hops[Math.floor(hops.length / 2)]
  maxHop = hops[hops.length - 1]
  lines.push({
    name: '没有瞬移（单帧位移不突然远大于相邻帧）',
    pass: maxHop < Math.max(medHop * 6, 0.02),
    detail: `单步位移中位 ${(medHop * 1000).toFixed(2)} mm，最大 ${(maxHop * 1000).toFixed(2)} mm`,
  })

  return lines
}

function App() {
  const [mood, setMood] = useState<Mood>('cruise')
  const [report, setReport] = useState<ReturnType<typeof audit>>(null)
  const [recording, setRecording] = useState(false)
  const log = useRef<Telemetry[]>([])
  const recRef = useRef(false)
  recRef.current = recording

  const onTelemetry = useCallback((t: Telemetry) => {
    if (recRef.current) log.current.push(t)
  }, [])

  const record = () => {
    log.current = []
    setReport(null)
    setRecording(true)
    window.setTimeout(() => {
      setRecording(false)
      setReport(audit(log.current))
    }, 30000)
  }

  const dump = () => {
    const rows = ['t,mode,x,y,z,vx,vy,vz,speed,wingPhase,downstroke,accelY,fx,fy,fz,pitch,bank,headingErrDeg']
    for (const l of log.current) {
      rows.push([
        l.t.toFixed(4), l.mode, ...l.pos.map((v) => v.toFixed(5)), ...l.vel.map((v) => v.toFixed(5)),
        l.speed.toFixed(4), l.wingPhase.toFixed(4), l.downstroke.toFixed(5), l.accelY.toFixed(4),
        ...l.forward.map((v) => v.toFixed(4)), l.pitch.toFixed(4), l.bank.toFixed(4), l.headingErrorDeg.toFixed(2),
      ].join(','))
    }
    const url = URL.createObjectURL(new Blob([rows.join('\n')], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = '雾蝶飞行记录.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <>
      <SoloButterfly mood={mood} onTelemetry={onTelemetry} />
      <div className="solo-panel">
        <h1>单只雾蝶 · 飞行验飞</h1>
        <p className="solo-note">
          这是独立原型页，不接大厅、不接后端。翅膀几何、拍翼行程和身体质量都是启动时从模型量出来的。
        </p>
        <div className="solo-row">
          <button className={mood === 'cruise' ? 'on' : ''} onClick={() => setMood('cruise')}>待机巡游</button>
          <button className={mood === 'thinking' ? 'on' : ''} onClick={() => setMood('thinking')}>思考中</button>
        </div>
        <div className="solo-row">
          <button onClick={record} disabled={recording}>{recording ? '记录中… 30s' : '录 30 秒并自动验收'}</button>
          <button onClick={dump} disabled={!log.current.length}>导出运动记录 CSV</button>
        </div>
        {report && (
          <div className="solo-report">
            {report.map((r) => (
              <div key={r.name} className={r.pass ? 'ok' : 'bad'}>
                <span className="mark">{r.pass ? '通过' : '未达标'}</span>
                <span className="what">{r.name}</span>
                <span className="detail">{r.detail}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}

createRoot(document.getElementById('root')!).render(<StrictMode><Boundary><App /></Boundary></StrictMode>)
