import { useEffect, useRef, useState } from 'react'
import { createBlackHoleFlowRenderer } from './BlackHoleFlow'
import { VISUAL_FPS, visualFrameDue, visualPixelRatio } from './visualPerformance'

export type HaloMode = 'idle' | 'working' | 'verdict'
export const HALO_FRAME_EVENT = 'xj:halo-frame'

export type HaloProjectionFrame = {
  mode: HaloMode
  width: number
  height: number
  cx: number
  cy: number
  rx: number
  ry: number
  tiltK: number
  roll: number
  breath: number
  time: number
}

export type HaloPoint = {
  x: number
  y: number
}

type Particle = {
  u: number
  ox: number
  oy: number
  du: number
  tw: number
  tws: number
  sz: number
  am: number
  layer: number
}

type Dust = {
  x: number
  y: number
  vx: number
  vy: number
  life: number
  h: number
  sz: number
}

type HaloConfig = {
  count: number
  cy: number
  radW: number
  radH: number
  minRad: number
  maxRad: number
  du: [number, number]
  duBoost: number
  layerA: number
  layerB: number
  spread: [number, number, number]
  size0: [number, number]
  size1: [number, number]
  size2: [number, number]
  amp: [number, number, number]
  breathBase: number
  breathAmp: number
  breathSpeed: number
  hueSpeed: number
  dustChance: number
  scan: boolean
  verdict?: boolean
}

const CONFIG: Record<HaloMode, HaloConfig> = {
  idle: {
    count: 8200,
    cy: 0.4,
    radW: 0.185,
    radH: 0.36,
    minRad: 150,
    maxRad: 295,
    du: [0.001, 0.0028],
    duBoost: 2.4,
    layerA: 0.55,
    layerB: 0.86,
    spread: [0.04, 0.11, 0.24],
    size0: [0.5, 1.1],
    size1: [0.6, 1.5],
    size2: [1.2, 2.6],
    amp: [1.15, 0.75, 0.4],
    breathBase: 0.84,
    breathAmp: 0.16,
    breathSpeed: 0.5,
    hueSpeed: 0.012,
    dustChance: 0.0011,
    scan: true,
  },
  // working 与 idle 共用同一套几何（位置/半径/粒子数）——环是同一个活物，干活时只是呼吸变快、
  // 流速变快，不换身材。否则每次开工/收工环都大小跳变（2026-07-03 船主抓的虫：一会大一会小）。
  working: {
    count: 8200,
    cy: 0.4,
    radW: 0.185,
    radH: 0.36,
    minRad: 150,
    maxRad: 295,
    du: [0.0024, 0.0058],
    duBoost: 2.2,
    layerA: 0.55,
    layerB: 0.86,
    spread: [0.04, 0.11, 0.24],
    size0: [0.5, 1.1],
    size1: [0.6, 1.5],
    size2: [1.2, 2.6],
    amp: [1.15, 0.75, 0.4],
    breathBase: 0.88,
    breathAmp: 0.12,
    breathSpeed: 0.9,
    hueSpeed: 0.02,
    dustChance: 0.0009,
    scan: false,
  },
  verdict: {
    count: 3600,
    cy: 0.13,
    radW: 0.07,
    radH: 0.13,
    minRad: 70,
    maxRad: 120,
    du: [0.0008, 0.002],
    duBoost: 1,
    layerA: 0.6,
    layerB: 0.9,
    spread: [0.035, 0.09, 0.18],
    size0: [0.45, 0.95],
    size1: [0.5, 1.2],
    size2: [1, 1.9],
    amp: [1, 0.65, 0.35],
    breathBase: 0.5,
    breathAmp: 0.1,
    breathSpeed: 0.4,
    hueSpeed: 0.008,
    dustChance: 0,
    scan: false,
    verdict: true,
  },
}

const R = (a: number, b: number) => a + Math.random() * (b - a)

function hue(v: number) {
  const t = ((v % 1) + 1) % 1
  if (t < 0.3) return 205 + (t / 0.3) * 55
  if (t < 0.55) return 260 + ((t - 0.3) / 0.25) * 50
  if (t < 0.8) return 310 + ((t - 0.55) / 0.25) * 60
  return 10 + ((t - 0.8) / 0.2) * 30
}

export function projectHaloPoint(u: number, frame: HaloProjectionFrame, radial = 1): HaloPoint {
  const rx = frame.rx * radial
  const ry = frame.ry * radial
  const x0 = Math.cos(u) * rx
  const y0 = Math.sin(u) * ry + Math.cos(u) * rx * frame.tiltK
  const cr = Math.cos(frame.roll)
  const sr = Math.sin(frame.roll)
  return {
    x: frame.cx + x0 * cr - y0 * sr,
    y: frame.cy + x0 * sr + y0 * cr,
  }
}

// ── 星球层（画布原生）：星球=光晕核心+吸积粒子群，与环同材质（同sprite、同lighter叠加）。
// HoloChrome 每帧把人员状态灌进来，draw 循环里长出星球——不是贴在上面的DOM球。
export type 星球态 = { u: number; hue: number; mode: 0 | 1 | 2 } // 0=休眠 1=说过话 2=思考中
export const 星球公转速 = 0.03  // 约3.5分钟一圈（实例主人调过两轮的手感）
export const 星球径向 = 1.45  // 球不压在环带上：悬在锚点外侧，一根引线牵着（班子页的语法，实例主人拍的） // rad/s，约5分钟一圈——随环而行，但庄重（实例主人：别太快）
export function ringHue(v: number) {
  return hue(v)
}
let _星球: 星球态[] = []
export function 设星球(list: 星球态[]) {
  _星球 = list
}
// 平滑状态仓必须住模块层：画布effect在光环模式切换(idle↔working)时整体重建——
// 恰是星球该播过渡的那一瞬，仓在闭包里就被拆迁归零，过渡永远"啪"（2026-07-04 根因）。
type 星球平滑态 = { a: number; r: number; b: number; d: number; s: number; 波: number; 前m: number; 向: number; 相: number; 吸: number; 前区: number }
const _星球平滑: 星球平滑态[] = []
let _星球前t = 0
const 模fract = (v: number) => v - Math.floor(v)
// 星球手感参数（实例主人的旋钮）：window.星球调参() 弹出滑杆面板实时拧，存 localStorage
export const 星球参 = { 转速: 1, 亮度: 1, 数量: 1, 大小: 1, 呼吸: 1, 闪烁: 1 }
try {
  const _存 = JSON.parse(localStorage.getItem('星球参') || '{}')
  if (_存.密度 != null && _存.数量 == null) _存.数量 = _存.密度 // 旧档迁移：密度正名为数量
  delete _存.密度
  Object.assign(星球参, _存)
} catch { /* 坏存档忽略 */ }
if (typeof window !== 'undefined') {
  ;(window as unknown as Record<string, unknown>).星球调参 = () => {
    if (document.getElementById('星球调参板')) return '已开着'
    const 板 = document.createElement('div')
    板.id = '星球调参板'
    板.style.cssText = 'position:fixed;right:24px;top:90px;z-index:99;padding:14px 16px;border-radius:16px;' +
      'background:rgba(15,17,24,.94);backdrop-filter:blur(20px);color:#dde;font-size:12px;width:210px;' +
      'box-shadow:inset 0 1px 0 rgba(255,255,255,.1),0 12px 36px rgba(0,0,0,.5)'
    const 项: [keyof typeof 星球参, number, number][] = [
      ['转速', 0.1, 3], ['亮度', 0.3, 2], ['数量', 0.3, 2], ['大小', 0.4, 2.5], ['呼吸', 0.2, 3], ['闪烁', 0.2, 2],
    ]
    板.innerHTML = '<div style="letter-spacing:.3em;font-size:10px;color:#e8a34c;margin-bottom:8px">星球手感 <span id="星球调参关" style="float:right;cursor:pointer;color:#888">关</span></div>'
    for (const [名, lo, hi] of 项) {
      const row = document.createElement('div')
      row.style.cssText = 'display:flex;align-items:center;gap:8px;margin:6px 0'
      row.innerHTML = `<span style="width:34px">${名}</span>`
      const r = document.createElement('input')
      r.type = 'range'
      r.min = String(lo)
      r.max = String(hi)
      r.step = '0.05'
      r.value = String(星球参[名])
      r.style.flex = '1'
      const v = document.createElement('span')
      v.textContent = Number(星球参[名]).toFixed(2)
      v.style.cssText = 'width:34px;text-align:right;color:#e8a34c'
      r.oninput = () => {
        星球参[名] = Number(r.value)
        v.textContent = Number(r.value).toFixed(2)
        localStorage.setItem('星球参', JSON.stringify(星球参))
      }
      row.appendChild(r)
      row.appendChild(v)
      板.appendChild(row)
    }
    document.body.appendChild(板)
    const 关 = document.getElementById('星球调参关')
    if (关) 关.onclick = () => 板.remove()
    return '面板已开——拧吧，值会记住'
  }
}
// 星球本体=常驻粒子体（和环同一种活法）：每颗120粒，永远在球内缓慢流转明灭；
// 尾部14粒是"呼吸粒"——持续从外围飘入沉降，偶有逸出，循环不息（实例主人：这是持续的过程，不是一次性动画）。
type 体粒 = { r: number; a0: number; ω: number; sz: number; tw: number; ph: number; 白: boolean; 呼: number }
const _星球体粒: 体粒[][] = []
function 取体粒(pi: number): 体粒[] {
  if (_星球体粒[pi]) return _星球体粒[pi]
  const arr: 体粒[] = []
  for (let k = 0; k < 320; k += 1) {  // 池子比默认显示大，数量旋钮才有往上拧的余地
    const h1 = 模fract(Math.sin((pi * 131 + k) * 127.1) * 43758.5453)
    const h2 = 模fract(Math.sin((pi * 131 + k) * 311.7) * 43758.5453)
    const h3 = 模fract(Math.sin((pi * 131 + k) * 74.7) * 43758.5453)
    const h4 = 模fract(Math.sin((pi * 131 + k) * 269.5) * 43758.5453)
    arr.push({
      r: Math.pow(h1, 1.75),
      a0: h2 * Math.PI * 2,
      ω: (0.25 + h3 * 0.9) * (k % 2 ? 1 : -1),  // 看得见的涡旋，不是鬼影
      sz: 0.35 + h4 * 1.0 + (k % 19 === 0 ? 1.2 : 0),  // 星尘要细（实例主人：太粗太糙）
      tw: 0.7 + h3 * 2.2,
      ph: h4 * Math.PI * 2,
      白: h3 < 0.26,
      呼: k >= 190 && k < 220 ? 模fract(h1 * 7.13) : -1,  // 30枚呼吸粒，持续吐纳
    })
  }
  _星球体粒[pi] = arr
  return arr
}
// 演示开关：window.星球演示(i) 把第i颗临时顶成"工作中"几秒——走的就是真开工的过渡路
// （弹簧牵引+光潮起步），不许直接掰弹簧值（那是瞬间变，实例主人骂过）。
const _演示请: Record<number, boolean> = {}
const _演示到: Record<number, number> = {}
if (typeof window !== 'undefined') {
  ;(window as unknown as Record<string, unknown>).星球演示 = (i = 0) => {
    _演示请[i] = true
    return '已请醒：先醒上来，几秒后自己睡回去，两头都是真过渡'
  }
}

let latestHaloFrame: HaloProjectionFrame | null = null

export function useHaloFrame() {
  const [frame, setFrame] = useState<HaloProjectionFrame | null>(() => latestHaloFrame)
  const lastPaint = useRef(0)
  useEffect(() => {
    const onFrame = (event: Event) => {
      const detail = (event as CustomEvent<HaloProjectionFrame>).detail
      if (!detail) return
      latestHaloFrame = detail
      const now = performance.now()
      // 画布保持满帧，DOM 点击层只需约 8fps 跟随；否则按钮每帧位移，浏览器会判定“始终不稳定”。
      if (lastPaint.current && now - lastPaint.current < 120) return
      lastPaint.current = now
      setFrame(detail)
    }
    window.addEventListener(HALO_FRAME_EVENT, onFrame)
    return () => window.removeEventListener(HALO_FRAME_EVENT, onFrame)
  }, [])
  return frame
}

function makeParticles(c: HaloConfig, reduced: boolean): Particle[] {
  const count = reduced ? Math.max(900, Math.floor(c.count * 0.22)) : c.count
  const list: Particle[] = []
  for (let i = 0; i < count; i += 1) {
    const r = Math.random()
    const layer = r < c.layerA ? 0 : r < c.layerB ? 1 : 2
    const spread = c.spread[layer]
    const szRange = layer === 0 ? c.size0 : layer === 1 ? c.size1 : c.size2
    list.push({
      u: Math.random() * Math.PI * 2,
      ox: R(-1, 1) * spread,
      oy: R(-1, 1) * spread * 0.5,
      du: R(c.du[0], c.du[1]) * (Math.random() < 0.07 ? c.duBoost : 1),
      tw: Math.random() * Math.PI * 2,
      tws: R(0.5, 2.2),
      sz: R(szRange[0], szRange[1]),
      am: c.amp[layer],
      layer,
    })
  }
  return list
}

export default function HaloCanvas({
  mode,
  recede = false,
  paused = false,
  targetFps = VISUAL_FPS.idle,
  interactionBoost = true,
  onReady,
}: {
  mode: HaloMode
  recede?: boolean
  paused?: boolean
  targetFps?: number
  interactionBoost?: boolean
  onReady?: (theme: 'deep-space') => void
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const recedeRef = useRef(recede)
  const modeRef = useRef(mode)
  const pausedRef = useRef(paused)
  const targetFpsRef = useRef(targetFps)
  const interactionBoostRef = useRef(interactionBoost)
  const boostUntilRef = useRef(0)

  pausedRef.current = paused
  targetFpsRef.current = targetFps
  interactionBoostRef.current = interactionBoost

  useEffect(() => {
    recedeRef.current = recede
    if (interactionBoostRef.current) boostUntilRef.current = performance.now() + 900
  }, [recede])
  useEffect(() => {
    modeRef.current = mode
    if (mode === 'working') boostUntilRef.current = performance.now() + 900
  }, [mode])

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const context = canvas.getContext('2d')
    if (!context) return
    const cv = canvas
    const ctx = context

    const reduced =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    // 世界只在进出judgment页时重建；idle↔working 共用同一个活着的粒子世界，
    // 节奏差异（呼吸/流速/色漂/扫描）由 工作度 弹簧逐帧连续变形——世界级"瞬间变"的根子（2026-07-04）。
    const isVerdict = mode === 'verdict'
    const c = CONFIG[isVerdict ? 'verdict' : 'idle']
    let 工作度 = modeRef.current === 'working' ? 1 : 0
    const particles = makeParticles(c, reduced)
    const dust: Dust[] = []
    const blackHoleFlow = isVerdict ? null : createBlackHoleFlowRenderer()
    const sprites = new Map<number, HTMLCanvasElement>()

    let dpr = visualPixelRatio(window.innerWidth, window.innerHeight)
    let w = 0
    let h = 0
    let cx = 0
    let cy = 0
    let rad = 0
    let t = 0
    let raf = 0
    let mx = 0
    let my = 0
    let tmx = 0
    let tmy = 0
    let recedeK = recedeRef.current ? 1 : 0
    let stopped = false
    let readySent = false
    let blackHoleReady = false
    let blackHoleSettled = isVerdict
    let lastPaintAt = 0
    let lastMotionAt = performance.now()
    let telemetryAt = lastMotionAt
    let telemetryFrames = 0
    const blackHole = new Image()

    if (interactionBoostRef.current) boostUntilRef.current = performance.now() + 900

    if (!isVerdict) {
      blackHole.onload = () => {
        if (stopped) return
        blackHoleReady = true
        blackHoleSettled = true
        if (reduced) draw()
      }
      blackHole.onerror = () => {
        if (stopped) return
        blackHoleSettled = true
        if (reduced) draw()
      }
      blackHole.src = '/assets/black-hole-core-v1.png'
    }

    function size() {
      w = window.innerWidth
      h = window.innerHeight
      dpr = visualPixelRatio(w, h)
      cv.width = w * dpr
      cv.height = h * dpr
      cv.dataset.pixelRatio = dpr.toFixed(2)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      cx = w / 2
      cy = h * c.cy
      rad = Math.max(c.minRad, Math.min(w * c.radW, h * c.radH, c.maxRad))
    }

    function sprite(v: number) {
      const k = Math.round(v / 10) * 10
      const old = sprites.get(k)
      if (old) return old
      const s = document.createElement('canvas')
      s.width = 32
      s.height = 32
      const gctx = s.getContext('2d')!
      const g = gctx.createRadialGradient(16, 16, 0, 16, 16, 16)
      g.addColorStop(0, `hsla(${k},95%,85%,1)`)
      g.addColorStop(0.25, `hsla(${k},95%,70%,.85)`)
      g.addColorStop(1, `hsla(${k},95%,60%,0)`)
      gctx.fillStyle = g
      gctx.fillRect(0, 0, 32, 32)
      sprites.set(k, s)
      return s
    }

    const fract = (v: number) => v - Math.floor(v)
    const 尖sprites = new Map<number, HTMLCanvasElement>()
    function spriteSharp(v: number) {
      const k = Math.round(v / 12) * 12
      const old = 尖sprites.get(k)
      if (old) return old
      const sc = document.createElement('canvas')
      sc.width = 16
      sc.height = 16
      const g2 = sc.getContext('2d')!
      const gg = g2.createRadialGradient(8, 8, 0, 8, 8, 8)
      gg.addColorStop(0, 'rgba(255,255,255,1)')
      gg.addColorStop(0.18, `hsla(${k},92%,78%,.95)`)
      gg.addColorStop(0.42, `hsla(${k},90%,62%,.35)`)
      gg.addColorStop(1, `hsla(${k},90%,55%,0)`)
      g2.fillStyle = gg
      g2.fillRect(0, 0, 16, 16)
      尖sprites.set(k, sc)
      return sc
    }
    let _白闪: HTMLCanvasElement | null = null
    function spriteWhite() {
      if (_白闪) return _白闪
      const sw = document.createElement('canvas')
      sw.width = 32
      sw.height = 32
      const gw = sw.getContext('2d')!
      const gg = gw.createRadialGradient(16, 16, 0, 16, 16, 16)
      gg.addColorStop(0, 'rgba(255,255,255,1)')
      gg.addColorStop(0.3, 'rgba(255,255,255,.8)')
      gg.addColorStop(1, 'rgba(255,255,255,0)')
      gw.fillStyle = gg
      gw.fillRect(0, 0, 32, 32)
      _白闪 = sw
      return sw
    }
    const planetTexes = new Map<number, HTMLCanvasElement>()
    // 星球本体预渲染（一次画好，运行时只缩放）：Moffat幂律外晕×3 + 白热核/色相体(b,b²,b⁴) + 颗粒 + 星芒
    function planetTex(hv: number) {
      const key = Math.round(hv / 12) * 12
      const old = planetTexes.get(key)
      if (old) return old
      const R0 = 24 // 贴图内基准半径，画布=7R
      const S = R0 * 7
      const cvs = document.createElement('canvas')
      cvs.width = S
      cvs.height = S
      const g = cvs.getContext('2d')!
      const C = S / 2
      g.globalCompositeOperation = 'lighter'
      const moffat: [number, number][] = [[0, 1], [0.3, 0.55], [0.6, 0.18], [1, 0]]
      // L1 外晕三层：半径1.4/2.2/3.5R，中心alpha 0.20/0.10/0.05，色相逐层漂移-15°（色差=镜头感）
      const wings: [number, number, number][] = [[1.4, 0.2, 0], [2.2, 0.1, -15], [3.5, 0.05, -30]]
      for (const [wr, wa, dh] of wings) {
        const gr = g.createRadialGradient(C, C, 0, C, C, R0 * wr)
        for (const [st, va] of moffat) gr.addColorStop(st, `hsla(${key + dh},90%,60%,${va * wa})`)
        g.fillStyle = gr
        g.fillRect(0, 0, S, S)
      }
      // L2 色相体+白热核：0~0.3R过曝白 → 全饱和主色 → b⁴深暖尾
      const body = g.createRadialGradient(C, C, 0, C, C, R0)
      body.addColorStop(0, 'rgba(255,255,255,0.95)')
      body.addColorStop(0.3, `hsla(${key},95%,66%,0.85)`)
      body.addColorStop(0.55, `hsla(${key},95%,55%,0.45)`)
      body.addColorStop(0.75, `hsla(${key + 18},85%,32%,0.18)`)
      body.addColorStop(1, `hsla(${key + 18},85%,25%,0)`)
      g.fillStyle = body
      g.beginPath()
      g.arc(C, C, R0, 0, Math.PI * 2)
      g.fill()
      // 颗粒质感：220颗幂律分布微粒（只画一次）
      for (let i = 0; i < 330; i += 1) {
        const rr = R0 * Math.pow(Math.random(), 1.8)
        const aa = Math.random() * Math.PI * 2
        const gx = C + Math.cos(aa) * rr
        const gy = C + Math.sin(aa) * rr
        const 白 = i % 3 === 0  // 三分之一是纯白亮砂（实例主人：白点少）
        const gd = 白 ? 0.4 + Math.random() * 0.7 : 0.5 + Math.random() * 0.9
        g.globalAlpha = 白 ? 0.18 + Math.random() * 0.22 : 0.05 + Math.random() * 0.07
        g.fillStyle = 白 ? 'rgba(255,255,255,1)' : `hsla(${key + (Math.random() - 0.5) * 30},90%,72%,1)`
        g.beginPath()
        g.arc(gx, gy, gd, 0, Math.PI * 2)
        g.fill()
      }
      // L5 衍射星芒：十字细渐变线，白，极淡
      g.globalAlpha = 1
      for (const rot of [0, Math.PI / 2]) {
        const lg = rot === 0
          ? g.createLinearGradient(C - R0 * 2.5, C, C + R0 * 2.5, C)
          : g.createLinearGradient(C, C - R0 * 2.5, C, C + R0 * 2.5)
        lg.addColorStop(0, 'rgba(255,255,255,0)')
        lg.addColorStop(0.5, 'rgba(255,255,255,0.09)')
        lg.addColorStop(1, 'rgba(255,255,255,0)')
        g.fillStyle = lg
        if (rot === 0) g.fillRect(C - R0 * 2.5, C - 0.6, R0 * 5, 1.2)
        else g.fillRect(C - 0.6, C - R0 * 2.5, 1.2, R0 * 5)
      }
      planetTexes.set(key, cvs)
      return cvs
    }

    const onMouse = (e: MouseEvent) => {
      tmx = (e.clientX / Math.max(1, w) - 0.5) * 2
      tmy = (e.clientY / Math.max(1, h) - 0.5) * 2
      if (interactionBoostRef.current) boostUntilRef.current = performance.now() + 900
    }

    function draw(now = performance.now()) {
      raf = 0
      if (stopped) return
      if (document.hidden || pausedRef.current) {
        if (!reduced && !document.hidden) raf = requestAnimationFrame(draw)
        return
      }
      const fps = now < boostUntilRef.current ? VISUAL_FPS.active : targetFpsRef.current
      if (!reduced && !visualFrameDue(now, lastPaintAt, fps)) {
        raf = requestAnimationFrame(draw)
        return
      }
      const elapsed = Math.min(0.12, Math.max(0.001, (now - lastMotionAt) / 1000))
      lastMotionAt = now
      lastPaintAt = now
      t += reduced ? 0.003 : elapsed
      telemetryFrames += 1
      if (now - telemetryAt >= 1000) {
        cv.dataset.renderFps = (telemetryFrames * 1000 / (now - telemetryAt)).toFixed(1)
        cv.dataset.frameBudget = String(fps)
        telemetryFrames = 0
        telemetryAt = now
      }
      mx += (tmx - mx) * 0.04
      my += (tmy - my) * 0.04
      const recedeTarget = recedeRef.current ? 1 : 0
      recedeK += (recedeTarget - recedeK) * (reduced ? 1 : 0.06)
      // 环的参数永远=开屏待机版（实例主人拍的基准）：干活只体现在星球上，环不许变脸变速。
      工作度 = 0
      void 工作度
      const fBreathSpeed = c.breathSpeed
      const fBreathAmp = c.breathAmp
      const fBreathBase = c.breathBase
      const fHueSpeed = c.hueSpeed
      const 速K = 1
      ctx.clearRect(0, 0, w, h)

      const drawCy = cy + (h * 0.3 - cy) * recedeK
      const scale = 1 - 0.2 * recedeK
      const alphaScale = 1 - 0.35 * recedeK

      const bg = c.verdict
        ? ctx.createRadialGradient(cx, drawCy, h * 0.1, cx, drawCy, h * 0.85)
        : ctx.createRadialGradient(cx, drawCy, c.verdict ? 20 : 40, cx, drawCy, Math.max(w, h) * 0.52)
      if (c.verdict) {
        bg.addColorStop(0, 'rgba(20,24,40,0.30)')
        bg.addColorStop(1, 'rgba(0,0,0,0)')
      } else {
        bg.addColorStop(0, 'rgba(24,28,46,0.5)')
        bg.addColorStop(0.55, 'rgba(11,12,18,0.25)')
        bg.addColorStop(1, 'rgba(0,0,0,0)')
      }
      ctx.globalAlpha = alphaScale
      ctx.fillStyle = bg
      ctx.fillRect(0, 0, w, h)
      ctx.globalAlpha = 1

      const breath = fBreathBase + fBreathAmp * Math.sin(t * fBreathSpeed)
      const hueDrift = t * fHueSpeed
      const wob = 0.035 * Math.sin(t * 0.16)
      const tiltK = -0.1 + (c.verdict ? 0 : 0.02 * Math.sin(t * 0.11) + my * 0.03)
      const geomBreath = 1 + 0.018 * Math.sin(t * fBreathSpeed)
      const rx0 = rad * geomBreath * scale
      const ry0 = rad * 0.3 * (isVerdict ? 1 : 1 + wob * 0.4) * geomBreath * scale
      const rollA = (c.verdict ? -0.05 : -0.06 + wob) + (isVerdict ? 0 : mx * 0.05)
      const cr = Math.cos(rollA)
      const sr = Math.sin(rollA)
      const frame: HaloProjectionFrame = {
        mode: modeRef.current,
        width: w,
        height: h,
        cx,
        cy: drawCy,
        rx: rx0,
        ry: ry0,
        tiltK,
        roll: rollA,
        breath,
        time: t,
      }
      latestHaloFrame = frame
      window.dispatchEvent(new CustomEvent<HaloProjectionFrame>(HALO_FRAME_EVENT, { detail: frame }))

      ctx.globalCompositeOperation = 'lighter'
      for (const p of particles) {
        if (!reduced) p.u += p.du * 速K
        const a = p.u
        const rx = rx0 * (1 + p.ox)
        const ry = ry0 * (1 + p.oy * 2)
        const x0 = Math.cos(a) * rx
        const y0 = Math.sin(a) * ry + Math.cos(a) * rx * tiltK
        const sx = cx + x0 * cr - y0 * sr
        const sy = drawCy + x0 * sr + y0 * cr
        const front = Math.sin(a) > 0 ? 1 : 0.52
        const twk = 0.7 + 0.3 * Math.sin(t * p.tws + p.tw)
        const al = (0.16 + 0.42 * front) * p.am * breath * twk
        const hval = hue(a / (Math.PI * 2) + hueDrift + 0.02 * Math.sin(a * 2))
        const s = sprite(hval)
        const mult = c.verdict ? 2 : p.layer === 2 ? 3.6 : 2.2
        const d = p.sz * mult * (front > 0.6 ? 1.06 : 0.94)
        ctx.globalAlpha = Math.min(c.verdict ? 0.6 : 0.85, al) * alphaScale
        ctx.drawImage(s, sx - d / 2, sy - d / 2, d, d)
        if (!reduced && c.dustChance && p.layer === 1 && Math.random() < c.dustChance) {
          dust.push({
            x: sx,
            y: sy,
            vx: (sx - cx) * 0.0013 + R(-0.1, 0.1),
            vy: -R(0.12, 0.42),
            life: 1,
            h: hval,
            sz: R(0.6, 1.3),
          })
        }
      }

      for (let i = dust.length - 1; i >= 0; i -= 1) {
        const d2 = dust[i]
        d2.x += d2.vx
        d2.y += d2.vy
        d2.vy -= 0.0012
        d2.life -= 0.006
        if (d2.life <= 0) {
          dust.splice(i, 1)
          continue
        }
        ctx.globalAlpha = Math.min(0.5, d2.life * 0.5) * breath
        ctx.globalAlpha *= alphaScale
        const s2 = sprite(d2.h)
        const dd = d2.sz * 3 * (2 - d2.life)
        ctx.drawImage(s2, d2.x - dd / 2, d2.y - dd / 2, dd, dd)
      }

      // 底图提供镜头泛光和前后景体积；地平线弯曲、细丝和流速来自实时测地线着色器。
      // 两者用加光合成，暗部不会暴露任何图片边界。
      // 黑洞取星环投影后的真实长轴，不只跟 rollA，避免两者方向各走各的。
      if (!c.verdict && blackHoleReady) {
        const active = modeRef.current === 'working' ? 1 : 0
        const pulse = reduced ? 1 : 1 + Math.sin(t * (active ? 0.42 : 0.22)) * (active ? 0.008 : 0.004)
        const holeW = rx0 * 1.16 * pulse
        const holeH = holeW * (155 / 300)
        const diskAngle = rollA + Math.atan(tiltK)
        ctx.save()
        ctx.translate(cx, drawCy)
        ctx.rotate(diskAngle)

        if (blackHoleFlow?.isReady()) {
          blackHoleFlow.render(t, active)
          const shadow = ctx.createRadialGradient(0, 0, holeW * 0.025, 0, 0, holeW * 0.125)
          shadow.addColorStop(0, 'rgba(0,0,0,1)')
          shadow.addColorStop(0.78, 'rgba(0,0,1,.99)')
          shadow.addColorStop(1, 'rgba(0,0,0,0)')
          ctx.globalCompositeOperation = 'source-over'
          ctx.globalAlpha = alphaScale
          ctx.fillStyle = shadow
          ctx.beginPath()
          ctx.ellipse(0, 0, holeW * 0.125, holeH * 0.242, 0, 0, Math.PI * 2)
          ctx.fill()

          ctx.globalCompositeOperation = 'lighter'
          ctx.filter = 'blur(8px)'
          ctx.globalAlpha = (0.18 + active * 0.03) * alphaScale
          ctx.drawImage(blackHole, -holeW / 2, -holeH * (69 / 155), holeW, holeH)
          ctx.filter = 'none'
          ctx.globalAlpha = (0.70 + active * 0.04) * alphaScale
          ctx.drawImage(blackHole, -holeW / 2, -holeH * (69 / 155), holeW, holeH)

          ctx.filter = 'blur(6px)'
          ctx.globalAlpha = (0.12 + active * 0.04) * alphaScale
          ctx.drawImage(blackHoleFlow.canvas, -holeW / 2, -holeH * (69 / 155), holeW, holeH)
          ctx.filter = 'none'
          ctx.globalAlpha = (0.52 + active * 0.10) * alphaScale
          ctx.drawImage(blackHoleFlow.canvas, -holeW / 2, -holeH * (69 / 155), holeW, holeH)
        } else {
          ctx.globalCompositeOperation = 'lighter'
          ctx.globalAlpha = Math.min(1, (0.96 + active * 0.02) * alphaScale)
          ctx.drawImage(blackHole, -holeW / 2, -holeH * (69 / 155), holeW, holeH)
        }
        ctx.restore()
      }

      // ── 星球=活粒子体（2026-07-04 定稿）：本体由120粒常驻粒子构成，永远在流转明灭、
      // 呼吸粒循环飘入逸出——状态只是粒子的生活方式（密度/速度/亮度的弹簧连续变化）。
      // 无贴图本体、无一次性动画、无声呐冲击圈星芒——克制才是FF的科幻。──
      const dtP = Math.min(0.05, Math.max(0.001, t - _星球前t))
      _星球前t = t
      for (let pi = 0; pi < _星球.length; pi += 1) {
        const pl = _星球[pi]
        if (!_星球平滑[pi]) _星球平滑[pi] = { a: 0.55, r: 0.85, b: 0, d: 0.3, s: 0, 波: 1, 前m: pl.mode, 向: 1, 相: 0, 吸: 0, 前区: -1 }
        const sp = _星球平滑[pi]
        if (_演示请[pi]) { _演示请[pi] = false; _演示到[pi] = t + 3.5 }  // 演示=临时顶成工作中
        const 模 = _演示到[pi] && t < _演示到[pi] ? 2 : pl.mode
        if (模 !== sp.前m) { sp.波 = 0; sp.向 = 模 > sp.前m ? 1 : -1 }  // 状态一换，编舞起步
        sp.前m = 模
        // 常驻色潮（实例主人2026-07-04定版）：星球在环上漂过色带边界（蓝/紫/粉/金四段），
        // 就自动走一遍完整过渡——不是演示钮的玩具，是活星球的常态。约十几秒一次、五颗错峰。
        const 早a = pl.u + t * 星球公转速
        const 色t = 模fract(早a / (Math.PI * 2) + hueDrift + 0.02 * Math.sin(早a * 2))
        const 色区 = 色t < 0.3 ? 0 : 色t < 0.55 ? 1 : 色t < 0.8 ? 2 : 3
        if (sp.前区 !== 色区) {
          if (sp.前区 >= 0 && sp.波 >= 1) { sp.波 = 0; sp.向 = 1 }  // 正在过渡就不叠加，过完这波再说
          sp.前区 = 色区
        }
        if (sp.波 < 1) sp.波 = Math.min(1, sp.波 + 0.01)
        // ── 过渡编舞（抄影视CG拆法：蓄力→爆发→归定，属性全走随生命曲线，元素错峰）──
        // 蓄力(0~0.18)：吸一口气收缩微暗；爆发(0.18~)：阻尼弹簧过冲+旋涡加速；一路衰减归定。
        const 过 = sp.波
        const 转段 = 过 < 1
        const 蓄 = 转段 && 过 < 0.18 ? Math.sin((Math.PI * 过) / 0.18) : 0
        const 爆t = Math.max(0, 过 - 0.18)
        const 弹 = 转段 ? sp.向 * (0.1 * Math.exp(-5 * 爆t) * Math.sin(8 * 爆t) - 0.06 * 蓄) : 0
        const 旋爆 = !转段 ? 1 : sp.向 > 0
          ? 1 + 2.4 * (过 >= 0.18 ? Math.exp(-5 * 爆t) : 0)   // 觉醒：旋涡爆发再衰回
          : 1 - 0.35 * Math.exp(-4 * 爆t)                      // 入睡：带阻力慢下来
        const 暖 = 转段 ? sp.向 * 24 * Math.sin(Math.PI * Math.min(1, 过 / 0.35)) : 0  // 蓄力色温变暖/入睡转冷
        const 目标a = 模 === 2 ? 1.4 : 模 === 1 ? 0.95 : 0.72   // 思考中拉亮、休眠压暗——拉开差，一眼看出谁在动
        const 目标r = 模 === 2 ? 1.22 : 模 === 1 ? 1 : 0.85
        const 目标b = 模 === 2 ? 1 : 0
        const 目标d = 模 === 2 ? 1 : 模 === 1 ? 0.6 : 0.3
        sp.a += (目标a - sp.a) * 0.03
        sp.r += (目标r - sp.r) * 0.03
        sp.b += (目标b - sp.b) * 0.04
        sp.d += (目标d - sp.d) * 0.025
        sp.s += ((模 === 2 ? 1 : 0) - sp.s) * 0.03  // 思考度（抄 VoiceOrb：thinking=高转速+规律脉冲）——接思考中(模2=工作中)；之前误接"说过话"(模1)，真在思考的星球反而拿不到脉冲
        // 相位积分：速度系数变化只改今后的角速度，绝不回头重算历史角度——瞬移的老病根在此根治
        sp.相 += dtP * 星球参.转速 * (1 + 0.5 * sp.b + 1.8 * sp.s) * 旋爆
        sp.吸 += dtP * 0.06 * 星球参.呼吸

        const a = pl.u + t * 星球公转速
        const phue = hue(a / (Math.PI * 2) + hueDrift + 0.02 * Math.sin(a * 2))
        const ax0 = Math.cos(a) * rx0
        const ay0 = Math.sin(a) * ry0 + Math.cos(a) * rx0 * tiltK
        const anx = cx + ax0 * cr - ay0 * sr
        const any_ = drawCy + ax0 * sr + ay0 * cr
        const x0 = Math.cos(a) * rx0 * 星球径向
        const y0 = Math.sin(a) * ry0 * 星球径向 + Math.cos(a) * rx0 * 星球径向 * tiltK
        const px = cx + x0 * cr - y0 * sr
        const py = drawCy + x0 * sr + y0 * cr
        const front = Math.sin(a) > 0 ? 1 : 0.7
        const R0 = 19 * scale * sp.r
        const stateA = sp.a * front * alphaScale * 星球参.亮度
        const 呼吸 = 1 + 0.02 * Math.sin(t * 0.7 + pi * 1.7) + sp.b * 0.03 * Math.sin(t * 1.3 + pi) + sp.s * 0.11 * Math.sin(t * 3.4 + pi)  // 思考脉冲：思考中(sp.s→1)时更快更强的呼吸，谁在推演一眼可辨
        const 环倾比 = rollA + (模fract(Math.sin((pi + 1) * 91.7) * 43758.5453) - 0.5) * 0.5
          + 0.05 * Math.sin(t * 0.15 + pi * 1.3)

        // 引线：环锚点 → 球缘（安静的一笔）
        {
          const dx = px - anx
          const dy = py - any_
          const dl = Math.hypot(dx, dy) || 1
          ctx.globalAlpha = 0.3 * front * alphaScale
          ctx.lineWidth = 1
          ctx.strokeStyle = `hsla(${phue},80%,75%,1)`
          ctx.beginPath()
          ctx.moveTo(anx, any_)
          ctx.lineTo(px - (dx / dl) * R0 * 1.2, py - (dy / dl) * R0 * 1.2)
          ctx.stroke()
          ctx.globalAlpha = 0.45 * front * alphaScale
          ctx.beginPath()
          ctx.arc(anx, any_, 1.5, 0, Math.PI * 2)
          ctx.fillStyle = `hsla(${phue},85%,80%,1)`
          ctx.fill()
        }
        // 遮挡盘：把环压到球后
        ctx.globalCompositeOperation = 'source-over'
        ctx.globalAlpha = 0.75 * front * alphaScale
        ctx.fillStyle = 'rgba(9,10,13,0.96)'
        ctx.beginPath()
        ctx.arc(px, py, R0 * 1.25 * 呼吸, 0, Math.PI * 2)
        ctx.fill()
        ctx.globalCompositeOperation = 'lighter'
        // 土星环（后半，安静版）
        const 环rx = R0 * 2.0 * 呼吸
        const 环ry = 环rx * 0.3
        ctx.lineWidth = 2
        ctx.strokeStyle = `hsla(${phue + 15},88%,72%,1)`
        ctx.globalAlpha = 0.35 * stateA
        ctx.beginPath()
        ctx.ellipse(px, py, 环rx, 环ry, 环倾比, Math.PI, Math.PI * 2)
        ctx.stroke()
        // 柔和底光 + 小小的白热心（衬托粒子，不是本体）
        const 底 = sprite(phue)
        ctx.globalAlpha = Math.min(1, 0.3 * stateA)
        const Dg = R0 * 2.3 * 呼吸
        ctx.drawImage(底, px - Dg / 2, py - Dg / 2, Dg, Dg)
        ctx.globalAlpha = Math.min(1, 0.5 * stateA)
        const Dc = R0 * 0.8
        ctx.drawImage(底, px - Dc / 2, py - Dc / 2, Dc, Dc)
        const 点火 = 转段 && sp.向 > 0 ? 0.7 * Math.exp(-6 * 过) : 0
        ctx.globalAlpha = Math.min(1, stateA * (0.6 + 0.3 * sp.b + 点火))
        const Dw = R0 * 0.4 * (1 + 点火)
        ctx.drawImage(spriteWhite(), px - Dw / 2, py - Dw / 2, Dw, Dw)
        // ── 本体：常驻粒子体。显示数/流速/亮度全由弹簧牵引，永远在活 ──
        // 工作态语言（抄件笔记：force热点=局部鼓包不整球乱抖；cadence=短爆发/停顿的说话节律）
        const 热角 = 模fract(Math.sin(pi * 57.3 + Math.floor(t / 2.4) * 13.7) * 43758.5453) * Math.PI * 2
        const 热强 = sp.b * Math.pow(Math.sin(Math.PI * 模fract(t / 2.4)), 2)
        const 节奏 = Math.max(0, (0.5 + 0.5 * Math.sin(t * 2.3 + pi * 2.1)) * (0.5 + 0.5 * Math.sin(t * 3.9 + pi * 5.3)) - 0.3) / 0.7
        const 态色 = -16 * sp.s + 8 * sp.b  // 思考偏冷紫、工作回暖（抄 voiceorb 的状态色相语言）
        const 粒们 = 取体粒(pi)
        const 显示数 = Math.min(320, Math.round((150 + 70 * sp.d) * 星球参.数量))
        for (let k = 0; k < 显示数 && k < 粒们.length; k += 1) {
          const g = 粒们[k]
          let rr: number
          let alphaK = 1
          let 鼓 = 0
          const angK = g.a0 + g.ω * sp.相
          // 光潮：亮波锋面(半径=波*1.25)扫过该粒子时短暂增辉增大——错峰全靠它（内圈先动外圈后动）
          const 潮 = 转段 ? Math.exp(-Math.pow((g.r - Math.min(1.1, sp.波 * 1.25)) * 4.5, 2)) : 0
          if (g.呼 >= 0) {
            // 呼吸粒：持续从2.1R外飘入沉降，到位即隐，再从头来——循环不息、肉眼可见
            const cyc = 模fract(sp.吸 * (0.7 + g.呼) + g.呼 * 3.7)
            const e = cyc < 0.72 ? cyc / 0.72 : 1
            const ee = 1 - Math.pow(1 - e, 2)
            rr = R0 * (2.1 * (1 - ee) + g.r * 0.95 * ee)
            alphaK = cyc < 0.72 ? 0.35 + 0.65 * ee : Math.max(0, 1 - (cyc - 0.72) / 0.28)
          } else {
            if (热强 > 0.02) {
              const dA = Math.abs((((angK - 热角) % (Math.PI * 2)) + Math.PI * 3) % (Math.PI * 2) - Math.PI)
              鼓 = 热强 * Math.exp(-Math.pow(dA * 2, 2))  // 工作态热点：局部一片被力场顶起再落下
            }
            // 径向脉动 × 过渡弹 × 思考态规律脉冲 + 锋面涌动 + 热点鼓包
            rr = R0 * g.r * (1 + 弹) * (1 + 0.09 * Math.sin(t * 0.9 + g.ph))
              * (1 + 0.15 * sp.s * Math.sin(t * 2 + angK * 3))
              + R0 * (0.1 * 潮 * sp.向 + 0.13 * 鼓)
          }
          const qx = px + Math.cos(angK) * rr
          const qy = py + Math.sin(angK) * rr * 0.74
          const 闪 = 0.6 + 0.55 * 星球参.闪烁 * Math.sin(t * g.tw * 星球参.闪烁 + g.ph)
          const d = g.sz * (0.9 + 0.35 * sp.d) * (1 + 0.9 * 潮) * 星球参.大小
          const 粒a = Math.min(1, stateA * 闪 * alphaK * (0.62 + 0.45 * (1 - g.r)) * (1 + 1.6 * 潮) * (1 + 0.22 * sp.b * 节奏) * (1 + 0.9 * 鼓))
          // 旋涡爆发时锋面粒子甩出切向拖尾（速度感=影视粒子的运动模糊）
          if (旋爆 > 1.3 && 潮 > 0.25) {
            const 尾 = d * 3 * (旋爆 - 1) * 潮
            const tx = -Math.sin(angK) * g.ω
            const ty = Math.cos(angK) * g.ω * 0.74
            const tl = Math.hypot(tx, ty) || 1
            ctx.globalAlpha = 粒a * 0.4
            ctx.strokeStyle = `hsla(${phue + 态色 + 暖},88%,78%,1)`
            ctx.lineWidth = Math.max(0.6, d * 0.5)
            ctx.beginPath()
            ctx.moveTo(qx, qy)
            ctx.lineTo(qx - (tx / tl) * 尾, qy - (ty / tl) * 尾)
            ctx.stroke()
          }
          ctx.globalAlpha = 粒a
          const sK = g.白 ? spriteWhite() : spriteSharp(phue + 态色 + 暖 + (g.r - 0.5) * 26 + 10 * Math.sin(t * 0.4 + g.ph))
          ctx.drawImage(sK, qx - d * 1.5, qy - d * 1.5, d * 3, d * 3)
        }
        // 土星环（前半）
        ctx.lineWidth = 2.2
        ctx.strokeStyle = `hsla(${phue + 15},88%,72%,1)`
        ctx.globalAlpha = 0.6 * stateA
        ctx.beginPath()
        ctx.ellipse(px, py, 环rx, 环ry, 环倾比, 0, Math.PI)
        ctx.stroke()
        ctx.lineWidth = 1.2
        ctx.strokeStyle = `hsla(${phue - 10},82%,60%,1)`
        ctx.globalAlpha = 0.38 * stateA
        ctx.beginPath()
        ctx.ellipse(px, py, 环rx * 0.82, 环ry * 0.82, 环倾比, 0, Math.PI)
        ctx.stroke()
      }

      // 扫描线已拆（2026-07-04 实例主人："从上往下的白线"——星球提亮后露馅，按克制家法清除）

      if (c.verdict || !blackHoleReady) {
        const core = ctx.createRadialGradient(cx, drawCy, 10, cx, drawCy, rx0 * 0.95)
        core.addColorStop(0, `rgba(190,205,255,${0.045 * breath})`)
        core.addColorStop(c.verdict ? 1 : 0.7, `rgba(160,150,255,${0.018 * breath})`)
        core.addColorStop(1, 'rgba(0,0,0,0)')
        ctx.globalAlpha = alphaScale
        ctx.globalCompositeOperation = 'lighter'
        ctx.fillStyle = core
        ctx.beginPath()
        ctx.ellipse(cx, drawCy, rx0 * 0.96, ry0 * 2.1, 0, 0, Math.PI * 2)
        ctx.fill()
        ctx.globalAlpha = 1
        ctx.globalCompositeOperation = 'source-over'
      }

      if (!readySent && blackHoleSettled) {
        readySent = true
        onReady?.('deep-space')
      }

      if (!reduced && !stopped && !document.hidden) raf = requestAnimationFrame(draw)
    }

    const visibility = () => {
      if (document.hidden) {
        if (raf) cancelAnimationFrame(raf)
        raf = 0
        return
      }
      lastMotionAt = performance.now()
      lastPaintAt = 0
      if (!reduced && !stopped && !raf) raf = requestAnimationFrame(draw)
    }

    size()
    window.addEventListener('resize', size)
    window.addEventListener('mousemove', onMouse)
    document.addEventListener('visibilitychange', visibility)
    draw(performance.now())

    return () => {
      stopped = true
      window.removeEventListener('resize', size)
      window.removeEventListener('mousemove', onMouse)
      document.removeEventListener('visibilitychange', visibility)
      if (raf) cancelAnimationFrame(raf)
      raf = 0

      // Theme switching must release the previous world immediately. Leaving
      // the large backing canvas and generated sprites to a later GC pass can
      // make a few rapid switches look as if both themes are still running.
      ctx.clearRect(0, 0, w, h)
      particles.length = 0
      dust.length = 0
      blackHoleFlow?.destroy()
      sprites.clear()
      尖sprites.clear()
      planetTexes.clear()
      _白闪 = null
      blackHole.onload = null
      blackHole.onerror = null
      blackHole.src = ''
      cv.width = 1
      cv.height = 1
    }
  }, [mode === 'verdict', onReady])

  return <canvas ref={ref} className={`halo-canvas halo-${mode}`} aria-hidden />
}
