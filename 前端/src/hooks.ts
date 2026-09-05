import { useEffect, useRef, useState } from 'react'

// 轮询：每 ms 拉一次，拉失败静默（下一轮重试，不把界面打崩）。
// 返回 [数据, 立即刷新()]。enabled=false 时停轮询（如不在大厅就不拉 /hall）。
export function usePoll<T>(
  fn: () => Promise<T>,
  ms: number,
  enabled = true,
): [T | null, () => void, boolean] {
  const [data, setData] = useState<T | null>(null)
  const [连着, set连着] = useState(true)  // 上一次轮询是否成功——LINK灯的真话来源
  const fnRef = useRef(fn)
  fnRef.current = fn

  const 拉 = useRef(async () => {
    try {
      setData(await fnRef.current())
      set连着(true)
    } catch {
      set连着(false)  // 静默重试，但灯要说真话
    }
  })

  useEffect(() => {
    if (!enabled) return
    拉.current()
    const id = setInterval(() => 拉.current(), ms)
    return () => clearInterval(id)
  }, [enabled, ms])

  return [data, () => 拉.current(), 连着]
}

// 版本轮询：先拉很小的“是否变化”标记，只有标记变化时才下载完整数据。
// 手动刷新仍会强制拉完整数据；并发刷新会排在当前请求后执行，避免重复下载和旧结果覆盖新结果。
export function useVersionedPoll<T>(
  load: () => Promise<T>,
  loadVersion: () => Promise<string>,
  ms: number,
  enabled = true,
): [T | null, () => void, boolean] {
  const [data, setData] = useState<T | null>(null)
  const [连着, set连着] = useState(true)
  const dataRef = useRef<T | null>(null)
  const versionRef = useRef('')
  const loadRef = useRef(load)
  const loadVersionRef = useRef(loadVersion)
  const inFlightRef = useRef<Promise<void> | null>(null)
  const forceAfterRef = useRef(false)
  const pullRef = useRef<(force?: boolean) => Promise<void>>(async () => {})
  loadRef.current = load
  loadVersionRef.current = loadVersion

  pullRef.current = async (force = false) => {
    if (inFlightRef.current) {
      if (force) forceAfterRef.current = true
      return inFlightRef.current
    }
    const job = (async () => {
      try {
        const version = await loadVersionRef.current()
        if (force || dataRef.current === null || versionRef.current !== version) {
          const next = await loadRef.current()
          dataRef.current = next
          versionRef.current = version
          setData(next)
        }
        set连着(true)
      } catch {
        set连着(false)
      } finally {
        inFlightRef.current = null
        if (forceAfterRef.current) {
          forceAfterRef.current = false
          void pullRef.current(true)
        }
      }
    })()
    inFlightRef.current = job
    return job
  }

  useEffect(() => {
    if (!enabled) return
    void pullRef.current()
    const id = setInterval(() => void pullRef.current(), ms)
    return () => clearInterval(id)
  }, [enabled, ms])

  return [data, () => void pullRef.current(true), 连着]
}
