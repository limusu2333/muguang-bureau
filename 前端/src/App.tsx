import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from 'react'
import { getJSON } from './api'
import type { Board, Hall, 健康 } from './api'
import { usePoll, useVersionedPoll } from './hooks'
import { useHallStore } from './hallStore'
import { ToastHost } from './toast'
import HallView from './components/HallView'
import MemoryView from './components/MemoryView'
import SettingsView from './components/SettingsView'
import { CrewView, DecisionsView, ManageConfirmModal } from './components/Pages'
import ScenesView from './components/ScenesView'
import HallWorkspace from './components/HallWorkspace'
import Room from './components/Room'
import { HoloChrome } from './components/HoloChrome'
import type { HaloMode } from './components/HaloCanvas'
import MistAtmosphere from './components/MistAtmosphere'
import RuntimeVersionBadge from './components/RuntimeVersionBadge'
import { readVisualTheme, saveVisualTheme, type VisualTheme } from './theme'

export type View = {
  t: 'hall' | 'room' | 'scenes' | 'day' | 'crew' | 'decisions' | 'memory' | 'settings'
  id?: string
  date?: string
  focus?: string
}

function 读视图(): View {
  try {
    const v = JSON.parse(localStorage.getItem('xj_view') || 'null')
    if (
      v &&
      ['hall', 'room', 'scenes', 'day', 'crew', 'decisions', 'memory', 'settings'].includes(v.t)
    ) {
      return v.t === 'scenes' ? { t: 'hall' } : v
    }
  } catch {
    /* 忽略坏缓存 */
  }
  return { t: 'hall' }
}

function 待拍板数(board: Board | null): number {
  const t = board?.待办
  return (
    (t?.待确认?.length ?? 0) +
    (t?.待裁决?.length ?? 0) +
    (t?.待办?.length ?? 0) +
    (t?.待验收?.length ?? 0) +
    (t?.活公司待验收?.length ?? 0)
  )
}

function 当天日期() {
  const d = new Date()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

function 是今天(t?: string) {
  if (!t) return true
  const d = t.slice(0, 10)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return true
  return d === 当天日期()
}

type VisualPhase = 'waiting' | 'idle' | 'covering' | 'revealing'
type ViewTransitionPhase = 'idle' | 'entering'

const VISUAL_COVER_MS = 320
const VISUAL_REVEAL_MS = 640
const VISUAL_READY_TIMEOUT_MS = 7000
const VIEW_ENTER_MS = 180

function 减少动态效果() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function VisualCurtain({
  phase,
  theme,
  intent,
}: {
  phase: VisualPhase
  theme: VisualTheme
  intent: 'boot' | 'theme'
}) {
  const label = intent === 'boot' ? '船主的窗户' : theme === 'mist' ? '雾中醒来' : '深空'
  return (
    <div
      className={`visual-curtain phase-${phase} visual-to-${theme}`}
      aria-hidden={phase === 'idle'}
      data-phase={phase}
      data-target-theme={theme}
    >
      <div className="visual-curtain-copy">
        <span>MUNAEA</span>
        <i aria-hidden><b /></i>
        <small>{label}</small>
      </div>
    </div>
  )
}

export default function App() {
  const [view, _setView] = useState<View>(读视图)
  const [viewTransitionPhase, setViewTransitionPhaseState] = useState<ViewTransitionPhase>('idle')
  const [, startTransition] = useTransition()
  const [theme, setThemeState] = useState<VisualTheme>(readVisualTheme)
  const [visualPhase, setVisualPhaseState] = useState<VisualPhase>('waiting')
  const [curtainTheme, setCurtainTheme] = useState<VisualTheme>(theme)
  const [curtainIntent, setCurtainIntent] = useState<'boot' | 'theme'>('boot')
  const themeRef = useRef(theme)
  const expectedThemeRef = useRef(theme)
  const phaseRef = useRef<VisualPhase>('waiting')
  const switchingRef = useRef(false)
  const coverTimerRef = useRef<number | null>(null)
  const revealTimerRef = useRef<number | null>(null)
  const readyTimeoutRef = useRef<number | null>(null)
  const viewRef = useRef(view)
  const viewEnterTimerRef = useRef<number | null>(null)

  const setViewTransitionPhase = useCallback((phase: ViewTransitionPhase) => {
    setViewTransitionPhaseState(phase)
  }, [])

  const setVisualPhase = useCallback((phase: VisualPhase) => {
    phaseRef.current = phase
    setVisualPhaseState(phase)
  }, [])

  const onVisualReady = useCallback((readyTheme: VisualTheme) => {
    if (readyTheme !== expectedThemeRef.current || phaseRef.current !== 'waiting') return
    if (readyTimeoutRef.current !== null) window.clearTimeout(readyTimeoutRef.current)
    readyTimeoutRef.current = null
    setVisualPhase('revealing')
    const revealMs = 减少动态效果() ? 90 : VISUAL_REVEAL_MS
    revealTimerRef.current = window.setTimeout(() => {
      revealTimerRef.current = null
      switchingRef.current = false
      setVisualPhase('idle')
    }, revealMs)
  }, [setVisualPhase])

  const setTheme = useCallback((next: VisualTheme) => {
    if (next === themeRef.current || switchingRef.current) return
    switchingRef.current = true
    expectedThemeRef.current = next
    setCurtainTheme(next)
    setCurtainIntent('theme')
    setVisualPhase('covering')
    if (coverTimerRef.current !== null) window.clearTimeout(coverTimerRef.current)
    if (revealTimerRef.current !== null) window.clearTimeout(revealTimerRef.current)
    if (readyTimeoutRef.current !== null) window.clearTimeout(readyTimeoutRef.current)
    const coverMs = 减少动态效果() ? 30 : VISUAL_COVER_MS
    coverTimerRef.current = window.setTimeout(() => {
      coverTimerRef.current = null
      themeRef.current = next
      setThemeState(next)
      saveVisualTheme(next)
      setVisualPhase('waiting')
      readyTimeoutRef.current = window.setTimeout(() => onVisualReady(next), VISUAL_READY_TIMEOUT_MS)
    }, coverMs)
  }, [onVisualReady, setVisualPhase])

  useEffect(() => {
    const preboot = document.getElementById('preboot')
    const handoffFrame = window.requestAnimationFrame(() => preboot?.classList.add('handoff'))
    const handoffTimer = window.setTimeout(() => preboot?.remove(), 220)
    if (phaseRef.current === 'waiting') {
      readyTimeoutRef.current = window.setTimeout(
        () => onVisualReady(expectedThemeRef.current),
        VISUAL_READY_TIMEOUT_MS,
      )
    }
    return () => {
      window.cancelAnimationFrame(handoffFrame)
      window.clearTimeout(handoffTimer)
      if (coverTimerRef.current !== null) window.clearTimeout(coverTimerRef.current)
      if (revealTimerRef.current !== null) window.clearTimeout(revealTimerRef.current)
      if (readyTimeoutRef.current !== null) window.clearTimeout(readyTimeoutRef.current)
      if (viewEnterTimerRef.current !== null) window.clearTimeout(viewEnterTimerRef.current)
    }
  }, [onVisualReady])
  const setView = useCallback((v: View) => {
    const current = viewRef.current
    const changed = current.t !== v.t || current.id !== v.id || current.date !== v.date || current.focus !== v.focus
    if (!changed) return

    viewRef.current = v
    localStorage.setItem('xj_view', JSON.stringify(v))
    if (viewEnterTimerRef.current !== null) window.clearTimeout(viewEnterTimerRef.current)
    if (减少动态效果()) {
      _setView(v)
      setViewTransitionPhase('idle')
      return
    }

    setViewTransitionPhase('entering')
    startTransition(() => _setView(v))
    viewEnterTimerRef.current = window.setTimeout(() => {
      viewEnterTimerRef.current = null
      setViewTransitionPhase('idle')
    }, VIEW_ENTER_MS)
  }, [setViewTransitionPhase, startTransition])

  // 任务进行中暂停 /hall 轮询：进行中只信实时「工作现场」，避免持久态(会议气泡)和实时态打架
  // ——根治"卡往下跑"和会议发言双份。任务结束后恢复，读永久记录。
  const [暂停hall, set暂停hall] = useState(false)
  // 开屏=常驻标题层（实例主人：打开永远在欢迎那里，打字才进聊天）——与有无历史无关
  const [入聊, set入聊] = useState(false)
  const [board, refreshBoard, 连着] = useVersionedPoll<Board>(
    () => getJSON('/board'),
    async () => (await getJSON<{ version: string }>('/board-version')).version,
    2500,
  )
  const [health, refreshHealth] = usePoll<健康>(() => getJSON('/健康'), 45000)
  const [hall, refreshHall] = usePoll<Hall>(
    () => getJSON('/hall'),
    2500,
    (view.t === 'hall' || view.t === 'day') && !暂停hall,
  )
  const [dayHall] = usePoll<Hall>(
    () => getJSON('/hall_day?date=' + encodeURIComponent(view.t === 'day' ? (view.date || '') : '')),
    10000,
    view.t === 'day',
  )
  const store = useHallStore(refreshHall, refreshBoard, set暂停hall)
  const refreshAll = useCallback(() => {
    refreshBoard()
    refreshHall()
  }, [refreshBoard, refreshHall])
  const pending = 待拍板数(board)
  const mode: HaloMode =
    store.工作中 ? 'working' : view.t === 'decisions' && pending > 0 ? 'verdict' : 'idle'
  const hasHallDialogue = useMemo(() => {
    const temp = store.临时.some((e) => e.k === '话' && (e.text || e.流式))
    const stable = (hall?.事件 ?? []).some((e) => e.k === '话' && 是今天(e.t) && (e.text || e.流式))
    return temp || stable
  }, [hall?.事件, store.临时])
  // 记忆库是阅读/管理页：环退后让位（同大厅有对话时），内容不跟活物打架
  const haloRecede = (view.t === 'hall' && 入聊) || view.t === 'day' || view.t === 'memory'

  // 项目室不存在了（被作废/重置）→ 自动退回大厅
  useEffect(() => {
    if (view.t === 'room' && board?.项目 && !board.项目.some((p) => p.id === view.id)) {
      setView({ t: 'hall' })
    }
  }, [board, view, setView])

  return (
    <div className={`holo-root mode-${mode} view-${view.t} theme-${theme}`} data-theme={theme}>
      {theme === 'mist' ? <MistAtmosphere /> : null}
      <HoloChrome
        mode={mode}
        recede={haloRecede}
        连着={连着}
        board={board}
        view={view}
        setView={setView}
        units={store.活人单元}
        health={health}
        theme={theme}
        setTheme={setTheme}
        onVisualReady={onVisualReady}
      />
      <div id="app" className={`view-transition-stage phase-${viewTransitionPhase}`}>
        {view.t === 'hall' || view.t === 'day' ? (
          <HallWorkspace hall={hall} board={board} view={view} setView={setView}>
            {view.t === 'hall' ? (
              <HallView store={store} hall={hall} health={health} refresh={refreshAll} refreshHealth={refreshHealth} 入聊={入聊} set入聊={set入聊}
                成员={(board?.成员 ?? []).map((m) => ({ 名: (m as { 名字?: string }).名字 || m.title || '', 岗: m.title || '' })).filter((r) => r.名)} />
            ) : (
              <ScenesView hall={hall} dayEvents={dayHall?.事件 ?? []} view={view} setView={setView} />
            )}
          </HallWorkspace>
        ) : view.t === 'crew' ? (
          <CrewView board={board} store={store} health={health} refresh={refreshAll} />
        ) : view.t === 'memory' ? (
          <MemoryView />
        ) : view.t === 'settings' ? (
          <SettingsView theme={theme} setTheme={setTheme} />
        ) : view.t === 'decisions' ? (
          <DecisionsView board={board} setView={setView} refresh={refreshAll} />
        ) : (
          <Room view={view} setView={setView} refresh={refreshAll} continueInHall={(id, title, text) => {
            setView({ t: 'hall' })
            set入聊(true)
            void store.智能发送(text, [], { room: id, title })
          }} />
        )}
      </div>

      <ManageConfirmModal board={board} refresh={refreshAll} />
      <ToastHost />
      <RuntimeVersionBadge />
      <VisualCurtain phase={visualPhase} theme={curtainTheme} intent={curtainIntent} />
    </div>
  )
}
