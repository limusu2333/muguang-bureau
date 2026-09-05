import { useEffect, useMemo, useRef, useState } from 'react'
import { getJSON, postJSON } from '../api'
import type { 健康, 健康告警, 健康告警处理 } from '../api'

const HEALTH_ACK_KEY = 'xj_health_alert_ack'
const HEALTH_SESSION_DISMISS_KEY = 'xj_health_alert_session_dismissed'
const HEALTH_ACTION_KEY = 'xj_health_action_current'
const HEALTH_ACTION_ACK_KEY = 'xj_health_action_ack'

type Choice = string

function 读取数组(key: string): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '[]')
    return Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

function 读取回执(): 健康告警处理 | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(HEALTH_ACTION_KEY) || 'null')
    return value && typeof value === 'object' && typeof value.id === 'string' ? value : null
  } catch {
    return null
  }
}

function 读取字符串(key: string): string {
  try {
    return sessionStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

function 告警指纹(alert: 健康告警) {
  if (alert.指纹) return alert.指纹
  return JSON.stringify({
    id: alert.任务id || '',
    标题: alert.标题 || '',
    详情: alert.详情 || '',
    更新时间: alert.更新时间 || '',
  })
}

function 健康指纹(health: 健康 | null): string {
  // 新后端已经把任务、系统和班子问题统一成告警列表。
  // 这里只用整份列表判断“本次收起”，不再另造一张重复卡。
  if (health?.告警 !== undefined) {
    return JSON.stringify((health.告警 ?? []).map(告警指纹))
  }
  const crew = Object.entries(health?.班子 ?? {})
    .filter(([, value]) => value?.ok === false)
    .map(([name, value]) => `${name}:${value?.状态 || ''}:${value?.原因 || ''}`)
  const issues = (health?.系统?.问题 ?? [])
    .filter((issue) => issue?.级别 === '错误' || issue?.级别 === '警告')
    .map((issue) => `${issue.级别 || ''}:${issue.区域 || ''}:${issue.说明 || ''}`)
  return crew.length || issues.length ? JSON.stringify({ crew, issues }) : ''
}

function 当前告警(health: 健康 | null, acknowledged: string[]): 健康告警 | null {
  if (health?.告警 !== undefined) {
    return (health.告警 ?? []).find((item) => !acknowledged.includes(告警指纹(item))) ?? null
  }
  // 只兼容没有“告警”字段的旧后端。新后端即使返回空列表，也不得在前端重复合成。
  const issue = (health?.系统?.问题 ?? []).find((item) =>
    (item?.级别 === '错误' || item?.级别 === '警告'))
  if (issue) {
    const alert: 健康告警 = {
      类别: '系统',
      级别: issue.级别 === '错误' ? '错误' : '警告',
      标题: `${issue.区域 || '系统'}出现${issue.级别 || '异常'}`,
      详情: issue.说明 || '没有留下具体原因。',
      建议: '请先核对真实原因，再决定是否交给项目经理处理。',
    }
    return acknowledged.includes(告警指纹(alert)) ? null : alert
  }
  const crew = Object.entries(health?.班子 ?? {}).find(([, value]) => value?.ok === false)
  if (!crew) return null
  const [name, value] = crew
  const alert: 健康告警 = {
    类别: '班子',
    级别: '错误',
    标题: `${name}当前无法正常调用`,
    详情: value?.原因 || value?.状态 || '没有留下具体原因。',
    建议: '请核对模型、账号或网络，再决定是否交给项目经理处理。',
  }
  return acknowledged.includes(告警指纹(alert)) ? null : alert
}

export function 有健康提问或回执(health: 健康 | null) {
  if (typeof window === 'undefined') return false
  if (读取回执()) return true
  const snapshot = 健康指纹(health)
  if (snapshot && 读取字符串(HEALTH_SESSION_DISMISS_KEY) === snapshot) return false
  return Boolean(当前告警(health, 读取数组(HEALTH_ACK_KEY)))
}

function 写回执(receipt: 健康告警处理 | null) {
  try {
    if (receipt) sessionStorage.setItem(HEALTH_ACTION_KEY, JSON.stringify(receipt))
    else sessionStorage.removeItem(HEALTH_ACTION_KEY)
  } catch {
    /* 会话存储不可用时只保留当前页面状态。 */
  }
}

function 状态文案(receipt: 健康告警处理) {
  if (receipt.状态 === '已完成') return '项目经理已经处理完成。'
  if (receipt.状态 === '失败' || receipt.状态 === '状态不可读') return receipt.错误 || '处理失败，但没有留下原因。'
  if (receipt.状态 === '处理中') return '项目经理正在处理。你可以继续使用大厅。'
  if (receipt.状态 === '稍后') return '这次先不处理，已经移到待处理列表；任务有新变化时再提醒。'
  return '决定已经收到，正在交给项目经理。你可以继续使用大厅。'
}

function 发生时间(value?: string) {
  if (!value) return ''
  const match = value.match(/^(\d{4}-\d{2}-\d{2})T?(\d{2}:\d{2})/)
  return match ? `${match[1]} ${match[2]}` : value
}

export default function HealthQuestionCard({
  health,
  refresh,
  onActiveChange,
}: {
  health: 健康 | null
  refresh: () => void
  onActiveChange: (active: boolean) => void
}) {
  const [acknowledged, setAcknowledged] = useState<string[]>(() => 读取数组(HEALTH_ACK_KEY))
  const [closedSnapshot, setClosedSnapshot] = useState(() => 读取字符串(HEALTH_SESSION_DISMISS_KEY))
  const [snoozed, setSnoozed] = useState('')
  const [choice, setChoice] = useState<Choice | ''>('')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [receipt, setReceipt] = useState<健康告警处理 | null>(() => 读取回执())
  const noteRef = useRef<HTMLTextAreaElement | null>(null)
  const alert = useMemo(() => 当前告警(health, acknowledged), [health, acknowledged])
  const healthSnapshot = useMemo(() => 健康指纹(health), [health])
  const fingerprint = useMemo(() => alert ? 告警指纹(alert) : '', [alert])
  const hidden = !receipt && (!alert || fingerprint === snoozed || closedSnapshot === healthSnapshot)

  useEffect(() => onActiveChange(!hidden), [hidden, onActiveChange])

  useEffect(() => {
    if (choice === '补充') noteRef.current?.focus()
  }, [choice])

  useEffect(() => {
    if (!receipt?.id || ['已完成', '失败', '稍后', '状态不可读'].includes(receipt.状态 || '')) return
    let stopped = false
    const poll = async () => {
      try {
        const result = await getJSON<{ 处理?: 健康告警处理 | null }>(`/健康告警_处理状态?id=${encodeURIComponent(receipt.id)}`)
        if (!stopped && result.处理) {
          setReceipt(result.处理)
          写回执(result.处理)
        }
      } catch {
        /* 短暂断线保留上次状态，下一轮继续取。 */
      }
    }
    void poll()
    const timer = window.setInterval(poll, 1600)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [receipt?.id, receipt?.状态])

  useEffect(() => {
    if (receipt) return
    let stopped = false
    getJSON<{ 记录?: 健康告警处理[] }>('/健康告警_处理状态')
      .then((result) => {
        if (stopped) return
        const ignored = 读取数组(HEALTH_ACTION_ACK_KEY)
        const latest = (result.记录 ?? []).find((item) =>
          Boolean(item.id) && !ignored.includes(item.id) && !['稍后'].includes(item.状态 || ''))
        if (latest) {
          setReceipt(latest)
          写回执(latest)
        }
      })
      .catch(() => undefined)
    return () => { stopped = true }
  }, [receipt])

  const remember = () => {
    if (!fingerprint) return
    const next = [...acknowledged.filter((item) => item !== fingerprint), fingerprint].slice(-120)
    try {
      localStorage.setItem(HEALTH_ACK_KEY, JSON.stringify(next))
    } catch {
      /* 本地存储不可用时只在本轮关闭。 */
    }
    setAcknowledged(next)
  }

  const submit = async (forced?: Choice) => {
    const action = forced || choice
    if (!action || !alert || submitting) return
    if (action === '不再提示') {
      remember()
      setChoice('')
      setError('')
      return
    }
    if (action === '补充' && !note.trim()) {
      setError('请先写下要补充的话。')
      noteRef.current?.focus()
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const result = await postJSON<{
        ok?: boolean
        error?: string
        处理?: 健康告警处理
      }>('/健康告警_处理', {
        动作: action,
        补充: action === '补充' ? note.trim() : '',
        指纹: fingerprint,
        告警: alert,
      }, 5000)
      if (result.ok === false || !result.处理) throw new Error(result.error || '公司没有返回收件回执')
      setReceipt(result.处理)
      写回执(result.处理)
      if (action === '稍后') setSnoozed(fingerprint)
      else remember()
      refresh()
    } catch (reason) {
      setError(`没有送达：${reason instanceof Error ? reason.message : String(reason)}。内容还在，可以重试。`)
    } finally {
      setSubmitting(false)
    }
  }

  const closeForNow = () => {
    if (healthSnapshot) {
      try { sessionStorage.setItem(HEALTH_SESSION_DISMISS_KEY, healthSnapshot) } catch { /* 只在当前页面收起。 */ }
    }
    setClosedSnapshot(healthSnapshot)
    setChoice('')
    setError('')
    onActiveChange(false)
  }

  const closeReceipt = () => {
    if (receipt?.id) {
      const next = [...读取数组(HEALTH_ACTION_ACK_KEY).filter((item) => item !== receipt.id), receipt.id].slice(-120)
      try { localStorage.setItem(HEALTH_ACTION_ACK_KEY, JSON.stringify(next)) } catch { /* 只在本轮关闭。 */ }
    }
    setReceipt(null)
    写回执(null)
    onActiveChange(false)
  }

  if (hidden) return null

  if (receipt) {
    const failed = receipt.状态 === '失败' || receipt.状态 === '状态不可读'
    const done = receipt.状态 === '已完成' || receipt.状态 === '稍后'
    return (
      <div className="health-question-line">
        <section className={`health-receipt-card${failed ? ' failed' : ''}`} aria-live="polite">
          <div className="health-receipt-mark" aria-hidden>{failed ? '!' : done ? '✓' : '·'}</div>
          <div className="health-receipt-copy">
            <b>{receipt.动作 === '补充' ? '已收到你的补充' : receipt.动作 === '稍后' ? '已设为稍后' : '已收到你的决定'}</b>
            {receipt.补充 && <p>“{receipt.补充}”</p>}
            <small>{状态文案(receipt)}</small>
            {receipt.状态 === '已完成' && receipt.结果 && <p className="health-action-result">{receipt.结果}</p>}
          </div>
          {(failed || done) && <button className="health-card-close" onClick={closeReceipt} aria-label="收起">×</button>}
        </section>
      </div>
    )
  }

  if (!alert) return null
  const total = Math.max(1, health?.告警?.length ?? 1)
  const current = Math.max(1, (health?.告警 ?? []).findIndex((item) => 告警指纹(item) === fingerprint) + 1)
  const actionChoices: Array<{ id: Choice; title: string; description: string }> = alert.可选动作?.length
    ? alert.可选动作.map((item) => ({
        id: item.id,
        title: item.标题 || item.id,
        description: item.说明 || '按这项决定继续处理',
      }))
    : [
        { id: '同意', title: '同意', description: '按上面的建议交给项目经理继续处理' },
        { id: '稍后', title: '稍后', description: '现在先不处理，下次打开办公室再提醒' },
        { id: '补充', title: '补充', description: '写下你的判断，再一起交给公司' },
      ]
  const choices = actionChoices.some((item) => item.id === '不再提示')
    ? actionChoices
    : [...actionChoices, {
        id: '不再提示',
        title: '不再提示',
        description: '记住这一条提醒；问题内容发生变化时仍会再次提醒',
      }]

  return (
    <div className="health-question-line">
      <section className="health-question-card" aria-label="公司有一条提醒">
        <header className="health-question-head">
          <div>
            <span>公司有一条提醒{alert.更新时间 ? ` · 发生于 ${发生时间(alert.更新时间)}` : ''}</span>
            <h3>{alert.标题 || '有一件事需要你处理'}</h3>
          </div>
          <div className="health-question-progress">{current} / {total}</div>
          <button className="health-card-close" onClick={closeForNow} aria-label="关闭本次提醒" title="关闭本次提醒">×</button>
        </header>
        <div className="health-question-context">
          <p>{alert.详情 || '没有留下具体原因。'}</p>
          {alert.建议 && <p><b>建议：</b>{alert.建议}</p>}
        </div>
        <div className="health-choice-list" role="radiogroup" aria-label="请选择处理方式">
          {choices.map((item) => (
            <div className={`health-choice-wrap${choice === item.id ? ' selected' : ''}`} key={item.id}>
              <button
                className="health-choice-row"
                role="radio"
                aria-checked={choice === item.id}
                onClick={() => { setChoice(item.id); setError('') }}
              >
                <span className="health-choice-radio" aria-hidden />
                <span className="health-choice-copy"><b>{item.title}</b><small>{item.description}</small></span>
              </button>
              {item.id === '补充' && choice === '补充' && (
                <textarea
                  ref={noteRef}
                  value={note}
                  rows={3}
                  placeholder="把你的补充写在这里"
                  onChange={(event) => setNote(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) void submit()
                  }}
                />
              )}
            </div>
          ))}
        </div>
        {error && <div className="health-question-error" role="alert">{error}</div>}
        <footer className="health-question-footer">
          <span>{choice ? '已选择 1 项' : '可以直接关闭，也可以选择如何处理'}</span>
          <button
            className="health-question-submit"
            disabled={!choice || submitting || (choice === '补充' && !note.trim())}
            onClick={() => void submit()}
            aria-label="提交选择"
          >
            {submitting ? <i className="health-submit-spinner" /> : '→'}
          </button>
        </footer>
      </section>
    </div>
  )
}
