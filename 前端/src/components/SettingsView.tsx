// 设置页 · 星球调音台：星球在上面的舞台转，操作台横在下部——五列参数，边看边拧。
import { useState } from 'react'
import { 星球参 } from './HaloCanvas'
import type { VisualTheme } from '../theme'

const 项们: { 名: keyof typeof 星球参; lo: number; hi: number; 说: string }[] = [
  { 名: '转速', lo: 0.1, hi: 3, 说: '涡旋快慢' },
  { 名: '亮度', lo: 0.3, hi: 2, 说: '整体光度' },
  { 名: '数量', lo: 0.3, hi: 2, 说: '星尘几多' },
  { 名: '大小', lo: 0.4, hi: 2.5, 说: '单粒粗细' },
  { 名: '呼吸', lo: 0.2, hi: 3, 说: '吐纳节奏' },
  { 名: '闪烁', lo: 0.2, hi: 2, 说: '明灭幅度' },
]

export default function SettingsView({ theme, setTheme }: { theme: VisualTheme; setTheme: (theme: VisualTheme) => void }) {
  const [, 刷] = useState(0)
  const 改 = (名: keyof typeof 星球参, v: number) => {
    星球参[名] = v
    localStorage.setItem('星球参', JSON.stringify(星球参))
    刷((x) => x + 1)
  }
  return (
    <main className="stage settings-stage">
      <section className="theme-picker" aria-labelledby="theme-picker-title">
        <div className="theme-picker-copy">
          <span id="theme-picker-title">大厅主题</span>
          <small>选择船主窗外的景象，工作内容不会改变</small>
        </div>
        <div className="theme-options">
          <button className={`theme-option theme-preview-space${theme === 'deep-space' ? ' on' : ''}`} onClick={() => setTheme('deep-space')}>
            <span className="theme-preview-art" aria-hidden><i /><i /><i /></span>
            <strong>深空</strong><small>粒子星环</small>
          </button>
          <button className={`theme-option theme-preview-mist${theme === 'mist' ? ' on' : ''}`} onClick={() => setTheme('mist')}>
            <span className="theme-preview-art" aria-hidden><i /><i /><i /></span>
            <strong>雾中醒来</strong><small>亮雾与人员雾蝶</small>
          </button>
        </div>
      </section>
      <div className="set-console">
        <div className="set-side">
          <span className="set-title">星球手感</span>
          <span className="set-sub">实时生效</span>
        </div>
        {项们.map(({ 名, lo, hi, 说 }) => {
          const v = Number(星球参[名])
          const pct = ((v - lo) / (hi - lo)) * 100
          return (
            <div className="set-col" key={名}>
              <div className="set-top"><span className="set-k">{名}</span><span className="set-hint">{说}</span></div>
              <input type="range" min={lo} max={hi} step={0.05} value={v}
                style={{ background: `linear-gradient(90deg, rgba(232,163,76,.75) ${pct}%, rgba(255,255,255,.1) ${pct}%)` }}
                onChange={(e) => 改(名, Number(e.target.value))} />
              <span className="set-v">{v.toFixed(2)}</span>
            </div>
          )
        })}
        <div className="set-side end">
          <button className="text-action" onClick={() => {
            const 演 = (window as unknown as Record<string, unknown>).星球演示 as ((i: number) => void) | undefined
            if (演) [0, 1, 2, 3, 4].forEach((i, k) => setTimeout(() => 演(i), k * 450))
          }}>演示过渡</button>
          <button className="text-action dim" onClick={() => { (['转速','亮度','数量','大小','呼吸','闪烁'] as const).forEach((k) => 改(k, 1)) }}>恢复默认</button>
        </div>
      </div>
    </main>
  )
}
