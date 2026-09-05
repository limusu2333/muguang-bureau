export const VISUAL_FPS = {
  active: 60,
  idle: 30,
  background: 12,
} as const

export function visualFrameRate(primaryView: boolean, working: boolean): number {
  if (!primaryView) return VISUAL_FPS.background
  return working ? VISUAL_FPS.active : VISUAL_FPS.idle
}

export function visualPixelRatio(width: number, height: number): number {
  const deviceRatio = Math.max(1, window.devicePixelRatio || 1)
  const largeCanvas = width * height >= 900_000
  return Math.min(deviceRatio, largeCanvas ? 1.5 : 2)
}

export function visualFrameDue(now: number, lastPaint: number, fps: number): boolean {
  if (!lastPaint) return true
  return now - lastPaint >= 1000 / Math.max(1, fps) - 1
}
