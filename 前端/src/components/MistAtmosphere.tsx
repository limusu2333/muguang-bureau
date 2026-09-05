const butterflies = [
  { x: 18, y: 51, s: 0.78, c: 'blur', d: 24, delay: -7 },
  { x: 51, y: 58, s: 1.08, c: 'soft', d: 28, delay: -13 },
  { x: 76, y: 66, s: 1.3, c: 'far', d: 34, delay: -19 },
]

export default function MistAtmosphere() {
  return (
    <div className="mist-atmosphere" aria-hidden>
      <div className="mist-fog" />
      <div className="mist-veil-back" />
      <div className="mist-warmth" />
      <div className="mist-flock">
        {butterflies.map((b, i) => (
          <span
            className={`mist-butterfly ${b.c}`}
            key={i}
            style={{
              left: `${b.x}%`,
              top: `${b.y}%`,
              ['--bf-scale' as string]: String(b.s),
              ['--bf-duration' as string]: `${b.d}s`,
              ['--bf-delay' as string]: `${b.delay}s`,
            }}
          >
            <svg viewBox="0 0 60 48" focusable="false">
              <path d="M30 24C26 13 18 4 9 3 4 3 1 7 3 13c2 7 10 14 20 16-8 4-15 11-16 18-1 6 3 9 8 6 8-4 13-14 15-25 2 11 7 21 15 25 5 3 9 0 8-6-1-7-8-14-16-18 10-2 18-9 20-16 2-6-1-10-6-10-9 1-17 10-21 21Z" />
            </svg>
          </span>
        ))}
      </div>
      <svg className="mist-flow-layer" viewBox="0 0 1000 600" preserveAspectRatio="none">
        <defs>
          <linearGradient id="mist-theme-flow" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#8ca7ba" stopOpacity="0.06" />
            <stop offset="0.38" stopColor="#fff" stopOpacity="0.28" />
            <stop offset="0.7" stopColor="#a9bdcb" stopOpacity="0.14" />
            <stop offset="1" stopColor="#fff7ef" stopOpacity="0.18" />
          </linearGradient>
        </defs>
        <path className="mist-flow-cloud" d="M310 234 C350 184 390 174 430 168 C485 155 540 154 590 162 C650 172 690 202 704 234 C678 270 625 292 560 298 C490 302 420 284 362 258 C338 248 320 240 310 234 Z" />
        <path className="mist-flow-body" d="M310 234 C350 184 390 174 430 168 C485 155 540 154 590 162 C650 172 690 202 704 234 C678 270 625 292 560 298 C490 302 420 284 362 258 C338 248 320 240 310 234 Z" />
        <path className="mist-flow-glint" d="M310 234 C350 184 390 174 430 168 C485 155 540 154 590 162 C650 172 690 202 704 234 C678 270 625 292 560 298 C490 302 420 284 362 258 C338 248 320 240 310 234 Z" />
      </svg>
      <div className="mist-grain" />
    </div>
  )
}
