// 大厅交互核心：发送 → /活厅 唤醒项目经理本人 → /stream 看公司工作现场。
// 公司工作中：说话即插话(/插话)。点名：/rollcall SSE。
// 临时事件 = 发送中的乐观气泡/流式打字机气泡/分割线；落盘后由正式 /hall 接管、清掉临时。
import { useEffect, useRef, useState } from 'react'
import { getJSON, postJSON } from './api'
import type { HallEvent, 活人单元 } from './api'
import { toast } from './toast'

export type RunCard = {
  kind: '活厅' | '点名'
  running: boolean
  lines: string[]
  start: number
  end?: number
  当前?: string
  轮?: number
}

export const __会议标签演示: HallEvent[] = [
  { k: '会议', 会议id: 'demo-meeting', 主题: '演示会议标签', 状态: '进行中', 参会: ['老梁', '老纪'], 当前发言人: '老梁', t: '20:10', 开始: 0 },
  { k: '会议', 会议id: 'demo-meeting', 主题: '演示会议标签', 状态: '已结束', 参会: ['老梁', '老纪'], t: '20:12', 开始: 0, 结束: 120000 },
]

const 现在 = () => new Date().toTimeString().slice(0, 5)

export function useHallStore(
  refreshHall: () => void,
  refreshBoard: () => void,
  set暂停hall?: (b: boolean) => void,
) {
  const [临时, set临时] = useState<HallEvent[]>([])
  const [runCard, setRunCard] = useState<RunCard | null>(null)
  const [工作中, set工作中] = useState(false)
  const [活人单元, set活人单元] = useState<活人单元[]>([])  // 工作现场：这次任务里每个活起来工作的人
  const [公开回应总数, set公开回应总数] = useState(0)
  const 根单元id = useRef<string>('')   // PM（父=None）那个单元——它的最终发言进播放队列走大厅气泡
  const 根名字 = useRef<string>('')
  const 单元会议 = useRef<Record<string, { 会议id: string; 名字: string }>>({})
  const sidRef = useRef<string | null>(null)
  const [, setTick] = useState(0)
  // 播放队列：到货的发言排队上打字机，一次只有一个人在"说"（真人群聊不会五个人同帧刷屏）。
  // 队列放空 + 屏上没人在打字 + 待恢复 → 才恢复 /hall 轮询（否则持久态一把全刷出来，播放就穿帮了）。
  const 播放队列 = useRef<{ who: string; 目标: string; t?: string }[]>([])
  const 已播 = useRef<Set<string>>(new Set())  // SSE重连会整场重放事件——同一句只许进一次队列（04:37双泡虫）
  const 待恢复 = useRef(false)
  const 临时Ref = useRef<HallEvent[]>([])
  useEffect(() => {
    临时Ref.current = 临时
  }, [临时])

  // runCard 运行中：每 500ms 触发一次重渲染，刷新秒表
  useEffect(() => {
    if (!runCard?.running) return
    const id = setInterval(() => setTick((t) => t + 1), 500)
    return () => clearInterval(id)
  }, [runCard?.running])

  // 匀速打字机 + 播放泵：后端到货的发言先进播放队列；屏上没人在打字时才放下一个上来，
  // 一个说完再蹦下一个（视觉规则：打字机人人平等、一次一个）。全播完且任务已结束 → 恢复 /hall。
  useEffect(() => {
    const id = setInterval(() => {
      const 屏上 = 临时Ref.current
      const 有流式 = 屏上.some((e) => e.流式)
      if (有流式) {
        set临时((l) => {
          let 改 = false
          const n = l.map((e) => {
            if (!e.流式) return e
            const 目标 = e.目标 || ''
            const 显 = e.text || ''
            if (显.length < 目标.length) {
              改 = true
              const step = Math.min(4, Math.max(1, Math.ceil((目标.length - 显.length) / 10)))
              return { ...e, text: 目标.slice(0, 显.length + step) }
            }
            if (e.收尾 && 显.length >= 目标.length) {
              改 = true
              const { 流式, 收尾, 目标: _t, ...rest } = e
              void 流式
              void 收尾
              void _t
              return rest
            }
            return e
          })
          return 改 ? n : l
        })
        return
      }
      const next = 播放队列.current.shift()
      if (next) {
        set临时((l) => [...l, { k: '话', who: next.who, text: '', t: next.t || 现在(), 流式: true, 收尾: true, 目标: next.目标 }])
        return
      }
      if (待恢复.current) {
        待恢复.current = false
        set暂停hall?.(false)
        refreshHall()
      }
    }, 25)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const add = (e: HallEvent) => set临时((l) => [...l, e])
  const 更新会议 = (会议id: string, patch: Partial<HallEvent>) => {
    if (!会议id) return
    set临时((l) => {
      const idx = l.findIndex((x) => x.k === '会议' && x.会议id === 会议id)
      if (idx >= 0) {
        const next = l.slice()
        next[idx] = { ...next[idx], ...patch, k: '会议', 会议id }
        return next
      }
      return [...l, { k: '会议', 会议id, ...patch }]
    })
  }
  const 清乐观 = () => set临时((l) => l.filter((x) => x.k === '系统' || x.k === '分割'))
  const pushLine = (s: string) => setRunCard((c) => (c ? { ...c, lines: [...c.lines, s] } : c))

  async function 插话(text: string) {
    const v = text.trim()
    if (!v) return
    await postJSON('/插话', { text: v, sid: sidRef.current || '' })
  }

  async function 智能发送(text: string, files: unknown[] = [], context?: { room: string; title?: string }) {
    const v = text.trim()
    if (!v && !files.length) return
    if (工作中) {
      // 公司工作中：实时插话
      try {
        await 插话(v || '请看附件')
        toast('已插话进会场，下个发言带上你的话', 'good')
      } catch (e) {
        toast('插话失败：' + e, 'bad')
      }
      return
    }
    // 新任务开始：清掉上一次的临时和没播完的队列（没播完的话已在持久态里，恢复轮询后由字幕栈接管）。
    // 不在这里 refreshHall——它的异步返回会晚于 /活厅 落盘，把这次的船主消息也拉成持久态 → 双份。
    播放队列.current = []
    已播.current = new Set()
    待恢复.current = false
    set临时([])
    add({
      k: '话',
      who: '船主',
      text: v + (files.length ? `\n[已附 ${files.length} 个文件/截图]` : ''),
      t: 现在(),
    })
    // 活公司：直接唤醒项目经理本人，它自己判断聊/查证/开会/派活/报告
    await 活厅(v, files, context)
  }

  async function 活厅(v: string, files: unknown[], context?: { room: string; title?: string }) {
    set工作中(true)
    set暂停hall?.(true)  // 进行中暂停 /hall：只信实时工作现场，不让持久态打架
    set活人单元([])
    set公开回应总数(0)
    单元会议.current = {}
    根单元id.current = ''
    add({ k: '工作现场' })  // 占位：渲染成工作现场，位置在船主气泡之后、PM回复之前（修问题3的顺序）
    setRunCard({ kind: '活厅', running: true, lines: ['正在唤醒项目经理本人…'], start: performance.now() })
    try {
      const tj = await postJSON<{ sid?: string; 管理动作卡?: string; error?: string }>('/活厅', {
        text: v,
        attachments: files,
        project_room: context?.room || '',
        project_title: context?.title || '',
      })
      if (!tj.sid) {
        if (tj.管理动作卡) {
          // 船主自然语言直控：这句被识别成管理指令、已建确认卡（浮窗会经 /board 轮询弹出）——不是错误，别抛红字（十审#1）。
          set工作中(false)
          set暂停hall?.(false)
          setRunCard((c) => (c ? { ...c, running: false, end: performance.now() } : c))
          refreshHall()
          refreshBoard()
          return
        }
        throw new Error(tj.error || '活厅未返回会话 sid')
      }
      sidRef.current = tj.sid
      // 不清乐观——进行中已暂停 /hall，船主气泡不会和持久态双份；
      // 而且清乐观会把船主气泡+工作现场占位都清掉（它只留系统/分割），导致工作现场没处渲染。
      let result: Record<string, unknown> | null = null

      await new Promise<void>((resolve, reject) => {
        const es = new EventSource('/stream?sid=' + encodeURIComponent(tj.sid!))
        let settled = false
        let lastSeq = 0
        let lastContact = Date.now()
        let polling = false
        const cleanup = () => {
          es.close()
          window.clearInterval(pollTimer)
          window.clearInterval(watchTimer)
        }
        const finish = () => {
          if (settled) return
          settled = true
          cleanup()
          resolve()
        }
        const fail = (message: string) => {
          if (settled) return
          settled = true
          cleanup()
          reject(new Error(message))
        }
        const receive = (data: Record<string, unknown>) => {
          if (settled) return
          lastContact = Date.now()
          const seq = Math.max(0, Number(data.序号) || 0)
          if (seq && seq <= lastSeq) return
          if (seq) lastSeq = seq
          const 类型 = (data.类型 as string) || ''
          const who = data.发言人 as string | undefined
          const t = data.t as string | undefined

          if (类型 === '心跳') return
          const 单元 = (data.单元 as string) || ''
          if (类型 === '公开回应开始') {
            set公开回应总数(Math.max(0, Number(data.人数) || 0))
          } else if (类型 === '群聊发言') {
            // 到货即排队，播放泵一次放一个上打字机：视觉规则人人平等，不同帧刷屏
            const 内容 = (data.内容 as string) || ''
            const 播键 = (who || '') + '|' + 内容
            if (内容 && !内容.includes('[沉默]') && !已播.current.has(播键)) {
              已播.current.add(播键)
              播放队列.current.push({ who: who || '', 目标: 内容, t })
            }
          } else if (类型 === '插话') {
            add({ k: '话', who: who || '船主', text: (data.内容 as string) || '', t })
          } else if (类型 === '会议开始') {
            const mid = (data.会议id as string) || ''
            const 主题 = (data.主题 as string) || '会议'
            const 参会 = (data.参会 as string[]) || []
            const 参会岗位 = (data.参会岗位 as string[]) || []
            更新会议(mid, { 主题, 状态: '进行中', 参会, t, 开始: Date.now() })
            // 全体开场即"在场"（2026-07-09 船主：先进会议室、别一个个冒）：预置占位单元，等真人来了再认领升级。
            if (mid && 参会.length) {
              set活人单元((l) => {
                const 已有 = new Set(l.filter((u) => u.会议id === mid).map((u) => u.名字))
                const 席位 = 参会
                  .map((名, i) => ({ 名, 岗: 参会岗位[i] || '' }))
                  .filter((x) => x.名 && !已有.has(x.名))
                  .map((x) => ({
                    单元id: `会议席::${mid}::${x.名}`, 父单元: null, 岗位: x.岗, 名字: x.名,
                    场合: '会议', 会议id: mid, 会议主题: 主题,
                    状态: '在场' as const, 思考: '', 动作: [], 发言: '', 起: Date.now(),
                  }))
                return 席位.length ? [...l, ...席位] : l
              })
            }
          } else if (类型 === '会议结束') {
            更新会议((data.会议id as string) || '', {
              状态: '已结束',
              当前发言人: '',
              结束: Date.now(),
            })
          } else if (类型 === '人来了') {
            // 一个活人开始工作。父单元=None → 根单元。不再预挂空气泡——话没到货就没有气泡
            //（真人群聊里没人会先看到一个空框闪光标；他在忙的状态由工作现场星座显示）。
            const 父单元 = (data.父单元 as string) || null
            const 岗位 = (data.岗位 as string) || ''
            const 名字 = (data.名字 as string) || 岗位
            const 会议id = (data.会议id as string) || ''
            const 会议主题 = (data.会议主题 as string) || ''
            if (会议id) {
              单元会议.current[单元] = { 会议id, 名字 }
              更新会议(会议id, {
                主题: 会议主题 || undefined,
                状态: '进行中',
                当前发言人: 名字,
              })
            }
            if (!父单元) {
              根单元id.current = 单元
              根名字.current = 名字
            }
            set活人单元(l => {
              // 会议里：认领开场预置的"在场"占位席（同会议同名字）→升级成真单元，别再新增一个重复的
              if (会议id) {
                const idx = l.findIndex((u) => u.会议id === 会议id && u.名字 === 名字 && u.单元id.startsWith('会议席::'))
                if (idx >= 0) {
                  const n = l.slice()
                  n[idx] = { ...n[idx], 单元id: 单元, 父单元, 岗位, 会议主题: 会议主题 || n[idx].会议主题, 状态: '工作中', 起: Date.now() }
                  return n
                }
              }
              return [...l, {
                单元id: 单元, 父单元, 岗位, 名字, 场合: (data.场合 as string) || '',
                会议id: 会议id || undefined, 会议主题: 会议主题 || undefined,
                状态: '工作中', 思考: '', 动作: [], 发言: '', 起: Date.now(),
              }]
            })
          } else if (类型 === '在想') {
            const delta = (data.delta as string) || ''
            set活人单元(l => l.map(u => u.单元id === 单元 ? { ...u, 思考: u.思考 + delta } : u))
          } else if (类型 === '动作开始') {
            const 动作id = (data.动作id as string) || ''
            const 工具 = (data.工具 as string) || ''
            const 轮 = (data.轮 as number) || 0
            set活人单元(l => l.map(u => u.单元id === 单元
              ? { ...u, 动作: [...u.动作, { 动作id, 工具, 目标: '', 结果: '', 轮, 状态: '进行' as const, 成功: true }] } : u))
          } else if (类型 === '动作完成') {
            const 动作id = (data.动作id as string) || ''
            const 目标 = (data.目标 as string) || ''
            const 结果 = (data.结果 as string) || ''
            const 成功 = data.成功 !== false
            set活人单元(l => l.map(u => u.单元id === 单元
              ? { ...u, 动作: u.动作.map(a => a.动作id === 动作id
                  ? { ...a, 目标, 结果, 成功, 状态: '完成' as const } : a) } : u))
          } else if (类型 === '在说') {
            // 发言先流进工作现场单元（星座浮牌可见"他在说什么"）；大厅气泡等说完、到货后排队播放
            const delta = (data.delta as string) || ''
            const ref = 单元会议.current[单元]
            if (ref) 更新会议(ref.会议id, { 状态: '进行中', 当前发言人: ref.名字 })
            set活人单元(l => l.map(u => u.单元id === 单元 ? { ...u, 发言: u.发言 + delta } : u))
          } else if (类型 === '说完') {
            const 内容 = (data.内容 as string) || ''
            const 全文 = (data.全文 as string) || 内容
            const ref = 单元会议.current[单元]
            if (ref) 更新会议(ref.会议id, { 状态: '进行中', 当前发言人: ref.名字 })
            set活人单元(l => l.map(u => u.单元id === 单元 ? { ...u, 发言: 全文 || u.发言 } : u))
            if (单元 === 根单元id.current && 内容 && !内容.includes('[沉默]')) {
              const 播键 = (根名字.current || who || '') + '|' + 内容
              if (!已播.current.has(播键)) {
                已播.current.add(播键)
                播放队列.current.push({ who: 根名字.current || who || '', 目标: 内容, t })
              }
            }
          } else if (类型 === '走了') {
            set活人单元(l => l.map(u => u.单元id === 单元 ? { ...u, 状态: '完成', 止: Date.now() } : u))
          } else if (类型 === '进展') {
            const line = (data.内容 as string) || ''
            pushLine(line)
          } else if (类型 === '立项完成') {
            if (data.result) result = data.result as Record<string, unknown>
          } else if (类型 === '错误') {
            fail((data.内容 as string) || '活厅失败')
            return
          }
          if (类型 === 'run结束') {
            finish()
          }
        }
        es.onmessage = (ev) => {
          try {
            receive(JSON.parse(ev.data || '{}') as Record<string, unknown>)
          } catch {
            // 单个坏帧交给按序号补拉纠正，不能拖死整轮。
          }
        }
        es.onerror = () => {
          // 公网代理可能中断长连接；真实事件仍在服务端按序号落盘，交给短请求继续补收。
          es.close()
        }
        const pull = async () => {
          if (settled || polling) return
          polling = true
          try {
            const snapshot = await getJSON<{
              事件?: Record<string, unknown>[]
              状态?: string
              已结束?: boolean
              错误?: string
              error?: string
            }>('/run-events?sid=' + encodeURIComponent(tj.sid!) + '&after=' + lastSeq)
            if (snapshot.error) throw new Error(snapshot.error)
            lastContact = Date.now()
            for (const event of snapshot.事件 || []) receive(event)
            if (snapshot.已结束 && !settled) {
              if (snapshot.状态 && ['失败', '已停止', '被重启中断', '被门禁拦住'].includes(snapshot.状态)) {
                fail(snapshot.错误 || `任务${snapshot.状态}`)
              } else {
                finish()
              }
            }
          } catch {
            // 短暂断网继续重试；持续 30 秒收不到任何通道响应才明确失败。
          } finally {
            polling = false
          }
        }
        const pollTimer = window.setInterval(() => void pull(), 1000)
        const watchTimer = window.setInterval(() => {
          if (Date.now() - lastContact > 30000) fail('实时连接和事件补拉连续 30 秒无响应')
        }, 1000)
        void pull()
      })

      set工作中(false)
      sidRef.current = null
      setRunCard((c) => (c ? { ...c, running: false, end: performance.now() } : c))
      // 工作结束：所有还挂着的活人标完成
      set活人单元(l => l.map(u => u.状态 !== '完成' ? { ...u, 状态: '完成' as const, 止: Date.now() } : u))   // 在场占位/工作中 一并收尾
      const r = (result || {}) as Record<string, unknown>
      const 文 = r.报告 !== undefined
        ? '' // 活公司：项目经理的报告已通过它自己的发言气泡显示了，这里不重复
        : r.已停止
        ? '已停止本次会议。公司已恢复待命，可以直接发新消息。'
        : r.待回应
        ? `公司停在『要问你』：${((r.待船主 as string[]) || []).join('、') || '有几处要先问你'}。请在大厅补一句，项目经理本人会带着你的话继续判断。`
        : r.评估结论
          ? `项目经理的结论：\n\n${r.评估结论}\n\n（这只是历史评估结果；要真派活，请直接在大厅对活公司说需求。）`
          : `本次工作已结束。要继续，请直接在大厅补充需求。`
      if (文) add({ k: '系统', who: '办公室', text: 文 })
      refreshBoard()
      // 结束后不立刻恢复 /hall——播放队列还没放完就恢复，持久态会一把全刷出来穿帮。
      // 泵在「队列空 + 屏上没人打字」时才恢复轮询+刷一把（字幕栈有去重，不会双份）。
      待恢复.current = true
    } catch (e) {
      set工作中(false)
      sidRef.current = null
      播放队列.current = []
      单元会议.current = {}
      待恢复.current = false
      set暂停hall?.(false)
      refreshHall()
      setRunCard((c) => (c ? { ...c, running: false, end: performance.now(), lines: [...c.lines, '失败：' + e] } : c))
      toast('活厅' + (String(e).includes('cancel') ? '已停止' : '失败：' + e), '')
    }
  }

  function 点名() {
    setRunCard({ kind: '点名', running: true, lines: ['并行自检准备中…'], start: performance.now() })
    try {
      const es = new EventSource('/rollcall')
      es.onmessage = (ev) => {
        let data: { line?: string }
        try {
          data = JSON.parse(ev.data || '{}')
        } catch {
          return
        }
        if (data.line) {
          setRunCard((c) => {
            if (!c) return c
            const lines = c.lines.length === 1 && c.lines[0] === '并行自检准备中…' ? [] : c.lines
            return { ...c, lines: [...lines, data.line!] }
          })
        }
        if ((data.line || '').startsWith('说明:')) {
          es.close()
          setRunCard((c) => (c ? { ...c, running: false, end: performance.now() } : c))
          refreshBoard()
        }
      }
      es.onerror = () => {
        es.close()
        setRunCard((c) => (c ? { ...c, running: false, end: performance.now(), lines: [...c.lines, '点名连接中断。'] } : c))
      }
    } catch (e) {
      setRunCard((c) => (c ? { ...c, running: false, end: performance.now(), lines: [...c.lines, '点名失败：' + e] } : c))
    }
  }

  return { 临时, runCard, 工作中, 活人单元, 公开回应总数, 智能发送, 插话, 点名 }
}

export type HallStore = ReturnType<typeof useHallStore>
