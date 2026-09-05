import { useEffect, useState } from 'react'

type Toast = { id: number; msg: string; kind: string }
let push: ((t: { msg: string; kind: string }) => void) | null = null
let seq = 1

// 全局轻提示（废 alert，照暗房亮桌：右下角浮条，3.2s 自散）
export function toast(msg: string, kind: '' | 'good' | 'bad' = '') {
  push?.({ msg, kind })
}

export function ToastHost() {
  const [list, setList] = useState<Toast[]>([])
  useEffect(() => {
    push = (t) => {
      const id = seq++
      setList((l) => [...l, { id, ...t }])
      setTimeout(() => setList((l) => l.filter((x) => x.id !== id)), 3200)
    }
    return () => {
      push = null
    }
  }, [])
  return (
    <div id="toasts">
      {list.map((t) => (
        <div key={t.id} className={'toast' + (t.kind ? ' ' + t.kind : '')}>
          {t.msg}
        </div>
      ))}
    </div>
  )
}
