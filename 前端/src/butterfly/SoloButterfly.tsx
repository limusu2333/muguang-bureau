import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { buildFlightModel, GRAVITY, type FlightModel } from './flightModel'

const MODEL_URL = '/assets/butterfly/organic-butterfly-v2.glb'

export type Mood = 'cruise' | 'thinking'

// The physics runs in SI; this is the only place the clock is touched.
//
// It is not a cosmetic filter. Slowing an aerial creature's clock by k is what
// scaling it up by 1/k^2 in length does — Froude scaling: bigger fliers cruise
// faster in absolute terms but beat slower and, seen at their own size, move more
// slowly. The active 0.4 scale is not "the butterfly in slow motion", it is a
// creature roughly six times the span, which is exactly the register the brief is
// after: something with heft that beats a few times and then rides it out. Idle
// cruise is slower still; the whole clock slows together so wingbeats and travel
// remain causally linked.
//
// The order matters, and an earlier build got it backwards. Slowing down a flight
// that was itself unstable — diving away, stalling at the top — just gave a
// leisurely view of a bad flight. The wing loading (see CRUISE_SPEED) had to be
// fixed first so the underlying trajectory was smooth and had momentum; only then
// does stretching the clock read as size rather than as sluggishness.
//
// Sourced, since the target is a look: Reiter & Moore 2024 (arXiv:2404.16985)
// tested butterfly flight on viewers and found low flap frequency and a greater
// share of gliding rated more appealing than the physically accurate rate. The
// measured 11.9 Hz beat lands near 4.8 Hz on screen, with glides several times
// longer than the strokes that buy them.
const CRUISE_TIME_SCALE = 0.23
const THINKING_TIME_SCALE = 0.4
// How much stage the creature gets, expressed as how big it draws. At 900 the
// frame was 1.5 m across — about 15 wingspans — and a settled glide covers 3.5 m,
// so it never had room to open out: it spent the whole circuit hard over on a 4 cm
// turn radius, could not accumulate speed, and every glide therefore began too slow
// to be carried. At 480 the frame is ~2.9 m, near 30 wingspans, which is closer to
// what watching a butterfly cross a room actually looks like — and to the way
// creatures read in the films this is aiming at, small in a wide sky rather than
// filling the lens.
const PIXELS_PER_METRE = 480

// The beat is not written down here — the flight model measures it from the wings
// (climbHz), so it tracks the asset instead of a guess. Thinking just leans on it.
const THINKING_BEAT = 1.18
// Beat until it is above the top of the arena, then glide until it is down at the
// bottom. Not a band around the route line — a band across the whole stage.
//
// This is the difference between gliding and falling, and it is a matter of time.
// Released at any speed, the glide takes 1 to 1.5 s to settle: only then does
// vertical acceleration reach zero and the sink lock onto a steady 11 degrees.
// Before that it is still in the transient, dropping at 0.6 to 0.7 m/s^2 — which
// is exactly what falling looks like. The earlier band was 3.5 cm deep, and at a
// settled sink of 0.17 m/s the creature fell out of it in 0.2 s. It never once
// reached equilibrium; it just replayed the first fifth of a second of the
// transient over and over, which is why it read as dropping rather than gliding.
// Fractions of the stage half-height. The ceiling must sit under what the creature
// can actually climb to — measured, it tops out at 0.13 m — and this is worth being
// exact about: at 0.14 m it missed by a single centimetre, the height gate opened on
// 0 frames out of 3015, and the beat cap ended every climb instead. The cap fires at
// a stroke boundary, which is the trough of the speed swing, so the glide was handed
// a creature doing 0.45 m/s with wings trimmed to carry 0.9 — and it fell, every
// time. The speed gate was never the problem; it was passing on 65% of frames.
// Floor to ceiling is ~0.55 m, which at the settled 0.17 m/s sink is over 3 s of glide.
const GLIDE_CEILING = 0.15
const GLIDE_FLOOR = -0.72
// Room to level off under the ceiling without dropping out of the height condition.
const CEILING_SLACK = 0.06
// Enough beats to actually get up there and then wind the speed back on.
const MIN_BEATS = 6
const MAX_BEATS = 60

// Normalised circuit, matching the drawn route: climb out of the low left, hold a
// long glide across the top, bank down the right, run home low. This is only an
// intent — position always comes from integrating force.
const ROUTE = [
  new THREE.Vector3(-0.92, -0.62, 0.30),
  new THREE.Vector3(-0.74, -0.10, 0.05),
  new THREE.Vector3(-0.38, 0.44, -0.22),
  new THREE.Vector3(0.10, 0.62, -0.35),
  new THREE.Vector3(0.62, 0.50, -0.20),
  new THREE.Vector3(0.92, 0.06, 0.10),
  new THREE.Vector3(0.74, -0.48, 0.32),
  new THREE.Vector3(0.18, -0.70, 0.34),
  new THREE.Vector3(-0.46, -0.74, 0.30),
]

export type Telemetry = {
  t: number
  mode: string
  pos: [number, number, number]
  vel: [number, number, number]
  speed: number
  wingPhase: number
  downstroke: number
  accelY: number
  /** Beat the governor actually asked for this frame, Hz. */
  flapHz: number
  /** Vertical aerodynamic force this frame, as a multiple of body weight. */
  liftPerWeight: number
  forward: [number, number, number]
  pitch: number
  bank: number
  headingErrorDeg: number
}

export type FlightRegion = {
  left: number
  right: number
  top: number
  bottom: number
}

const DEFAULT_FLIGHT_REGION: FlightRegion = { left: 0, right: 1, top: 0, bottom: 1 }

function damp(value: number, target: number, rate: number, dt: number) {
  return value + (target - value) * (1 - Math.exp(-rate * dt))
}

export default function SoloButterfly({
  mood = 'cruise',
  onTelemetry,
  showHud = true,
  showStatus = true,
  showMarker = true,
  className = '',
  onScreenPosition,
  flightRegion = DEFAULT_FLIGHT_REGION,
  appearance = 'natural',
}: {
  mood?: Mood
  onTelemetry?: (t: Telemetry) => void
  showHud?: boolean
  showStatus?: boolean
  showMarker?: boolean
  className?: string
  onScreenPosition?: (x: number, y: number) => void
  flightRegion?: FlightRegion
  appearance?: 'natural' | 'mist'
}) {
  const stageRef = useRef<HTMLDivElement | null>(null)
  const markerRef = useRef<HTMLDivElement | null>(null)
  const moodRef = useRef<Mood>(mood)
  const telemetryRef = useRef<((t: Telemetry) => void) | undefined>(onTelemetry)
  const screenPositionRef = useRef<((x: number, y: number) => void) | undefined>(onScreenPosition)
  const [status, setStatus] = useState('载入模型…')
  const [hud, setHud] = useState<Telemetry | null>(null)

  moodRef.current = mood
  telemetryRef.current = onTelemetry
  screenPositionRef.current = onScreenPosition

  useEffect(() => {
    const stage = stageRef.current
    if (!stage) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')

    // A canvas keeps its WebGL context for life, and releasing it on unmount (as
    // we must) makes that canvas unusable. So each mount gets its own.
    const canvas = document.createElement('canvas')
    canvas.className = 'solo-canvas'
    stage.insertBefore(canvas, stage.firstChild)

    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' })
    renderer.setClearColor(0x000000, 0)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.8))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(35, 1, 1, 6000)
    scene.add(new THREE.HemisphereLight(0xf8fcff, 0x8399aa, 2.1))
    const key = new THREE.DirectionalLight(0xffffff, 1.5)
    key.position.set(-260, 210, 520)
    scene.add(key)
    const rim = new THREE.DirectionalLight(0xb8d9eb, 1.0)
    rim.position.set(340, -100, 260)
    scene.add(rim)

    let disposed = false
    let raf = 0
    let width = 1
    let height = 1
    let cleanupExtra: (() => void) | null = null

    const resize = () => {
      width = canvas.clientWidth || window.innerWidth
      height = canvas.clientHeight || window.innerHeight
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.position.set(0, 0, (height * 0.5) / Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5)))
      camera.lookAt(0, 0, 0)
      camera.updateProjectionMatrix()
    }
    resize()
    window.addEventListener('resize', resize)

    const loader = new GLTFLoader()
    loader.load(MODEL_URL, (gltf) => {
      try {
      if (disposed) return
      const clip = gltf.animations[0]
      if (!clip) { setStatus('模型没有骨骼动画，无法飞行'); return }

      const model = gltf.scene
      const materials: THREE.MeshStandardMaterial[] = []
      model.traverse((o) => {
        const mesh = o as THREE.Mesh
        if (!mesh.isMesh) return
        const list = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
        const tuned = list.map((m) => {
          const mat = (m as THREE.MeshStandardMaterial).clone()
          mat.side = THREE.DoubleSide
          mat.transparent = true
          mat.depthWrite = false
          if (appearance === 'mist') {
            // Keep the model's volume and normal detail, but remove the monarch
            // pigment entirely so it belongs to the same blue-grey fog as the
            // distant butterflies behind it.
            mat.map = null
            mat.alphaMap = null
            mat.color.set(0x7a8fa3)
            mat.emissive.set(0xaebdca)
            mat.emissiveIntensity = 0.08
            mat.opacity = 0.52
            mat.roughness = 0.68
          } else {
            mat.opacity = 0.9
            mat.roughness = 0.44
          }
          mat.metalness = 0
          mat.needsUpdate = true
          return mat
        })
        mesh.material = Array.isArray(mesh.material) ? tuned : tuned[0]
        mesh.frustumCulled = false
        materials.push(...tuned)
      })

      const flight = buildFlightModel(model, clip)
      if (!flight) { setStatus('无法从模型读出翅膀几何，飞行模型未建立'); return }

      // Size on screen. The model's own span is normalised inside the flight
      // model, so scale the presentation from the measured bind width.
      model.updateMatrixWorld(true)
      const bounds = new THREE.Box3().setFromObject(model)
      const modelSpan = Math.max(1e-4, bounds.max.x - bounds.min.x)
      const presentation = new THREE.Group()
      presentation.add(model)
      presentation.scale.setScalar((flight.span * PIXELS_PER_METRE) / modelSpan)

      const body = new THREE.Group()
      body.add(presentation)
      scene.add(body)

      setStatus(`飞行模型已建立 · 质量 ${(flight.mass * 1000).toFixed(3)} g · 翼面 ${(flight.wingArea * 1e4).toFixed(2)} cm² · 悬停 ${flight.hoverHz.toFixed(1)}Hz / 爬升 ${flight.climbHz.toFixed(1)}Hz · 滑翔升阻比 ${flight.glideRatio.toFixed(2)}（下滑 ${(Math.atan2(1, flight.glideRatio) * 57.3).toFixed(0)}°）`)

      // --- flight state, all in SI metres ---
      const position = new THREE.Vector3()
      const velocity = new THREE.Vector3(0, 0, 0)
      const attitude = new THREE.Quaternion()
      let phase = flight.glidePhase
      let powered = true
      let beatsLeft = 0
      let pitch = 0
      let bank = 0
      let strokeTilt = 0
      let simTime = 0
      let routeIndex = 0
      let lastAccelY = 0
      let lastLiftY = 0
      let lastFlapHz = 0
      let lastShortOfHeight = 0
      let timeScale = moodRef.current === 'thinking' ? THINKING_TIME_SCALE : CRUISE_TIME_SCALE

      const route: THREE.Vector3[] = ROUTE.map((v) => v.clone())
      let glideCeiling = 0
      let glideFloor = 0
      let flightMinX = 0
      let flightMaxX = 0
      let flightMinY = 0
      let flightMaxY = 0
      const scaleRoute = () => {
        const leftBound = THREE.MathUtils.clamp(flightRegion.left, 0, 0.9)
        const rightBound = THREE.MathUtils.clamp(flightRegion.right, leftBound + 0.08, 1)
        const topBound = THREE.MathUtils.clamp(flightRegion.top, 0, 0.9)
        const bottomBound = THREE.MathUtils.clamp(flightRegion.bottom, topBound + 0.08, 1)
        const centreX = (((leftBound + rightBound) * 0.5) - 0.5) * width / PIXELS_PER_METRE
        const centreY = (0.5 - ((topBound + bottomBound) * 0.5)) * height / PIXELS_PER_METRE
        const halfW = ((rightBound - leftBound) * width * 0.5) / PIXELS_PER_METRE
        const halfH = ((bottomBound - topBound) * height * 0.5) / PIXELS_PER_METRE
        const routeHalfW = halfW * (appearance === 'mist' ? 0.84 : 0.92)
        const routeHalfH = halfH * (appearance === 'mist' ? 0.64 : 0.68)
        ROUTE.forEach((v, i) => route[i].set(
          centreX + v.x * routeHalfW,
          centreY + v.y * routeHalfH,
          v.z * 0.06,
        ))
        glideCeiling = centreY + GLIDE_CEILING * routeHalfH
        glideFloor = centreY + GLIDE_FLOOR * routeHalfH

        // An early steering margin normally keeps the animal away from these
        // limits. These hard limits are only the last guard against a rare large
        // integration excursion: the rendered butterfly must never enter the
        // navigation, sessions, crew or greeting text.
        const insetX = Math.min(64, width * (rightBound - leftBound) * 0.09)
        const insetY = Math.min(54, height * (bottomBound - topBound) * 0.10)
        flightMinX = ((leftBound * width + insetX) - width * 0.5) / PIXELS_PER_METRE
        flightMaxX = ((rightBound * width - insetX) - width * 0.5) / PIXELS_PER_METRE
        flightMinY = (height * 0.5 - (bottomBound * height - insetY)) / PIXELS_PER_METRE
        flightMaxY = (height * 0.5 - (topBound * height + insetY)) / PIXELS_PER_METRE
      }
      scaleRoute()
      position.copy(route[0])
      velocity.set(0.35, 0.1, 0)

      const forward = new THREE.Vector3(0, 0, 1)
      const prevForward = new THREE.Vector3(0, 0, 1)
      const bodyAxis = new THREE.Vector3(0, 0, 1) // the head axis actually rendered
      const desired = new THREE.Vector3()
      const up = new THREE.Vector3(0, 1, 0)
      const left = new THREE.Vector3()
      const bodyVel = new THREE.Vector3()
      const forceBody = new THREE.Vector3()
      const forceWorld = new THREE.Vector3()
      const accel = new THREE.Vector3()
      const basis = new THREE.Matrix4()
      const targetQuat = new THREE.Quaternion()
      const invAttitude = new THREE.Quaternion()
      const toTarget = new THREE.Vector3()
      const prevPos = new THREE.Vector3()
      const featherQuat = new THREE.Quaternion()
      const spanAxis = new THREE.Vector3(1, 0, 0)
      const projectedBody = new THREE.Vector3()

      const step = (dt: number) => {
        const mood = moodRef.current
        const moodBeat = mood === 'thinking' ? THINKING_BEAT : 1

        // How hard to beat. Flat-out every time is what makes it look panicked: it
        // rockets up, spends its speed on height, and hangs at the top with nothing
        // left, which reads as flailing rather than flying. Animals meter the
        // stroke against what they are short of — height, or speed — so this does
        // too, and the result is that it only works as hard as it needs to.
        const shortOfHeight = glideCeiling - position.y
        lastShortOfHeight = shortOfHeight
        const shortOfSpeed = flight.cruiseSpeed - Math.hypot(velocity.x, velocity.z)
        const flapHz = THREE.MathUtils.clamp(
          (flight.hoverHz * 1.04 + shortOfHeight * 7 + Math.max(0, shortOfSpeed) * 5) * moodBeat,
          flight.hoverHz * 0.94,
          flight.climbHz * moodBeat,
        )
        lastFlapHz = flapHz

        // --- route as intent -----------------------------------------------
        // Progress is claimed by real travel, never by a clock: reaching the next
        // waypoint requires actually getting near it.
        const target = route[routeIndex]
        toTarget.copy(target).sub(position)
        // Capture radius has to exceed the turn radius, or the butterfly orbits a
        // waypoint it can never quite touch, banks hard forever, and loses the lift
        // that bank costs it. Also let go of a waypoint once it is behind us.
        if (toTarget.length() < 0.14 || (toTarget.lengthSq() < 0.16 && toTarget.dot(velocity) < 0)) {
          routeIndex = (routeIndex + 1) % route.length
        }

        // Heading is horizontal only. Aiming the nose at a waypoint overhead points
        // the whole animal skyward, which lays its lift on its side and leaves it
        // hovering nose-up, unable to climb to the very waypoint it is chasing.
        // A butterfly climbs on its wings; its heading only says which way round.
        desired.copy(route[routeIndex]).sub(position)
        desired.y = 0
        if (desired.lengthSq() < 1e-8) desired.set(forward.x, 0, forward.z)
        desired.normalize()

        // --- attitude: bank is the only steering input ------------------------
        // The head is not slewed onto the target. It rides the horizontal velocity,
        // and the butterfly turns the way a flying animal does: bank rolls the lift
        // vector over, its sideways component pulls the flight path round, and the
        // heading follows because that is where the body is now going. Slewing the
        // head directly instead leaves the momentum pointing the old way — the
        // butterfly crabs sideways, which is the sideways drift this rebuild exists
        // to kill.
        prevForward.copy(forward)
        if (Math.hypot(velocity.x, velocity.z) > 0.04) forward.set(velocity.x, 0, velocity.z).normalize()

        // Sign: rolling by +bank about the heading tilts lift toward -X for a +Z
        // heading, so a target off to +X needs negative bank to be pulled in.
        // Hands off entirely while gliding. Left in isolation the glide is flawless —
        // it settles to a dead-level 11 degrees at 0.89 m/s and holds there for as
        // long as you let it. Inside the controller it was tumbling within half a
        // second, angle of attack running away to 90 and then 150 degrees. The glide
        // was not the problem; being steered was. Four loops were all pulling at it at
        // once — heading chasing velocity, bank chasing waypoints, pitch chasing the
        // path, the attitude slerp lagging behind all of them — and a force balance
        // that fine does not survive being hauled about. So the creature does what a
        // gliding animal does: it glides, and saves the turning for when it is beating
        // its wings and has thrust to spend on it.
        const yawErr = forward.z * desired.x - forward.x * desired.z
        bank = powered
          ? damp(bank, THREE.MathUtils.clamp(-yawErr * 1.6, -0.5, 0.5), 3.2, dt)
          : damp(bank, THREE.MathUtils.clamp(-yawErr * 0.6, -0.24, 0.24), 1.1, dt)

        // Nose rides the flight path, so the head points along where it is actually
        // going rather than at the next waypoint. On top of that sits the
        // stroke-coupled bob: Chen et al. drive the thorax a quarter cycle out of
        // phase with the wings, which is what the cosine term does. Bode-Oke & Dong
        // 2020 measure a per-halfstroke swing of -17 to +33 deg on a real monarch;
        // this is deliberately shallower, since at full depth the head no longer
        // reads as tracking the flight path.
        // Pitch mostly rides the flight path, so the head points along the way it is
        // actually going. But it is a control, not just a follower, and the
        // difference matters: lift comes out of the body's back, so a nose pointed
        // steeply down lays that lift over and keeps almost none of it vertical.
        // Let the nose follow a dive freely and it never recovers — sinking tips the
        // nose down, which costs vertical lift, which deepens the sink. Measured: a
        // stroke worth 3.7 body weights on the bench was delivering 1.0 in flight,
        // purely through attitude. So while it still owes itself height, the nose is
        // held up: that is the creature pulling out of the dive.
        // Only clamp the nose while beating. In a glide the nose has to sit exactly
        // on the flight path, because that is the attitude the glide pose was trimmed
        // at — hold it up and the wing meets the air at the wrong angle and the whole
        // 11-degree balance is gone.
        const horizontalSpeed = Math.hypot(velocity.x, velocity.z)
        const pathAngle = Math.atan2(velocity.y, Math.max(0.02, horizontalSpeed))
        const strokePitch = powered ? 0.06 * Math.cos(2 * Math.PI * phase - Math.PI / 2) : 0
        // The two states want opposite things from the nose, and one rule for both is
        // why this fought itself for so long.
        //
        // Gliding: pin the nose to the flight path, hard. That is the attitude the
        // glide pose is trimmed at, and any lag leaves the wing riding nose-up into
        // its own descent — the angle of attack creeps up, drag with it, and the
        // airspeed collapses into a mush and then a tumble. The tell in the traces was
        // speed halving while lift sat unmoved at 0.96 of weight, which can only
        // happen if the angle of attack is quietly climbing to prop it up.
        //
        // Beating: keep the nose near level instead. Lift comes off the creature's
        // back, so a nose pitched 46 degrees up to "follow" a climb lays that lift on
        // its side and the climb dies — that is the wing loading being thrown away to
        // point prettily. It rides the path only a little, and gently, which is also
        // where the stroke bob belongs.
        const pitchTarget = powered
          ? THREE.MathUtils.clamp(pathAngle * 0.45, -0.22, 0.22) + strokePitch
          : THREE.MathUtils.clamp(pathAngle, -1.1, 0.6)
        pitch = damp(pitch, pitchTarget, powered ? 11 : 38, dt)

        const flightForward = desired.copy(forward).normalize()
        up.set(0, 1, 0)
        left.crossVectors(up, flightForward)
        if (left.lengthSq() < 1e-6) left.set(1, 0, 0)
        left.normalize()
        up.crossVectors(flightForward, left).normalize()
        // Pitch about the span axis, then roll the up vector about the heading.
        // Negated: rotating the heading about `left` by a positive angle drops the
        // nose, while `pitch` tracks the flight-path angle, which is negative in a
        // descent. Without the sign flip the nose lifted by exactly as much as the
        // path fell, so the angle of attack came out at twice the descent angle
        // instead of zero — measured 18 degrees on a 10-degree path. That is what
        // was killing every glide: the steeper it sank the further the nose pointed
        // the wrong way, drag ran away with the airspeed, and it mushed and tumbled.
        flightForward.applyAxisAngle(left, -pitch).normalize()
        up.crossVectors(flightForward, left).normalize()
        up.applyAxisAngle(flightForward, bank).normalize()
        left.crossVectors(up, flightForward).normalize()

        // Measured from the asset, not read off a screenshot: the model's local
        // +Z runs abdomen to head, +Y is dorsal up and +X spans the wings toward
        // the left tip. Mapping +X to the heading is what made earlier builds
        // translate sideways.
        basis.makeBasis(left, up, flightForward)
        targetQuat.setFromRotationMatrix(basis)
        attitude.slerp(targetQuat, 1 - Math.exp(-(powered ? 13 : 34) * dt))
        bodyAxis.set(0, 0, 1).applyQuaternion(attitude)

        // --- stroke ---------------------------------------------------------
        // Whole beats only, so the rhythm reads as deliberate strokes: it works its
        // way up to the ceiling, then shuts the wings down and rides all the way to
        // the floor.
        // Height alone is not enough to stop on. The glide only settles quickly if it
        // is entered at something near trim speed — released at 0.9 m/s it is level
        // within a second, released at 0.6 it pitches into the transient at nearly
        // -5.5 m/s^2 first. Climbing spends speed, so stopping the moment the ceiling
        // arrives hands the glide a creature that is too slow to be carried, and it
        // simply drops. Wait for the speed as well and the glide starts already flying.
        const prevPhase = phase
        if (powered) {
          phase = (phase + flapHz * dt) % 1
          if (phase < prevPhase) beatsLeft += 1
          // Checked every frame, not only on a stroke boundary. Speed swings from
          // 0.44 to over 1.1 within a single beat, and the boundary lands on the
          // trough of that swing — so waiting for one meant systematically picking
          // the slowest instant in the cycle to fold the wings. Entry speed is the
          // whole game: too slow and the glide spends its entire length falling
          // through the transient and hits the floor before it can ever settle.
          // The cycle is climb, level off, build speed, let go — and it has to be all
          // four. Demanding height and speed at the same instant asks for something
          // impossible, since climbing is bought with speed and the highest moment is
          // the slowest one; that pair fired on 0 of 3015 frames. Waiting on speed
          // alone is worse in the other direction: it lets go the moment it is quick,
          // long before it has climbed, and then just skims the floor with nothing to
          // spend. So: get up to the ceiling, and once there the governor stops asking
          // for height and starts asking for speed — wait for that, then fold. The
          // slack under the ceiling is because levelling off costs a little altitude
          // and it must not drop out of the condition it just met.
          const highEnough = position.y > glideCeiling - CEILING_SLACK
          // At trim, not below it. Entering at 0.72 of trim looked survivable and is
          // not: the glide comes in on the nose-up attitude left over from beating,
          // holds a textbook 11 degrees for about 0.2 s on that borrowed angle of
          // attack, and then the nose settles onto the flight path, the angle of
          // attack drops back to what the pose was trimmed for, lift falls to
          // (0.72)^2 of weight, and it mushes — sinking, steepening, and finally
          // diving away at 60 degrees. Below trim speed there is no glide to be had,
          // only a slower way of falling. Powered flight reaches this on better than
          // 10% of frames, so it is worth waiting for.
          const fastEnough = horizontalSpeed > flight.cruiseSpeed
          // Levelled off too — nothing that flies folds its wings while still going
          // up, and entering on a climb hands the glide pose an angle it was never
          // trimmed for.
          const levelledOff = velocity.y < 0.2
          if (beatsLeft >= MIN_BEATS && ((highEnough && fastEnough && levelledOff) || beatsLeft >= MAX_BEATS)) {
            powered = false
          }
        } else {
          phase = flight.glidePhase
        }

        // --- forces ---------------------------------------------------------
        // Thrust comes from the wing sweep inside the flight model, not from
        // aiming the body. Tilting the stroke plane was measured and rejected:
        // even at 45 deg it never turned thrust positive, it only traded lift away.
        invAttitude.copy(attitude).invert()
        bodyVel.copy(velocity).applyQuaternion(invAttitude)
        flight.force(phase, powered ? flapHz : 0, bodyVel, forceBody)
        forceWorld.copy(forceBody).applyQuaternion(attitude)
        lastLiftY = forceWorld.y

        accel.copy(forceWorld).multiplyScalar(1 / flight.mass)
        accel.y -= GRAVITY // world gravity: never rotates with the path
        lastAccelY = accel.y

        velocity.addScaledVector(accel, dt)
        prevPos.copy(position)
        position.addScaledVector(velocity, dt)

        // The route is physical intent, but the hall has protected reading areas.
        // Keep an emergency envelope outside the normal route so a gust or a long
        // glide cannot carry the animal over UI text. On contact it loses energy
        // and resumes powered flight back into the circuit instead of teleporting
        // to another waypoint.
        let touchedEnvelope = false
        if (position.x < flightMinX) {
          position.x = flightMinX
          velocity.x = Math.abs(velocity.x) * 0.24
          touchedEnvelope = true
        } else if (position.x > flightMaxX) {
          position.x = flightMaxX
          velocity.x = -Math.abs(velocity.x) * 0.24
          touchedEnvelope = true
        }
        if (position.y < flightMinY) {
          position.y = flightMinY
          velocity.y = Math.abs(velocity.y) * 0.28
          touchedEnvelope = true
        } else if (position.y > flightMaxY) {
          position.y = flightMaxY
          velocity.y = -Math.abs(velocity.y) * 0.22
          touchedEnvelope = true
        }
        if (touchedEnvelope) {
          powered = true
          beatsLeft = 0
          phase = 0
        }

        // --- when to spend energy again -------------------------------------
        // Height decides, not a timer, and the floor is the whole point: the glide
        // is allowed to run its full length. Speed only intervenes if it is about to
        // fall out of the sky, which the settled glide never does — it holds 0.89 m/s
        // on its own.
        if (!powered && (position.y < glideFloor || velocity.length() < 0.18)) {
          powered = true
          beatsLeft = 0
          phase = 0
        }

        simTime += dt
        return prevPhase
      }

      let last = performance.now()
      let hudAccum = 0
      const strokeSpeed = () => flight.strokeVelocity(phase)

      const advance = (wall: number) => {
        // Fixed sim step keeps the integration stable regardless of frame rate.
        const targetTimeScale = moodRef.current === 'thinking' ? THINKING_TIME_SCALE : CRUISE_TIME_SCALE
        timeScale = damp(timeScale, targetTimeScale, 2.8, wall)
        const simDt = wall * timeScale
        const SUB = 1 / 480
        let remaining = simDt
        let guard = 0
        while (remaining > 1e-6 && guard++ < 64) {
          const dt = Math.min(SUB, remaining)
          step(dt)
          remaining -= dt
        }

        // The wings are posed by the flight model itself, from the same phase the
        // forces were integrated from. There is no second clock to drift against.
        flight.poseWings(phase, !powered)

        body.position.set(position.x * PIXELS_PER_METRE, position.y * PIXELS_PER_METRE, position.z * PIXELS_PER_METRE)
        body.quaternion.copy(attitude)
        const focus = moodRef.current === 'thinking' ? 1.12 : 1
        body.scale.setScalar(focus)

        renderer.render(scene, camera)

        // Marker tracks the real projected position, so the hit area cannot be
        // left behind where the butterfly used to be.
        const marker = markerRef.current
        projectedBody.copy(body.position).project(camera)
        const screenX = (projectedBody.x * 0.5 + 0.5) * width
        const screenY = (-projectedBody.y * 0.5 + 0.5) * height
        if (marker) {
          marker.style.transform = `translate3d(${screenX.toFixed(1)}px, ${screenY.toFixed(1)}px, 0)`
        }
        screenPositionRef.current?.(screenX, screenY)

        const down = Math.max(0, -strokeSpeed())
        const t: Telemetry = {
          t: simTime,
          mode: powered ? 'powered' : 'glide',
          pos: [position.x, position.y, position.z],
          vel: [velocity.x, velocity.y, velocity.z],
          speed: velocity.length(),
          wingPhase: phase,
          downstroke: down,
          accelY: lastAccelY,
          flapHz: lastFlapHz,
          liftPerWeight: lastLiftY / (flight.mass * GRAVITY),
          forward: [bodyAxis.x, bodyAxis.y, bodyAxis.z],
          pitch,
          bank,
          headingErrorDeg: velocity.length() > 1e-3
            ? THREE.MathUtils.radToDeg(bodyAxis.angleTo(velocity.clone().normalize()))
            : 0,
        }
        telemetryRef.current?.(t)
        hudAccum += wall
        if (showHud && hudAccum > 0.12) { hudAccum = 0; setHud(t) }
        return t
      }

      const frame = (now: number) => {
        if (disposed) return
        advance(Math.min(0.05, Math.max(0.0005, (now - last) / 1000)))
        last = now
        raf = requestAnimationFrame(frame)
      }

      const start = () => { if (!raf && !disposed) { last = performance.now(); raf = requestAnimationFrame(frame) } }
      const stop = () => { if (raf) { cancelAnimationFrame(raf); raf = 0 } }
      // ?always keeps it flying while unfocused, so the flight can be recorded
      // and inspected. Without it the page idles exactly as the brief requires.
      const alwaysRun = new URLSearchParams(location.search).has('always')
      const visibility = () => (!alwaysRun && (document.hidden || reduced.matches) ? stop() : start())

      // Browsers suspend requestAnimationFrame entirely in a background tab, so
      // an automated check can never watch the loop run. Under ?always the real
      // controller is also drivable by hand, which is what produces the motion log.
      if (alwaysRun) {
        const dbg = window as unknown as {
          __butterflyTick?: (dt: number) => Telemetry
          __butterflyModel?: FlightModel
          __butterflyVec?: (x: number, y: number, z: number) => THREE.Vector3
        }
        dbg.__butterflyTick = (dt) => advance(dt)
        dbg.__butterflyModel = flight
        dbg.__butterflyVec = (x, y, z) => new THREE.Vector3(x, y, z)
      }
      const onBlur = () => { if (!alwaysRun) stop() }
      document.addEventListener('visibilitychange', visibility)
      window.addEventListener('blur', onBlur)
      window.addEventListener('focus', start)
      reduced.addEventListener('change', visibility)
      const onResize = () => { resize(); scaleRoute() }
      window.addEventListener('resize', onResize)
      visibility()

      cleanupExtra = () => {
        stop()
        delete (window as unknown as { __butterflyTick?: unknown; __butterflyModel?: unknown }).__butterflyTick
        delete (window as unknown as { __butterflyModel?: unknown }).__butterflyModel
        document.removeEventListener('visibilitychange', visibility)
        window.removeEventListener('blur', onBlur)
        window.removeEventListener('focus', start)
        window.removeEventListener('resize', onResize)
        reduced.removeEventListener('change', visibility)
        flight.dispose()
        for (const m of materials) m.dispose()
        scene.traverse((o) => {
          const mesh = o as THREE.Mesh
          if (mesh.isMesh) mesh.geometry?.dispose()
        })
      }
      } catch (err) {
        // Never fail silently to a blank canvas: say what broke, on the page.
        console.error('[butterfly] flight setup failed', err)
        setStatus(`飞行模型建立失败：${err instanceof Error ? `${err.message}\n${err.stack ?? ''}` : String(err)}`)
      }
    }, undefined, (err) => {
      setStatus(`蝴蝶模型加载失败：${err instanceof Error ? err.message : '未知错误'}`)
    })

    return () => {
      disposed = true
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      cleanupExtra?.()
      renderer.dispose()
      renderer.forceContextLoss()
      canvas.remove()
    }
  }, [appearance, flightRegion, showHud])

  return (
    <div className={`solo-stage${className ? ` ${className}` : ''}`} ref={stageRef}>
      {showMarker && <div ref={markerRef} className="solo-marker" aria-hidden />}
      {showStatus && <div className="solo-status">{status}</div>}
      {showHud && hud && (
        <div className="solo-hud">
          <div><b>{hud.mode === 'powered' ? '拍翼' : '滑翔'}</b> · t={hud.t.toFixed(1)}s</div>
          <div>速度 {hud.speed.toFixed(3)} m/s · 垂直 {hud.vel[1] >= 0 ? '+' : ''}{hud.vel[1].toFixed(3)}</div>
          <div>翅膀相位 {hud.wingPhase.toFixed(3)} · 下拍 {hud.downstroke.toFixed(3)}</div>
          <div>垂直加速度 {hud.accelY >= 0 ? '+' : ''}{hud.accelY.toFixed(2)} m/s²</div>
          <div>朝向误差 {hud.headingErrorDeg.toFixed(1)}° · 俯仰 {THREE.MathUtils.radToDeg(hud.pitch).toFixed(0)}° · 侧倾 {THREE.MathUtils.radToDeg(hud.bank).toFixed(0)}°</div>
        </div>
      )}
    </div>
  )
}
