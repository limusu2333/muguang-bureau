// 共享小件：栏目标题、空状态行
export function SecTitle({ txt, n, live }: { txt: string; n: number; live?: boolean }) {
  return (
    <div className="sec-h">
      {txt}
      <span className={'count' + (live && n ? ' live' : '')}>{n}</span>
    </div>
  )
}

export function EmptyRow({ t, inStage }: { t: string; inStage?: boolean }) {
  return (
    <div
      className="empty"
      style={inStage ? undefined : { padding: '12px 16px', textAlign: 'left', whiteSpace: 'pre-line' }}
    >
      {t}
    </div>
  )
}
