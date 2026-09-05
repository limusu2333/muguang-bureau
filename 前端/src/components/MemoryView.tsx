// 记忆库后台（Kindroid式）：每个人的记忆可见、可删、可改。
// 2026-07-06 船主令：取消『待审』审批门——教导直接进『叮嘱』即时生效，每条可改可删，船主自己筛。
import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { getJSON, postJSON } from '../api'
import { toast } from '../toast'
import { renderLightText } from './文本渲染'

type 记忆人 = {
  人名: string
  岗位: string
  原则: string
  教导: string[]
  近况: string
  流水: string[]
}

type 图谱事实 = { id: number; 谓: string; 宾: string; 来源类: string; pinned: number }
const 图谱键 = '__图谱__'

function 记事列表({
  cur,
  搜索,
  显条数,
  set显条数,
  op,
}: {
  cur: 记忆人
  搜索: string
  显条数: number
  set显条数: (n: number) => void
  op: (body: Record<string, string>, 提示: string) => void
}) {
  const 全部 = [...cur.流水].reverse() // 最新在前
  const 命中 = 搜索.trim() ? 全部.filter((l) => l.includes(搜索.trim())) : 全部
  const 显 = 命中.slice(0, 显条数)
  return (
    <>
      {命中.length === 0 && <div className="mem-prose dim">{搜索 ? '没搜到' : '还没有记事'}</div>}
      {显.map((raw) => {
        const { 时, 文 } = 拆行(raw)
        return (
          <div className="mem-line entry" key={raw}>
            <span className="mem-time">{时}</span>
            <div className="mem-text">{renderLightText(文)}</div>
            <button className="mem-del" title="删掉这条" onClick={() => op({ op: '删行', 人名: cur.人名, 区: '流水', 行: raw }, '已删')}>
              ✕
            </button>
          </div>
        )
      })}
      {命中.length > 显条数 && (
        <button className="mem-more" onClick={() => set显条数(显条数 + 30)}>
          看更早的 · 还有 {命中.length - 显条数} 条
        </button>
      )}
    </>
  )
}

function 拆行(raw: string): { 时: string; 文: string } {
  const m = raw.match(/^-\s*\[([^\]]*)\]\s*(.*)$/)
  if (m) return { 时: m[1], 文: m[2] }
  return { 时: '', 文: raw.replace(/^-\s*/, '') }
}

type 页签 = '近况' | '原则' | '叮嘱' | '记事'

const 编辑框样式: CSSProperties = {
  flex: 1,
  font: 'inherit',
  lineHeight: 1.6,
  background: 'rgba(255,255,255,.06)',
  color: 'inherit',
  border: '1px solid rgba(217,160,76,.5)',
  borderRadius: 4,
  padding: '5px 9px',
  resize: 'vertical',
}

export default function MemoryView() {
  const [人们, set人们] = useState<记忆人[]>([])
  const [选中, set选中] = useState('')
  const [页, set页] = useState<页签>('近况')
  const [搜索, set搜索] = useState('')
  const [显条数, set显条数] = useState(30)
  const [编辑中, set编辑中] = useState('') // 正在改的那条叮嘱(raw)
  const [草稿, set草稿] = useState('')
  const [图谱, set图谱] = useState<图谱事实[]>([])
  const 拉 = () =>
    getJSON<{ 人们: 记忆人[]; 船主图谱?: 图谱事实[] }>('/memory')
      .then((r) => {
        set人们(r.人们 || [])
        set图谱(r.船主图谱 || [])
      })
      .catch(() => {})
  useEffect(() => {
    拉()
  }, [])

  const cur = 人们.find((p) => p.人名 === 选中) || 人们[0]
  const op = async (body: Record<string, string>, 提示: string) => {
    try {
      const r = await postJSON<{ ok: boolean; error?: string }>('/memory_op', body)
      if (!r.ok) throw new Error(r.error || '失败')
      toast(提示, 'good')
      拉()
    } catch (e) {
      toast('操作失败：' + e, 'bad')
    }
  }

  return (
    <main className="stage page-stage">
      <div className="stage-pad holo-page mem-page">
        <div className="page-head">
          <div className="page-k">MEMORY · 记忆库</div>
        </div>

        <div className="mem-rail" role="tablist" aria-label="按人查看记忆">
          {人们.map((p) => (
            <button
              key={p.人名}
              className={'mem-chip' + (cur?.人名 === p.人名 ? ' on' : '')}
              onClick={() => set选中(p.人名)}
            >
              {p.人名}
              <small>{p.岗位}</small>
            </button>
          ))}
          <button
            className={'mem-chip' + (选中 === 图谱键 ? ' on' : '')}
            onClick={() => set选中(图谱键)}
          >
            关于你
            <small>图谱</small>
          </button>
        </div>

        {选中 === 图谱键 ? (
          <div className="mem-panel">
            <div className="mem-tabs">
              <div className="mem-tab on">关于你的事实{图谱.length > 0 && <em>{图谱.length}</em>}</div>
            </div>
            <div className="mem-pane">
              {图谱.length === 0 && <div className="mem-prose dim">还没有关于你的事实——你在大厅聊过之后会自动长出来；不满意可以改、删、钉住</div>}
              {图谱.map((f) => {
                const 改中 = 编辑中 === '图谱' + f.id
                return (
                  <div className="mem-line entry" key={f.id}>
                    {改中 ? (
                      <>
                        <span className="mem-time" style={{ flex: '0 0 auto' }}>{f.谓}：</span>
                        <textarea style={编辑框样式} value={草稿} autoFocus rows={1} onChange={(e) => set草稿(e.target.value)} />
                        <span className="mem-acts">
                          <button
                            className="text-action"
                            onClick={() => {
                              const v = 草稿.trim()
                              if (!v) {
                                toast('内容不能为空', 'bad')
                                return
                              }
                              op({ op: '图谱改', id: String(f.id), 新文: v }, '已改')
                              set编辑中('')
                            }}
                          >
                            保存
                          </button>
                          <button className="text-action dim" onClick={() => set编辑中('')}>
                            取消
                          </button>
                        </span>
                      </>
                    ) : (
                      <>
                        <div className="mem-text">
                          <b>{f.谓}</b>：{f.宾}
                          {f.pinned ? <span className="tag" style={{ marginLeft: 6 }}>已钉</span> : null}
                          {f.来源类 === '亲口' ? (
                            <span className="tag" style={{ marginLeft: 6, background: '#2e7d32', color: '#fff' }}>亲口</span>
                          ) : null}
                        </div>
                        <span className="mem-acts">
                          <button
                            className="text-action"
                            onClick={() => {
                              set编辑中('图谱' + f.id)
                              set草稿(f.宾)
                            }}
                          >
                            修改
                          </button>
                          {!f.pinned && (
                            <button className="text-action" onClick={() => op({ op: '图谱钉', id: String(f.id) }, '已钉住·永不淡出')}>
                              钉住
                            </button>
                          )}
                          <button className="mem-del" title="删掉这条事实（图谱+船主档案一起清）" onClick={() => op({ op: '图谱删', id: String(f.id), 宾: f.宾 }, '已删')}>
                            ✕
                          </button>
                        </span>
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        ) : !cur ? (
          <div className="decision-empty">记忆库还是空的</div>
        ) : (
          <div className="mem-panel">
            <div className="mem-tabs" role="tablist" aria-label="记忆分区">
              {(['近况', '原则', '叮嘱', '记事'] as 页签[]).map((t) => {
                const n = t === '叮嘱' ? cur.教导.length : t === '记事' ? cur.流水.length : 0
                return (
                  <button
                    key={t}
                    className={'mem-tab' + (页 === t ? ' on' : '')}
                    onClick={() => {
                      set页(t)
                      set显条数(30)
                      set搜索('')
                      set编辑中('')
                    }}
                  >
                    {t}
                    {n > 0 && <em>{n}</em>}
                  </button>
                )
              })}
              {页 === '记事' && cur.流水.length > 8 && (
                <input
                  className="mem-search"
                  placeholder="搜记事"
                  value={搜索}
                  onChange={(e) => {
                    set搜索(e.target.value)
                    set显条数(30)
                  }}
                />
              )}
            </div>

            <div className="mem-pane">
              {页 === '近况' && (
                <div className="mem-prose">{cur.近况 ? renderLightText(cur.近况) : <span className="dim">还没有近况——聊过之后这里会自动长出来</span>}</div>
              )}
              {页 === '原则' && (
                <div className="mem-prose">{cur.原则 ? renderLightText(cur.原则) : <span className="dim">还没有原则——睡前巩固会从他的经历里蒸馏出来</span>}</div>
              )}
              {页 === '叮嘱' && (
                <>
                  {cur.教导.length === 0 && <div className="mem-prose dim">还没有叮嘱——你在大厅立的规矩、给的纠正会自动落到这里；不满意可以改或删</div>}
                  {cur.教导.map((raw) => {
                    const { 时, 文 } = 拆行(raw)
                    const 改中 = 编辑中 === raw
                    return (
                      <div className="mem-line entry" key={raw}>
                        <span className="mem-time">{时}</span>
                        {改中 ? (
                          <>
                            <textarea
                              style={编辑框样式}
                              value={草稿}
                              autoFocus
                              rows={2}
                              onChange={(e) => set草稿(e.target.value)}
                            />
                            <span className="mem-acts">
                              <button
                                className="text-action"
                                onClick={() => {
                                  const 新文 = 草稿.trim()
                                  if (!新文) {
                                    toast('内容不能为空', 'bad')
                                    return
                                  }
                                  op({ op: '改行', 人名: cur.人名, 区: '船主教导', 行: raw, 新文 }, '已改')
                                  set编辑中('')
                                }}
                              >
                                保存
                              </button>
                              <button className="text-action dim" onClick={() => set编辑中('')}>
                                取消
                              </button>
                            </span>
                          </>
                        ) : (
                          <>
                            <div className="mem-text">{renderLightText(文)}</div>
                            <span className="mem-acts">
                              <button
                                className="text-action"
                                onClick={() => {
                                  set编辑中(raw)
                                  set草稿(文)
                                }}
                              >
                                修改
                              </button>
                              <button
                                className="mem-del"
                                title="删掉这条"
                                onClick={() => op({ op: '删行', 人名: cur.人名, 区: '船主教导', 行: raw }, '已删')}
                              >
                                ✕
                              </button>
                            </span>
                          </>
                        )}
                      </div>
                    )
                  })}
                </>
              )}
              {页 === '记事' && (
                <记事列表 cur={cur} 搜索={搜索} 显条数={显条数} set显条数={set显条数} op={op} />
              )}
            </div>
          </div>
        )}
      </div>
    </main>
  )
}
