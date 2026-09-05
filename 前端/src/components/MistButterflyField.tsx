import { useEffect, useRef, type RefObject } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { clone as cloneSkeleton } from 'three/examples/jsm/utils/SkeletonUtils.js'
import { buildFlightModel, GRAVITY, type FlightModel } from '../butterfly/flightModel'
import { VISUAL_FPS, visualFrameDue, visualPixelRatio } from './visualPerformance'

export type MistButterflyConfig = {
  x: number
  y: number
  size: number
  opacity: number
  routeOffset: number
  timeScale: number
  flightRegion: FlightRegion
}

type FlightRegion = {
  left: number
  right: number
  top: number
  bottom: number
}

type Actor = {
  body: THREE.Group
  flight: FlightModel
  materials: THREE.MeshStandardMaterial[]
  node: HTMLElement | null
  config: MistButterflyConfig
  route: THREE.Vector3[]
  position: THREE.Vector3
  velocity: THREE.Vector3
  attitude: THREE.Quaternion
  forward: THREE.Vector3
  bodyAxis: THREE.Vector3
  desired: THREE.Vector3
  up: THREE.Vector3
  left: THREE.Vector3
  bodyVel: THREE.Vector3
  forceBody: THREE.Vector3
  forceWorld: THREE.Vector3
  accel: THREE.Vector3
  basis: THREE.Matrix4
  targetQuat: THREE.Quaternion
  invAttitude: THREE.Quaternion
  toTarget: THREE.Vector3
  projected: THREE.Vector3
  phase: number
  powered: boolean
  beatsLeft: number
  pitch: number
  bank: number
  simTime: number
  routeIndex: number
  timeScale: number
  glideCeiling: number
  glideFloor: number
  flightMinX: number
  flightMaxX: number
  flightMinY: number
  flightMaxY: number
}

const MODEL_URL = '/assets/butterfly/organic-butterfly-v2.glb'
const PIXELS_PER_METRE = 480
// Five visible employees should feel almost suspended while the company is
// idle. Work restores a readable flight rhythm, but remains calmer than the
// standalone flight study. Both values scale the whole physical clock, so wing
// strokes, lift, gravity and travel stay causally locked.
const CRUISE_TIME_SCALE = 0.055
const THINKING_TIME_SCALE = 0.24
const THINKING_BEAT = 1.18
const GLIDE_CEILING = 0.15
const GLIDE_FLOOR = -0.72
const CEILING_SLACK = 0.06
const MIN_BEATS = 6
const MAX_BEATS = 60

// The same physical circuit used by Claude's approved single-butterfly build.
// Each actor gets its own scaled copy and a different starting waypoint.
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

function damp(value: number, target: number, rate: number, dt: number) {
  return value + (target - value) * (1 - Math.exp(-rate * dt))
}

function tuneModel(model: THREE.Object3D, opacity: number) {
  const materials: THREE.MeshStandardMaterial[] = []
  model.traverse((object) => {
    const mesh = object as THREE.Mesh
    if (!mesh.isMesh) return
    const source = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    const tuned = source.map((material) => {
      const result = (material as THREE.MeshStandardMaterial).clone()
      result.side = THREE.DoubleSide
      result.transparent = true
      result.depthWrite = false
      result.map = null
      result.alphaMap = null
      result.color.set(0x9aabb9)
      result.emissive.set(0xc7d2da)
      result.emissiveIntensity = 0.04
      result.opacity = opacity
      result.roughness = 0.72
      result.metalness = 0
      result.needsUpdate = true
      return result
    })
    mesh.material = Array.isArray(mesh.material) ? tuned : tuned[0]
    mesh.frustumCulled = false
    materials.push(...tuned)
  })
  return materials
}

function scaleRoute(actor: Actor, width: number, height: number) {
  const region = actor.config.flightRegion
  const leftBound = THREE.MathUtils.clamp(region.left, 0, 0.9)
  const rightBound = THREE.MathUtils.clamp(region.right, leftBound + 0.08, 1)
  const topBound = THREE.MathUtils.clamp(region.top, 0, 0.9)
  const bottomBound = THREE.MathUtils.clamp(region.bottom, topBound + 0.08, 1)
  const centreX = (((leftBound + rightBound) * 0.5) - 0.5) * width / PIXELS_PER_METRE
  const centreY = (0.5 - ((topBound + bottomBound) * 0.5)) * height / PIXELS_PER_METRE
  const halfW = ((rightBound - leftBound) * width * 0.5) / PIXELS_PER_METRE
  const halfH = ((bottomBound - topBound) * height * 0.5) / PIXELS_PER_METRE
  const routeHalfW = halfW * 0.84
  const routeHalfH = halfH * 0.64

  ROUTE.forEach((point, index) => actor.route[index].set(
    centreX + point.x * routeHalfW,
    centreY + point.y * routeHalfH,
    point.z * 0.06,
  ))
  actor.glideCeiling = centreY + GLIDE_CEILING * routeHalfH
  actor.glideFloor = centreY + GLIDE_FLOOR * routeHalfH

  const insetX = Math.min(64, width * (rightBound - leftBound) * 0.09)
  const insetY = Math.min(54, height * (bottomBound - topBound) * 0.10)
  actor.flightMinX = ((leftBound * width + insetX) - width * 0.5) / PIXELS_PER_METRE
  actor.flightMaxX = ((rightBound * width - insetX) - width * 0.5) / PIXELS_PER_METRE
  actor.flightMinY = (height * 0.5 - (bottomBound * height - insetY)) / PIXELS_PER_METRE
  actor.flightMaxY = (height * 0.5 - (topBound * height + insetY)) / PIXELS_PER_METRE
}

function stepActor(actor: Actor, dt: number, thinking: boolean) {
  const { flight } = actor
  const moodBeat = thinking ? THINKING_BEAT : 1
  const shortOfHeight = actor.glideCeiling - actor.position.y
  const shortOfSpeed = flight.cruiseSpeed - Math.hypot(actor.velocity.x, actor.velocity.z)
  const flapHz = THREE.MathUtils.clamp(
    (flight.hoverHz * 1.04 + shortOfHeight * 7 + Math.max(0, shortOfSpeed) * 5) * moodBeat,
    flight.hoverHz * 0.94,
    flight.climbHz * moodBeat,
  )

  actor.toTarget.copy(actor.route[actor.routeIndex]).sub(actor.position)
  if (actor.toTarget.length() < 0.14 || (actor.toTarget.lengthSq() < 0.16 && actor.toTarget.dot(actor.velocity) < 0)) {
    actor.routeIndex = (actor.routeIndex + 1) % actor.route.length
  }
  actor.desired.copy(actor.route[actor.routeIndex]).sub(actor.position)
  actor.desired.y = 0
  if (actor.desired.lengthSq() < 1e-8) actor.desired.set(actor.forward.x, 0, actor.forward.z)
  actor.desired.normalize()

  if (Math.hypot(actor.velocity.x, actor.velocity.z) > 0.04) {
    actor.forward.set(actor.velocity.x, 0, actor.velocity.z).normalize()
  }
  const yawError = actor.forward.z * actor.desired.x - actor.forward.x * actor.desired.z
  actor.bank = actor.powered
    ? damp(actor.bank, THREE.MathUtils.clamp(-yawError * 1.6, -0.5, 0.5), 3.2, dt)
    : damp(actor.bank, THREE.MathUtils.clamp(-yawError * 0.6, -0.24, 0.24), 1.1, dt)

  const horizontalSpeed = Math.hypot(actor.velocity.x, actor.velocity.z)
  const pathAngle = Math.atan2(actor.velocity.y, Math.max(0.02, horizontalSpeed))
  const strokePitch = actor.powered ? 0.06 * Math.cos(2 * Math.PI * actor.phase - Math.PI / 2) : 0
  const pitchTarget = actor.powered
    ? THREE.MathUtils.clamp(pathAngle * 0.45, -0.22, 0.22) + strokePitch
    : THREE.MathUtils.clamp(pathAngle, -1.1, 0.6)
  actor.pitch = damp(actor.pitch, pitchTarget, actor.powered ? 11 : 38, dt)

  const flightForward = actor.desired.copy(actor.forward).normalize()
  actor.up.set(0, 1, 0)
  actor.left.crossVectors(actor.up, flightForward)
  if (actor.left.lengthSq() < 1e-6) actor.left.set(1, 0, 0)
  actor.left.normalize()
  actor.up.crossVectors(flightForward, actor.left).normalize()
  flightForward.applyAxisAngle(actor.left, -actor.pitch).normalize()
  actor.up.crossVectors(flightForward, actor.left).normalize()
  actor.up.applyAxisAngle(flightForward, actor.bank).normalize()
  actor.left.crossVectors(actor.up, flightForward).normalize()
  actor.basis.makeBasis(actor.left, actor.up, flightForward)
  actor.targetQuat.setFromRotationMatrix(actor.basis)
  actor.attitude.slerp(actor.targetQuat, 1 - Math.exp(-(actor.powered ? 13 : 34) * dt))
  actor.bodyAxis.set(0, 0, 1).applyQuaternion(actor.attitude)

  const previousPhase = actor.phase
  if (actor.powered) {
    actor.phase = (actor.phase + flapHz * dt) % 1
    if (actor.phase < previousPhase) actor.beatsLeft += 1
    const highEnough = actor.position.y > actor.glideCeiling - CEILING_SLACK
    const fastEnough = horizontalSpeed > flight.cruiseSpeed
    const levelledOff = actor.velocity.y < 0.2
    if (actor.beatsLeft >= MIN_BEATS && ((highEnough && fastEnough && levelledOff) || actor.beatsLeft >= MAX_BEATS)) {
      actor.powered = false
    }
  } else {
    actor.phase = flight.glidePhase
  }

  actor.invAttitude.copy(actor.attitude).invert()
  actor.bodyVel.copy(actor.velocity).applyQuaternion(actor.invAttitude)
  flight.force(actor.phase, actor.powered ? flapHz : 0, actor.bodyVel, actor.forceBody)
  actor.forceWorld.copy(actor.forceBody).applyQuaternion(actor.attitude)
  actor.accel.copy(actor.forceWorld).multiplyScalar(1 / flight.mass)
  actor.accel.y -= GRAVITY
  actor.velocity.addScaledVector(actor.accel, dt)
  actor.position.addScaledVector(actor.velocity, dt)

  let touchedEnvelope = false
  if (actor.position.x < actor.flightMinX) {
    actor.position.x = actor.flightMinX
    actor.velocity.x = Math.abs(actor.velocity.x) * 0.24
    touchedEnvelope = true
  } else if (actor.position.x > actor.flightMaxX) {
    actor.position.x = actor.flightMaxX
    actor.velocity.x = -Math.abs(actor.velocity.x) * 0.24
    touchedEnvelope = true
  }
  if (actor.position.y < actor.flightMinY) {
    actor.position.y = actor.flightMinY
    actor.velocity.y = Math.abs(actor.velocity.y) * 0.28
    touchedEnvelope = true
  } else if (actor.position.y > actor.flightMaxY) {
    actor.position.y = actor.flightMaxY
    actor.velocity.y = -Math.abs(actor.velocity.y) * 0.22
    touchedEnvelope = true
  }
  if (touchedEnvelope) {
    actor.powered = true
    actor.beatsLeft = 0
    actor.phase = 0
  }
  if (!actor.powered && (actor.position.y < actor.glideFloor || actor.velocity.length() < 0.18)) {
    actor.powered = true
    actor.beatsLeft = 0
    actor.phase = 0
  }
  actor.simTime += dt
}

function createActor(
  source: THREE.Group,
  clip: THREE.AnimationClip,
  config: MistButterflyConfig,
  node: HTMLElement | null,
  scene: THREE.Scene,
  width: number,
  height: number,
): Actor | null {
  const model = cloneSkeleton(source) as THREE.Group
  const materials = tuneModel(model, config.opacity)
  const flight = buildFlightModel(model, clip)
  if (!flight) {
    for (const material of materials) material.dispose()
    return null
  }

  model.updateMatrixWorld(true)
  const bounds = new THREE.Box3().setFromObject(model)
  const modelSpan = Math.max(1e-4, bounds.max.x - bounds.min.x)
  const presentation = new THREE.Group()
  presentation.add(model)
  presentation.scale.setScalar(((flight.span * PIXELS_PER_METRE) / modelSpan) * (config.size / 112))
  const body = new THREE.Group()
  body.add(presentation)
  scene.add(body)

  const actor: Actor = {
    body,
    flight,
    materials,
    node,
    config,
    route: ROUTE.map((point) => point.clone()),
    position: new THREE.Vector3(),
    velocity: new THREE.Vector3(),
    attitude: new THREE.Quaternion(),
    forward: new THREE.Vector3(0, 0, 1),
    bodyAxis: new THREE.Vector3(0, 0, 1),
    desired: new THREE.Vector3(),
    up: new THREE.Vector3(),
    left: new THREE.Vector3(),
    bodyVel: new THREE.Vector3(),
    forceBody: new THREE.Vector3(),
    forceWorld: new THREE.Vector3(),
    accel: new THREE.Vector3(),
    basis: new THREE.Matrix4(),
    targetQuat: new THREE.Quaternion(),
    invAttitude: new THREE.Quaternion(),
    toTarget: new THREE.Vector3(),
    projected: new THREE.Vector3(),
    phase: flight.glidePhase,
    powered: true,
    beatsLeft: 0,
    pitch: 0,
    bank: 0,
    simTime: 0,
    routeIndex: config.routeOffset % ROUTE.length,
    timeScale: CRUISE_TIME_SCALE * config.timeScale,
    glideCeiling: 0,
    glideFloor: 0,
    flightMinX: 0,
    flightMaxX: 0,
    flightMinY: 0,
    flightMaxY: 0,
  }
  scaleRoute(actor, width, height)
  actor.position.copy(actor.route[actor.routeIndex])
  const next = actor.route[(actor.routeIndex + 1) % actor.route.length]
  actor.forward.copy(next).sub(actor.position)
  actor.forward.y = 0
  actor.forward.normalize()
  actor.velocity.copy(actor.forward).multiplyScalar(0.35)
  actor.velocity.y = 0.08
  return actor
}

function disposeScene(scene: THREE.Object3D) {
  const geometries = new Set<THREE.BufferGeometry>()
  scene.traverse((object) => {
    const mesh = object as THREE.Mesh
    if (mesh.isMesh && mesh.geometry) geometries.add(mesh.geometry)
  })
  for (const geometry of geometries) geometry.dispose()
}

export default function MistButterflyField({
  rootRef,
  configs,
  targetFps = VISUAL_FPS.idle,
  onReady,
}: {
  rootRef: RefObject<HTMLDivElement | null>
  configs: MistButterflyConfig[]
  targetFps?: number
  onReady?: (theme: 'mist') => void
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const targetFpsRef = useRef(targetFps)
  const boostUntilRef = useRef(0)
  targetFpsRef.current = targetFps

  useEffect(() => {
    const canvas = canvasRef.current
    const rootElement = rootRef.current
    if (!canvas || !rootElement) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    const renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      powerPreference: 'default',
      premultipliedAlpha: true,
    })
    renderer.setClearColor(0x000000, 0)
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 0.98

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(35, 1, 1, 6000)
    scene.add(new THREE.HemisphereLight(0xfbfdff, 0x91a4b3, 2.05))
    const key = new THREE.DirectionalLight(0xffffff, 1.35)
    key.position.set(-260, 210, 520)
    scene.add(key)
    const rim = new THREE.DirectionalLight(0xc7ddea, 0.82)
    rim.position.set(340, -100, 260)
    scene.add(rim)

    let width = 1
    let height = 1
    let disposed = false
    let raf = 0
    let last = performance.now()
    let lastPaintAt = 0
    let telemetryAt = last
    let telemetryFrames = 0
    let readySent = false
    const actors: Actor[] = []

    const resize = () => {
      width = canvas.clientWidth || window.innerWidth
      height = canvas.clientHeight || window.innerHeight
      const dpr = visualPixelRatio(width, height)
      renderer.setPixelRatio(dpr)
      canvas.dataset.pixelRatio = dpr.toFixed(2)
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.position.set(0, 0, (height * 0.5) / Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5)))
      camera.lookAt(0, 0, 0)
      camera.updateProjectionMatrix()
      for (const actor of actors) scaleRoute(actor, width, height)
    }
    resize()
    window.addEventListener('resize', resize)

    const advanceActor = (actor: Actor, wall: number, thinking: boolean) => {
      const targetScale = (thinking ? THINKING_TIME_SCALE : CRUISE_TIME_SCALE) * actor.config.timeScale
      actor.timeScale = damp(actor.timeScale, targetScale, 2.8, wall)
      let remaining = wall * actor.timeScale
      let guard = 0
      while (remaining > 1e-6 && guard++ < 64) {
        const dt = Math.min(1 / 480, remaining)
        stepActor(actor, dt, thinking)
        remaining -= dt
      }
      actor.flight.poseWings(actor.phase, !actor.powered)
    }

    const frame = (now: number) => {
      raf = 0
      if (disposed) return
      const fps = now < boostUntilRef.current ? VISUAL_FPS.active : targetFpsRef.current
      if (!reduced.matches && !visualFrameDue(now, lastPaintAt, fps)) {
        raf = requestAnimationFrame(frame)
        return
      }
      const wall = Math.min(0.05, Math.max(0.0005, (now - last) / 1000))
      last = now
      lastPaintAt = now
      telemetryFrames += 1
      if (now - telemetryAt >= 1000) {
        canvas.dataset.renderFps = (telemetryFrames * 1000 / (now - telemetryAt)).toFixed(1)
        canvas.dataset.frameBudget = String(fps)
        telemetryFrames = 0
        telemetryAt = now
      }
      const anyOpen = rootElement.classList.contains('pop-open')
      const labels: Array<{ element: HTMLElement; x: number; y: number }> = []

      for (const actor of actors) {
        const wrap = actor.node?.closest<HTMLElement>('.planet-wrap')
        const thinking = wrap?.classList.contains('busy') ?? false
        const open = wrap?.classList.contains('open') ?? false
        const hovered = actor.node?.closest('.planet-hit')?.matches(':hover') ?? false
        advanceActor(actor, wall, thinking)

        actor.body.position.set(
          actor.position.x * PIXELS_PER_METRE,
          actor.position.y * PIXELS_PER_METRE,
          actor.position.z * PIXELS_PER_METRE,
        )
        actor.body.quaternion.copy(actor.attitude)
        const targetBodyScale = (thinking ? 1.1 : open ? 1.12 : hovered ? 1.05 : 1)
        const currentBodyScale = actor.body.scale.x || 1
        actor.body.scale.setScalar(damp(currentBodyScale, targetBodyScale, 4.6, wall))

        const targetOpacity = anyOpen && !open
          ? actor.config.opacity * 0.18
          : open
            ? Math.min(0.5, actor.config.opacity * 1.25)
            : hovered
              ? Math.min(0.46, actor.config.opacity * 1.12)
              : actor.config.opacity
        for (const material of actor.materials) {
          material.opacity = damp(material.opacity, targetOpacity, 5, wall)
          material.emissiveIntensity = damp(material.emissiveIntensity, thinking ? 0.11 : 0.04, 4, wall)
        }

        actor.projected.copy(actor.body.position).project(camera)
        const screenX = (actor.projected.x * 0.5 + 0.5) * width
        const screenY = (-actor.projected.y * 0.5 + 0.5) * height
        if (wrap) {
          wrap.style.setProperty('--flight-x', `${(screenX - actor.config.x * width).toFixed(2)}px`)
          wrap.style.setProperty('--flight-y', `${(screenY - actor.config.y * height).toFixed(2)}px`)
          // The thought window is 286px wide. Turn it inward before the actor
          // actually reaches the crew rail, rather than waiting until the label
          // itself has already crossed the rail.
          wrap.classList.toggle('flight-right', screenX > width * 0.55)
          wrap.dataset.flightMode = actor.powered ? 'powered' : 'glide'
          wrap.dataset.flightX = screenX.toFixed(1)
          wrap.dataset.flightY = screenY.toFixed(1)
          wrap.dataset.simTime = actor.simTime.toFixed(2)
        }
        const label = actor.node?.querySelector<HTMLElement>('.mist-person-name')
        if (label) {
          const placeLeft = screenX > width * 0.67
          label.classList.toggle('left', placeLeft)
          label.classList.toggle('right', !placeLeft)
          labels.push({
            element: label,
            x: screenX + (placeLeft ? -1 : 1) * (actor.config.size * 0.53 + 66),
            y: screenY,
          })
        }
      }

      // The animals may cross; their names must not. Move only the small text
      // label by a few pixels and leave the physical flight untouched.
      labels.sort((a, b) => a.y - b.y)
      const placed: Array<{ x: number; y: number }> = []
      const nudges = [0, -25, 25, -48, 48]
      for (const label of labels) {
        const nudge = nudges.find((candidate) => !placed.some((other) => (
          Math.abs(other.x - label.x) < 138 && Math.abs(other.y - (label.y + candidate)) < 31
        ))) ?? 0
        label.element.style.setProperty('--label-nudge', `${nudge}px`)
        placed.push({ x: label.x, y: label.y + nudge })
      }

      renderer.render(scene, camera)
      if (!readySent && actors.length > 0) {
        readySent = true
        onReady?.('mist')
      }
      raf = requestAnimationFrame(frame)
    }

    const start = () => {
      if (!raf && !disposed && !document.hidden && !reduced.matches) {
        last = performance.now()
        lastPaintAt = 0
        raf = requestAnimationFrame(frame)
      }
    }
    const stop = () => {
      if (raf) cancelAnimationFrame(raf)
      raf = 0
    }
    const visibility = () => (document.hidden || reduced.matches ? stop() : start())
    const boost = () => {
      boostUntilRef.current = performance.now() + 900
    }
    rootElement.addEventListener('pointermove', boost)
    rootElement.addEventListener('pointerdown', boost)

    new GLTFLoader().load(
      MODEL_URL,
      (gltf) => {
        if (disposed) return
        const clip = gltf.animations[0]
        if (!clip) {
          canvas.dataset.error = '蝴蝶骨骼动画缺失'
          return
        }
        const nodes = Array.from(rootElement.querySelectorAll<HTMLElement>('.mist-person-flight'))
        configs.slice(0, nodes.length).forEach((config, index) => {
          const actor = createActor(gltf.scene, clip, config, nodes[index] ?? null, scene, width, height)
          if (actor) actors.push(actor)
        })
        canvas.dataset.ready = actors.length === configs.length ? 'true' : 'partial'
        canvas.dataset.actorCount = String(actors.length)
        document.addEventListener('visibilitychange', visibility)
        reduced.addEventListener('change', visibility)
        if (reduced.matches) {
          frame(performance.now())
          stop()
        } else {
          visibility()
        }
      },
      undefined,
      (error) => {
        canvas.dataset.error = error instanceof Error ? error.message : '蝴蝶模型加载失败'
      },
    )

    return () => {
      disposed = true
      stop()
      window.removeEventListener('resize', resize)
      rootElement.removeEventListener('pointermove', boost)
      rootElement.removeEventListener('pointerdown', boost)
      document.removeEventListener('visibilitychange', visibility)
      reduced.removeEventListener('change', visibility)
      for (const actor of actors) {
        actor.flight.dispose()
        for (const material of actor.materials) material.dispose()
      }
      disposeScene(scene)
      renderer.dispose()
      renderer.forceContextLoss()
    }
  }, [configs, onReady, rootRef])

  return <canvas className="mist-butterfly-field" ref={canvasRef} aria-hidden />
}
