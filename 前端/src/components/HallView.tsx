// 大厅待命屏：只保留母版六类元素中的「问候语/对话字幕栈」和「底部指令行」。
// 导航、光环、引线标注、四角读数由 HoloChrome 承载。
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { 急停 } from '../actions'
import type { Refresh } from '../actions'
import { getJSON } from '../api'
import type { Hall, HallEvent, MeetingReplayLine, 健康 } from '../api'
import type { HallStore } from '../hallStore'
import HealthQuestionCard, { 有健康提问或回执 } from './HealthQuestionCard'
import { renderLightText } from './文本渲染'
import Worksite from './Worksite'
import { toast } from '../toast'

const 单附件上限 = 5 * 1024 * 1024
const 附件总上限 = 12 * 1024 * 1024
const 附件数上限 = 8

export default function HallView({
  store,
  hall,
  health,
  refresh,
  refreshHealth,
  成员,
  入聊,
  set入聊,
}: {
  store: HallStore
  hall: Hall | null
  health: 健康 | null
  refresh: Refresh
  refreshHealth: () => void
  成员: { 名: string; 岗: string }[]
  入聊: boolean
  set入聊: (b: boolean) => void
}) {
  const [text, setText] = useState('')
  const [附件, set附件] = useState<{ name: string; data: string; type: string; size: number }[]>([])
  const [艾特选中, set艾特选中] = useState(0)
  const [现场开, set现场开] = useState(false)  // 开工卡开合提到卡外保管：卡重装也不丢（点不进去的病根）
  const [会议开, set会议开] = useState<string | null>(null)  // 展开中的会议id：原地在流里展开，不再弹全屏浮窗（实例主人定论：二级菜单浮到整屏之上就离谱、插话都插不了）
  const [健康提问活跃, set健康提问活跃] = useState(() => 有健康提问或回执(health))
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  // @选人器：光标处最后一个 @ 后面还没敲空格 → 弹成员名单（点选或↑↓回车带入）
  const 艾特词 = (() => {
    const m = text.match(/@([^\s@]*)$/)
    return m ? m[1] : null
  })()
  const 艾特候选 =
    艾特词 !== null && !store.工作中
      ? 成员.filter((r) => !艾特词 || r.名.includes(艾特词) || r.岗.includes(艾特词))
      : []
  const 选艾特 = (名: string) => {
    setText((t) => t.replace(/@[^\s@]*$/, `@${名} `))
    set艾特选中(0)
    inputRef.current?.focus()
  }
  const stackRef = useRef<HTMLDivElement | null>(null)
  const stickBottom = useRef(true)
  const [有新消息, set有新消息] = useState(false)  // 药丸（抄Discord/Slack：上翻时来新东西不打扰，出个丸）
  const lastStackScrollTop = useRef(0)
  const dialogue = useDialogueStack(store, hall)
  const 健康状态变化 = useCallback((active: boolean) => {
    set健康提问活跃(active)
    if (active) set入聊(true)
  }, [set入聊])

  useEffect(() => {
    if (!有健康提问或回执(health)) return
    set健康提问活跃(true)
    set入聊(true)
  }, [health, set入聊])

  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 112) + 'px'
  }, [text])

  useEffect(() => {
    const el = stackRef.current
    if (!el || !stickBottom.current) return
    el.scrollTop = el.scrollHeight
  }, [dialogue])

  useEffect(() => {
    if (!健康提问活跃) return
    const el = stackRef.current
    if (!el) return
    const alignQuestion = () => {
      const question = el.querySelector<HTMLElement>('.health-question-line')
      if (!question) return
      // 问题卡可能比窄屏可视区高：先把问题本身的顶部交给用户，不能自动粘到卡底遮住标题。
      stickBottom.current = false
      set有新消息(false)
      el.scrollTop = question.offsetTop
    }
    const frame = requestAnimationFrame(alignQuestion)
    const timer = window.setTimeout(alignQuestion, 80)
    return () => {
      cancelAnimationFrame(frame)
      window.clearTimeout(timer)
    }
  }, [健康提问活跃])

  // 跟随的治本：盯内容高度而不是只盯消息数组——开工卡长出来/会议卡变高/打字机都算"有新东西"
  useEffect(() => {
    const el = stackRef.current
    const inner = el?.querySelector('.hall-dialogue-inner')
    if (!el || !inner) return
    const ro = new ResizeObserver(() => {
      if (stickBottom.current) el.scrollTop = el.scrollHeight
      else set有新消息(true)
    })
    ro.observe(inner)
    return () => ro.disconnect()
    // 依赖必须含 入聊：先加载后入聊时 stack 才出生，少了它观察者永远挂不上（2026-07-04体检抓虫）
  }, [入聊, dialogue.length === 0])

  const 读文件 = (f: File) =>
    new Promise<{ name: string; data: string; type: string; size: number }>((res, rej) => {
      const r = new FileReader()
      r.onload = () => res({ name: f.name, data: String(r.result), type: f.type, size: f.size })
      r.onerror = () => rej(r.error)
      r.readAsDataURL(f)
    })

  const 加文件 = async (fs: FileList | File[]) => {
    if (store.工作中) return
    const room = Math.max(0, 附件数上限 - 附件.length)
    const incoming = Array.from(fs)
    if (incoming.length > room) {
      toast(`附件最多 ${附件数上限} 个`, 'bad')
      return
    }
    const tooLarge = incoming.find((f) => f.size > 单附件上限)
    if (tooLarge) {
      toast(`${tooLarge.name} 超过 5MB`, 'bad')
      return
    }
    const total = 附件.reduce((n, f) => n + f.size, 0) + incoming.reduce((n, f) => n + f.size, 0)
    if (total > 附件总上限) {
      toast('附件合计不能超过 12MB', 'bad')
      return
    }
    const arr = incoming.slice(0, room)
    if (!arr.length) return
    const got = await Promise.all(arr.map(读文件))
    set附件((l) => [...l, ...got])
  }

  const send = () => {
    const v = text
    const files = 附件
    setText('')
    set附件([])
    // 发送那一刻回到最新并恢复跟随（实例主人：是回车发送才该到最下面）
    set入聊(true)
    set现场开(false)  // 新任务开始，上一场的现场浮层必须收掉（否则空壳浮层挂着）
    stickBottom.current = true
    set有新消息(false)
    const el = stackRef.current
    if (el) el.scrollTop = el.scrollHeight
    store.智能发送(v, files)
  }

  return (
    <main className="stage hall-stage">
      <div className={'idle-greeting' + (入聊 ? ' off' : '')} aria-label="问候语">
        晚上好，主人
      </div>
      {!入聊 || (dialogue.length === 0 && !健康提问活跃) ? null : (
        <div
          className={`hall-dialogue-stack${健康提问活跃 ? ' health-open' : ''}`}
          ref={stackRef}
          onWheel={(e) => {
            // 用户向上滚一格立刻松粘底（打字机撑高会拽底→距离又<80→粘底复咬的死循环，根治于此：
            // 程序滚动不算数，只有"用户的手"能松开/重新咬上）
            const el = e.currentTarget
            if (e.deltaY < 0) stickBottom.current = false
            else if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) { stickBottom.current = true; set有新消息(false) }
          }}
          onTouchMove={(e) => {
            const el = e.currentTarget
            if (el.scrollHeight - el.scrollTop - el.clientHeight >= 80) stickBottom.current = false
          }}
          onScroll={(e) => {
            // 跟随只看人在不在底部：在底就跟着新消息走，离底就让你安静看历史
            //（旧的 onWheel 太敏感——触控板轻微上滑/浮层滚动都会永久关掉跟随）
            const el = e.currentTarget
            const distance = el.scrollHeight - el.scrollTop - el.clientHeight
            const 往上了 = el.scrollTop < lastStackScrollTop.current   // 拖滚动条/键盘/惯性都只发 scroll 不发 wheel
            // 手往上、且已离底>8px 就松粘底（治"拖滚动条往上拖一点也被轮询咬回底"）；
            // 贴着底的 slice(-14) 滚旧内容会让 scrollTop 微降，distance≈0 不误判
            if (distance >= 80 || (往上了 && distance > 8)) stickBottom.current = false
            else set有新消息(false)
            lastStackScrollTop.current = el.scrollTop
          }}
          aria-label="大厅对话"
        >
          <div className="hall-dialogue-inner">
            {dialogue.map((e, i) => {
              const 键 = 分组键(e)
              const grouped = 键 !== null && i > 0 && 分组键(dialogue[i - 1]) === 键
              return (
                <DialogueLine e={e} grouped={grouped} store={store} 现场开={现场开} set现场开={set现场开} 会议开={会议开} set会议开={set会议开}
                  key={e.k === '工作现场' ? '工地' : (e.id || e.会议id || `${e.t || ''}-${e.who || ''}-${i}`)} />
              )
            })}
            <FlowStatus store={store} dialogue={dialogue} />
            <HealthQuestionCard health={health} refresh={refreshHealth} onActiveChange={健康状态变化} />
          </div>
          {有新消息 && (
            <button className="new-msg-pill" onClick={() => {
              stickBottom.current = true
              set有新消息(false)
              const el = stackRef.current
              if (el) el.scrollTop = el.scrollHeight
            }}>↓ 新消息</button>
          )}
        </div>
      )}
      {!健康提问活跃 && (
        <div className="health-question-probe" aria-hidden>
          <HealthQuestionCard health={health} refresh={refreshHealth} onActiveChange={健康状态变化} />
        </div>
      )}

      <div className="say command-line" aria-label="指令行">
        {附件.length > 0 && (
          <div className="command-attachments">
            {附件.map((f, i) => (
              <span className="attach-chip" key={i}>
                {f.name}
                <button onClick={() => set附件((l) => l.filter((_, j) => j !== i))}>X</button>
              </span>
            ))}
          </div>
        )}
        <div className="say-in">
          <textarea
            ref={inputRef}
            rows={1}
            value={text}
            onPaste={(e) => {
              const fs = e.clipboardData?.files
              if (fs && fs.length) {
                e.preventDefault()
                加文件(fs)
              }
            }}
            placeholder={store.工作中 ? '插话' : '对公司下达指令'}
            onFocus={() => set入聊(true)}  // 鼠标点到对话区即入聊天层（实例主人校准：不是等打字/发送）
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (艾特候选.length > 0) {
                if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  set艾特选中((i) => (i + 1) % 艾特候选.length)
                  return
                }
                if (e.key === 'ArrowUp') {
                  e.preventDefault()
                  set艾特选中((i) => (i - 1 + 艾特候选.length) % 艾特候选.length)
                  return
                }
                if ((e.key === 'Enter' || e.key === 'Tab') && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  选艾特(艾特候选[Math.min(艾特选中, 艾特候选.length - 1)].名)
                  return
                }
                if (e.key === 'Escape') {
                  set艾特选中(0)
                  setText((t) => t.replace(/@([^\s@]*)$/, '$1'))
                  return
                }
              }
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send()
              }
            }}
          />
          {艾特候选.length > 0 && (
            <div className="at-picker" role="listbox" aria-label="选人">
              {艾特候选.map((r, i) => (
                <button
                  key={r.名}
                  className={'at-item' + (i === 艾特选中 ? ' on' : '')}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    选艾特(r.名)
                  }}
                >
                  {r.名}
                  <small>{r.岗}</small>
                </button>
              ))}
            </div>
          )}
          <div className="command-hint">
            <button className="command-execute" onClick={send}>
              {store.工作中 ? '插话' : '执行'}
            </button>
            {store.工作中 && (
              <button className="command-stop" onClick={() => 急停(refresh)}>
                停止
              </button>
            )}
            <span>{store.工作中 ? '↵ 插话' : '↵ 执行 · @某人 单聊'}</span>
          </div>
        </div>
      </div>
    </main>
  )
}

function useDialogueStack(store: HallStore, hall: Hall | null) {
  return useMemo(() => {
    const temp = store.临时.filter((e) => (e.k === '话' && (e.text || e.流式)) || (e.k === '会议' && e.会议id) || e.k === '工作现场')
    const stable = (hall?.事件 ?? [])
      .filter((e) => ((e.k === '话' && (e.text || e.流式)) || (e.k === '会议' && e.会议id)) && 是当天(e))
    const stableTail = stable.slice(-20)
    const stableMeetings = new Set(stable.filter((e) => e.k === '会议' && e.会议id).map((e) => e.会议id))
    const dedupedTemp = temp.filter((e) => {
      if (e.k === '会议') return !stableMeetings.has(e.会议id)
      const text = 压缩空白(e.text || '')
      if (!text) return true
      // 去重窗口=2分钟内的同人同句（临时/持久真双份）。根因：临时气泡的t是"播放时刻"、
      // 持久记录的t是"说话时刻"，打字机一跨分钟"同一分钟"判据就漏（04:45说/04:46播完→双泡）。
      // 窗口2分钟既杀双份，又不吞实例主人隔半小时的原话重发。
      const eMin = 取分钟(e.t)
      return !stableTail.some((s) => {
        if ((s.who || '') !== (e.who || '') || 压缩空白(s.text || '') !== text) return false
        const sMin = 取分钟(s.t)
        if (eMin === null || sMin === null) return true
        return Math.abs(sMin - eMin) <= 2
      })
    })
    const combined = [...stable, ...dedupedTemp]
    // 本轮涉及开会（活人单元里有带会议id的）→会议tag就是这轮活动的表达，抑制那张只代表"PM在大厅编排"的开工/收工卡。
    // 用活人单元判(不是只看"活跃会议")：散会后活人单元仍在，收工卡才不会赖着和已结束会议tag并存——治船主"一个会俩标签说了几遍"。
    // 非会议的真干活：活人单元没有会议单元→照常出开工卡。
    const 本轮有会 = store.活人单元.some((u) => u.会议id)
    // 现场卡只在"工作中"实时显示（就在触发它的船主话下面）；收工后就撤——否则它作为临时元素会飘到全部持久记录之后、
    // 卡在最底部脱离原位（船主"收工跑到最下面不是开会时候的位置"）。会议本身是表达→有会议单元也撤。
    const 藏现场 = 本轮有会 || !store.工作中
    const 净 = 藏现场 ? combined.filter((e) => e.k !== '工作现场') : combined
    return 净.slice(-50)   // 14→50：一条发言常六七百字，14条就顶满屏、当天大半历史翻不上去（船主"大厅记录翻不上去"）
  }, [hall?.事件, store.临时, store.活人单元, store.工作中])
}

function 取分钟(t?: string): number | null {
  const m = (t || '').match(/(\d{1,2}):(\d{2})(?::\d{2})?\s*$/)
  return m ? Number(m[1]) * 60 + Number(m[2]) : null
}

function 压缩空白(s: string) {
  return s.replace(/\s+/g, '')
}

function 是当天(e: HallEvent) {
  const t = e.t || ''
  const d = t.slice(0, 10)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return true
  return d === localDateKey()
}

function localDateKey() {
  const d = new Date()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

function hhmm(t?: string): string {
  if (!t) return ''
  const m = t.match(/(\d{1,2}):(\d{2})/)
  return m ? `${m[1].padStart(2, '0')}:${m[2]}` : ''
}

function isOwner(e: HallEvent) {
  const who = e.who || ''
  return who.includes('船主') || who.includes('示例主人') || who === '你'
}

function speaker(e: HallEvent) {
  if (isOwner(e)) return '你'
  const who = e.who || ''
  if (!who || who === '公司' || who === '项目经理') return '老钟'
  return who
}

const 说话人色相: Array<[string, number]> = [
  ['老钟', 205],
  ['项目经理', 205],
  ['老梁', 164],
  ['首席工程师', 164],
  ['阿强', 112],
  ['阿言', 315],
  ['文案工程师（兼职）', 315],
  ['文案工程师', 315],
  ['老纪', 4],
  ['测试工程师', 4],
  ['工程师', 112],
]

function speakerHue(e: HallEvent) {
  const keys = [speaker(e), e.who || '', e.role || ''].filter(Boolean)
  const fullName = keys.join(' ')
  const fixed = 说话人色相.find(([name]) => fullName.includes(name))
  if (fixed) return fixed[1]
  return stableHue(keys[0] || '公司')
}

function stableHue(name: string) {
  let hue = 0
  for (const ch of name) hue = (hue * 31 + ch.charCodeAt(0)) % 360
  return hue
}

// 分组键：同一人连说才合并成一组（会议/工作现场卡不参与分组）
function 分组键(e: HallEvent): string | null {
  if (e.k === '会议' || e.k === '工作现场') return null
  if (isOwner(e)) return 'owner'
  return speaker(e) || '公司'
}
// 打字态用：从名字/岗位取本人星色
function hueForName(name: string, role?: string) {
  const full = [name, role || ''].join(' ')
  const f = 说话人色相.find(([n]) => full.includes(n))
  return f ? f[1] : stableHue(name || '公司')
}

function DialogueLine({ e, grouped, store, 现场开, set现场开, 会议开, set会议开 }: { e: HallEvent; grouped?: boolean; store: HallStore; 现场开: boolean; set现场开: (b: boolean) => void; 会议开: string | null; set会议开: (id: string | null) => void }) {
  if (e.k === '会议') return <MeetingTag e={e} store={store} 开={会议开 === e.会议id} onToggle={() => set会议开(会议开 === e.会议id ? null : (e.会议id || null))} />
  if (e.k === '工作现场') return <WorkCard store={store} 开={现场开} set开={set现场开} />
  const owner = isOwner(e)
  const name = speaker(e)
  const hue = owner ? undefined : speakerHue(e)
  const time = hhmm(e.t)
  const text = e.text || ''
  return (
    <div
      className={'dialogue-line ' + (owner ? 'owner' : 'company speaker-colored') + (grouped ? ' grouped' : '')}
      style={owner ? undefined : ({ '--spk': String(hue) } as CSSProperties)}
    >
      <div className="glass-msg">
        <div className="dialogue-meta">
          <span className="dialogue-speaker">{name}</span>{time ? <span className="dialogue-time">{time}</span> : null}
        </div>
        <div className="dialogue-text">
          {e.流式 ? text : renderLightText(text)}
          {e.流式 && <span className="dialogue-caret" aria-hidden />}
        </div>
      </div>
    </div>
  )
}

// 聊天流的活口：公司在忙时底部一条"谁在说…"打字态（在＝忙、消失＝完）；
// 公司说完最后一句且不忙时，一条"本轮结束·轮到你"分隔线，告诉船主没后续、该他了。
// 有开工卡（真在干活）时不出打字态，免得跟工作卡信息重复。
function FlowStatus({ store, dialogue }: { store: HallStore; dialogue: HallEvent[] }) {
  const units = store.活人单元.filter((u) => !u.会议id)
  const 干活 = units.filter((u) => !u.场合.startsWith('大厅'))
  if (store.工作中) {
    if (干活.length > 0) return null  // 工作卡已表达，别重复
    const 受邀 = units.filter((u) => u.场合 === '大厅受邀发言')
    if (store.公开回应总数 > 0) {
      const 已完成 = 受邀.filter((u) => u.状态 === '完成').length
      const 活跃受邀 = [...受邀].reverse().find((u) => u.状态 === '工作中')
      const name = 活跃受邀?.名字 || (已完成 >= store.公开回应总数 ? '大家' : '同事们')
      const hue = hueForName(name, 活跃受邀?.岗位)
      const verb = 已完成 >= store.公开回应总数 ? '回应完成' : '正在回应'
      return (
        <div className="dialogue-typing" style={{ '--spk': String(hue) } as CSSProperties}>
          <span className="typing-dot" />
          <span className="typing-name">{name}</span>&nbsp;{verb} · {已完成}/{store.公开回应总数}
          {已完成 < store.公开回应总数 && <span className="typing-beats"><i /><i /><i /></span>}
        </div>
      )
    }
    const 活跃 = [...units].reverse().find((u) => u.状态 === '工作中' && (u.发言 || u.思考 || (u.动作 && u.动作.length)))
      || [...units].reverse().find((u) => u.状态 === '工作中')
    const name = 活跃?.名字 || '公司'
    const hue = hueForName(name, 活跃?.岗位)
    const verb = !活跃 ? '正在回应'
      : 活跃.发言 ? '正在说'
      : (活跃.动作 && 活跃.动作.length) ? '正在查'
      : 活跃.思考 ? '正在想' : '正在回应'
    return (
      <div className="dialogue-typing" style={{ '--spk': String(hue) } as CSSProperties}>
        <span className="typing-dot" />
        <span className="typing-name">{name}</span>&nbsp;{verb}
        <span className="typing-beats"><i /><i /><i /></span>
      </div>
    )
  }
  // 不忙：公司说完了最后一句 → 一轮结束、轮到你
  const lastMsg = [...dialogue].reverse().find((e) => e.k !== '会议' && e.k !== '工作现场' && e.text != null)
  if (lastMsg && !isOwner(lastMsg)) {
    return <div className="turn-end"><span>本轮结束 · 轮到你</span></div>
  }
  return null
}

// 开工卡：干活时长在聊天流里的毛玻璃（实例主人：现场不是窗口，是流里一张会呼吸的卡）。
// 折叠=一行粗略实时（几人在动/最新动态）；点开=完整现场树（Worksite）。纯聊天不出卡（星球层管）。
function WorkCard({ store, 开, set开 }: { store: HallStore; 开: boolean; set开: (b: boolean) => void }) {
  const units = store.活人单元.filter((u) => !u.会议id)
  const 干活单元 = units.filter((u) => !u.场合.startsWith('大厅'))
  if (干活单元.length === 0) return null
  const 活跃 = 干活单元.filter((u) => u.状态 === '工作中')
  const 最新 = [...干活单元].reverse().find((u) => u.发言 || u.思考 || u.动作.length)
  const 动态 = 最新
    ? `${最新.名字}：${(最新.发言 || (最新.动作[最新.动作.length - 1]?.目标 ?? '') || 最新.思考 || '正在琢磨').slice(-60)}`
    : '开工中…'
  return (
    <div className="dialogue-line company">
      <div className="workcard-shell">
        <button className="workcard-bar" onClick={() => set开(!开)}>
          <span className={活跃.length ? 'workcard-dot on' : 'workcard-dot'} />
          <span className="workcard-title">{活跃.length ? '开工' : '收工'} · {干活单元.length}人</span>
          <span className="workcard-live">{活跃.length ? 动态 : '这单收完了，点开可回看现场。'}</span>
          <span className="workcard-arrow">{开 ? '收起' : '看现场'}</span>
        </button>
        {开 && (
          <div className="workcard-inline">
            <Worksite units={干活单元} runCard={store.runCard} 工作中={store.工作中} />
          </div>
        )}
      </div>
    </div>
  )
}

function MeetingTag({ e, store, 开, onToggle }: { e: HallEvent; store: HallStore; 开: boolean; onToggle: () => void }) {
  const status = e.状态 || '已结束'
  const running = status !== '已结束'
  const people = (e.参会 || []).filter(Boolean)
  const line = running && e.当前发言人 ? `${e.当前发言人}正在发言…` : running ? '会议进行中' : '会议已归档，可回看'
  // 原地展开（实例主人定论：不许弹全屏浮窗）——照开工卡：标签是折叠条，点开在流里向下长出会场，再点收起，输入框全程可用。
  return (
    <div className="meeting-shell">
      <button className={'meeting-tag ' + (running ? 'live' : 'done')} onClick={onToggle}>
        <span className="meeting-tag-dot" />
        <span className="meeting-tag-main">
          <b>{e.主题 || cleanMeetingTitle(e.text) || '会议'}</b>
          <small>{line}</small>
        </span>
        <span className="meeting-tag-side">
          {people.length ? people.slice(0, 4).join(' / ') : '会场'}
          {people.length > 4 ? ` +${people.length - 4}` : ''}
        </span>
        <span className="meeting-tag-arrow">{开 ? '收起' : running ? '看现场' : '看回看'}</span>
      </button>
      {开 && (
        <div className="meeting-inline">
          <MeetingBody meeting={e} store={store} />
        </div>
      )}
    </div>
  )
}

function cleanMeetingTitle(text?: string) {
  return (text || '').replace(/^\[会议\]\s*/, '').trim()
}

function MeetingBody({ meeting, store }: { meeting: HallEvent; store: HallStore }) {
  const [text, setText] = useState('')
  const [replay, setReplay] = useState<MeetingReplayLine[] | null>(null)
  const [loading, setLoading] = useState(false)
  const ended = meeting.状态 === '已结束' || !store.工作中
  const units = store.活人单元.filter((u) => u.会议id === meeting.会议id)

  useEffect(() => {
    if (!ended || !meeting.会议id) return
    setLoading(true)
    getJSON<{ 记录: MeetingReplayLine[] }>('/meeting_replay?id=' + encodeURIComponent(meeting.会议id))
      .then((r) => setReplay(r.记录 || []))
      .catch(() => setReplay([]))
      .finally(() => setLoading(false))
  }, [ended, meeting.会议id])

  const submit = async () => {
    const v = text.trim()
    if (!v) return
    setText('')
    await store.插话(v)
  }

  if (ended) {
    return (
      <div className="meeting-replay">
        {loading && <div className="meeting-muted">正在读取会议记录…</div>}
        {!loading && (replay || []).length === 0 && <div className="meeting-muted">没有读到这场会议的回看记录。</div>}
        {(replay || []).map((line, i) => (
          <div className={'meeting-replay-line ' + (line.类型 === '开会' ? 'open' : '')} key={`${line.时间 || ''}-${line.who || ''}-${i}`}>
            <span>{hhmm(line.时间)}</span>
            <b>{line.who || '会议室'}</b>
            <div className="meeting-replay-text">{renderLightText(line.text || '')}</div>
          </div>
        ))}
      </div>
    )
  }
  return (
    <>
      <Worksite units={units} runCard={store.runCard} 工作中={store.工作中} showMeetingUnits />
      <div className="meeting-interject">
        <textarea
          value={text}
          rows={2}
          placeholder="插话进会场"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              submit()
            }
          }}
        />
        <button onClick={submit}>插话</button>
      </div>
    </>
  )
}
