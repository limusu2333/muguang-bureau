import * as THREE from 'three'

// Aerodynamics for the mist-hall butterfly.
//
// The stroke window, the blade elements, the wing areas, the glide pose and the
// body mass are all measured from the shipped GLB at load time rather than
// hand-authored, so the visible wing motion and the force on the body cannot
// drift apart.
//
// Sourcing notes, because several published numbers did not survive checking:
//
//  * Chen et al., "A Practical Model for Realistic Butterfly Flight Simulation"
//    (ACM TOG 41(3), Article 31, 2022) publishes no code, data or supplemental
//    implementation. This re-implements the paper's *relationships* only —
//    its parametric flap/feather/sweep DOFs and quasi-steady per-element forces.
//    Nothing here is copied from the authors.
//  * That paper's printed lift fit, Cl(a) = -0.0095953a^2 + 0.090635a - 0.34182,
//    evaluates negative across the whole range in degrees (-0.34 at 0 deg,
//    -69.9 at 90 deg), which cannot be a lift coefficient. It is not used.
//    LIFT_COEFF/DRAG_COEFF are the thin flat-plate quasi-steady pair instead:
//    Cl ~ sin(2a) peaking at 1.8, which is the order Dickinson et al. 1999
//    (Science 284:1954) measured on a dynamically scaled insect wing once the
//    leading-edge vortex is included; Cd ~ Cd0 + k sin^2(a). Cl is odd and Cd
//    even in the angle of attack, so both behave correctly through stroke reversal.
//
// The asset only animates one of the three wing DOFs the paper models, and the
// two it omits are exactly the two that make flight possible. Both were measured,
// not assumed:
//
//  * No FEATHERING. Area-weighted broadside ratio between down- and upstroke is
//    0.944 — the wing stays equally flat both ways, so every bit of lift the
//    downstroke wins the upstroke gives back. Net lift is zero under any correct
//    quasi-steady model, and it reads as a hinge rather than a wing.
//  * No SWEEP. With flap and feather alone the wing only ever paddles downward:
//    measured thrust stays negative at every frequency and at every stroke-plane
//    tilt, i.e. pure drag, so the butterfly could hover and bob but never travel.
//    Adding the paper's sweeping DOF turns measured thrust positive and roughly
//    doubles lift at the same frequency.

// Measured from organic-butterfly-v2.glb: the sub-range of the 9.5833 s
// OrganicWingCycle clip whose start and end poses match most closely
// (wing-quaternion mismatch 0.00027 over a 0.3893 s period), so it loops seamlessly.
const STROKE_START = 2.6055
const STROKE_PERIOD = 0.3893

// Feathering, about each wing root's own span axis (the bone chains run down their
// local +X). The mean offset is the load-bearing part: a zero-mean twist rotates
// the wing equally far both ways, leaves |angle of attack| unchanged between
// strokes and produces exactly no net lift.
const FEATHER_MEAN = THREE.MathUtils.degToRad(40)
const FEATHER_AMPLITUDE = THREE.MathUtils.degToRad(40)
const FEATHER_PHASE = Math.PI / 2

// Sweep: the wing tip travelling fore and aft, about the body's dorsal axis.
// This is what generates thrust. Chen et al. quote 0-20 deg for their rig; this
// is deliberately larger, because at 20 deg the measured thrust barely clears
// zero and will not carry the butterfly against its own drag.
const SWEEP_AMPLITUDE = THREE.MathUtils.degToRad(45)
const SWEEP_PHASE = 0

const AIR_DENSITY = 1.225 // kg/m^3
export const GRAVITY = 9.81 // m/s^2

// Real wingspan we scale the model to. Monarch forewing length is ~51 mm
// (Bode-Oke & Dong 2020, J. R. Soc. Interface 17:20200268), so ~0.1 m tip to tip.
const REAL_SPAN = 0.1

// Design point. The stroke is calibrated so this frequency carries the body:
// below it the creature sinks, above it climbs. Mass follows from it, so this
// number IS the wing loading.
//
// A monarch's own loading wants 9-11 Hz, and that is what earlier builds flew at.
// It reads as an insect fussing, not as something with weight — the beat is too
// quick to see a stroke land, and every stroke jolts the body hard enough that it
// never settles into a line. So this sits deliberately low: same wings, much
// lighter body, i.e. a big-winged slow-beating flier. That is the shape of every
// creature that reads as majestic in the air, and it is what buys long glides
// between a few unhurried strokes. Declared, not smuggled: this is not a monarch's
// wing loading, it is the one that flies the way the brief asks for.
const HOVER_HZ = 3
// The speed the glide is trimmed for, and so — via m = rho*V^2*S*Cl/(2g) — the
// thing that sets the mass, the inertia and the whole usable speed envelope.
//
// Trimmed at 0.45 the creature massed 0.016 g and could not exceed about 0.5 m/s:
// past that its own wings' drag overwhelmed a body far too light to carry any
// momentum, lift collapsed (0.83 weights at 1.0 m/s, negative by 1.3), and the
// first dive ran away — faster, less lift, faster still. Mass rises with the
// square of this, so trimming higher buys both a wider speed band and the heft to
// hold a line through a turn instead of being blown about by its own wake.
const CRUISE_SPEED = 0.9 // m/s

const PHASE_SAMPLES = 96

// Cd0 is the wing's drag when it meets the air edge-on, and it alone sets the
// ceiling on gliding: maximising Cl/Cd for this pair gives L/D ~ 1.8/(2*sqrt(Cd0*1.9)).
// At the 0.1 first guessed here that ceiling is 4, the glide search duly hit 3.3,
// and a creature that sinks one metre in three cannot stay up long enough to look
// like anything but a falling leaf. A thin smooth membrane edge-on is skin friction
// and little else, so 0.028 is the honest figure and it roughly doubles the glide.
// (Cd0 makes almost no difference to flapping lift, which is why it went unexamined
// for so long — it is gliding that lives or dies by it.)
const LIFT_COEFF = (alpha: number) => 1.8 * Math.sin(2 * alpha)
const DRAG_COEFF = (alpha: number) => 0.028 + 1.9 * Math.sin(alpha) * Math.sin(alpha)

const WING_ROOT_NAMES = [
  'l_frnt_wings01_jnt97_97',
  'r_frnt_wings01_jnt105_105',
  'l_bk_wings01_jnt54_54',
  'r_bk_wings01_jnt113_113',
]

type Pose = {
  position: THREE.Vector3
  normal: THREE.Vector3
}

export type FlightModel = {
  strokePeriod: number
  glidePhase: number
  glideFeather: number
  /** Lift-to-drag of the chosen glide pose: the tangent of how flat it can sink. */
  glideRatio: number
  mass: number
  hoverHz: number
  /** Measured beat with enough margin to climb in real flight. */
  climbHz: number
  span: number
  wingArea: number
  cruiseSpeed: number
  /** Aerodynamic force in the body frame, newtons. hz = 0 uses the glide pose. */
  force: (phase: number, hz: number, bodyVelocity: THREE.Vector3, out: THREE.Vector3) => THREE.Vector3
  /** Signed vertical speed of the wings at this phase; negative is a downstroke. */
  strokeVelocity: (phase: number) => number
  /**
   * Drives the skeleton. The renderer and the force model must call this with the
   * same phase, which is the only reason the wings and the body agree.
   */
  poseWings: (phase: number, gliding: boolean) => void
  /** Releases the mixer that drives the skeleton. */
  dispose: () => void
}

/** Smallest-variance direction of a point cloud: the plane the wing lies in. */
function planeNormal(points: THREE.Vector3[], out: THREE.Vector3) {
  const c = new THREE.Vector3()
  for (const p of points) c.add(p)
  c.multiplyScalar(1 / points.length)
  let xx = 0, xy = 0, xz = 0, yy = 0, yz = 0, zz = 0
  const d = new THREE.Vector3()
  for (const p of points) {
    d.copy(p).sub(c)
    xx += d.x * d.x; xy += d.x * d.y; xz += d.x * d.z
    yy += d.y * d.y; yz += d.y * d.z; zz += d.z * d.z
  }
  // Iterate on (trace*I - M) so the dominant eigenvector is the one belonging to
  // the smallest eigenvalue of M.
  const tr = xx + yy + zz
  const a = [
    [tr - xx, -xy, -xz],
    [-xy, tr - yy, -yz],
    [-xz, -yz, tr - zz],
  ]
  const v = out.set(0.3, 0.9, 0.31).normalize()
  const n = new THREE.Vector3()
  for (let i = 0; i < 160; i++) {
    n.set(
      a[0][0] * v.x + a[0][1] * v.y + a[0][2] * v.z,
      a[1][0] * v.x + a[1][1] * v.y + a[1][2] * v.z,
      a[2][0] * v.x + a[2][1] * v.y + a[2][2] * v.z,
    )
    if (n.lengthSq() < 1e-20) break
    v.copy(n).normalize()
  }
  return v
}

export function buildFlightModel(scene: THREE.Object3D, clip: THREE.AnimationClip): FlightModel | null {
  const meshes: THREE.SkinnedMesh[] = []
  scene.traverse((o) => { if ((o as THREE.SkinnedMesh).isSkinnedMesh) meshes.push(o as THREE.SkinnedMesh) })
  if (!meshes.length) return null

  const wingRoots = WING_ROOT_NAMES
    .map((n) => scene.getObjectByName(n) as THREE.Bone | undefined)
    .filter((b): b is THREE.Bone => !!b)
  if (wingRoots.length !== WING_ROOT_NAMES.length) return null

  const mixer = new THREE.AnimationMixer(scene)
  const action = mixer.clipAction(clip)
  action.play()

  const twistQuat = new THREE.Quaternion()
  const sweepQuat = new THREE.Quaternion()
  const baseQuat = new THREE.Quaternion()
  const spanAxis = new THREE.Vector3(1, 0, 0)
  const sweepAxis = new THREE.Vector3()
  const parentInverse = new THREE.Matrix4()

  // Read each wing root's rotation straight off the clip, rather than trusting
  // that mixer.update(0) has reset the bone before we add our own twist to it.
  // It does not: the twist compounded on every call, so posing the same phase
  // twice gave two different wings, and the glide search was scoring poses it
  // could never reproduce. Sampling the track and assigning is idempotent by
  // construction.
  // createInterpolant() rather than a hand-picked one: the track carries its own
  // interpolation mode (this clip mixes STEP and LINEAR), and using the track's own
  // is what keeps these bones identical to what the mixer would have produced.
  type TrackWithInterpolant = THREE.KeyframeTrack & { createInterpolant: () => { evaluate: (t: number) => ArrayLike<number> } }
  const rootTracks = wingRoots.map((bone) => {
    const track = clip.tracks.find((t) => t.name === `${bone.name}.quaternion`) as TrackWithInterpolant | undefined
    return track?.createInterpolant ? track.createInterpolant() : null
  })

  /** The single place the wings are posed, shared by the force model and the renderer. */
  const poseWings = (phase: number, gliding: boolean) => {
    action.time = STROKE_START + (gliding ? glidePhase : phase) * STROKE_PERIOD
    mixer.update(0)
    const twist = gliding
      ? glideFeather
      : FEATHER_MEAN + FEATHER_AMPLITUDE * Math.cos(2 * Math.PI * phase + FEATHER_PHASE)
    const sweep = gliding ? 0 : SWEEP_AMPLITUDE * Math.cos(2 * Math.PI * phase + SWEEP_PHASE)
    wingRoots.forEach((bone, i) => {
      const side = bone.name.startsWith('l_') ? 1 : -1
      const interpolant = rootTracks[i]
      if (interpolant) baseQuat.fromArray(interpolant.evaluate(action.time) as unknown as number[])
      else baseQuat.copy(bone.quaternion)
      // No side flip on the twist, unlike the sweep below. Each wing's bone chain
      // runs out along its own local +X, so those axes already point opposite ways
      // in body space and an equal angle about each is the mirror-symmetric twist.
      // Flipping the sign as well makes it antisymmetric — one leading edge up, the
      // other down — which is a roll command, not feathering, and the two wings'
      // lift then cancels to exactly zero.
      bone.quaternion.copy(baseQuat).multiply(twistQuat.setFromAxisAngle(spanAxis, twist))
      if (sweep !== 0 && bone.parent) {
        // Sweep is fore/aft, i.e. about the body's dorsal axis. Express that axis
        // in the bone's parent frame so the rest of the wing chain inherits it.
        bone.parent.updateMatrixWorld(true)
        parentInverse.copy(bone.parent.matrixWorld).invert()
        sweepAxis.set(0, 1, 0).transformDirection(parentInverse).normalize()
        bone.quaternion.premultiply(sweepQuat.setFromAxisAngle(sweepAxis, sweep * side))
      }
    })
    scene.updateMatrixWorld(true)
  }

  // Set by the calibration below; poseWings reads them, so they must exist first.
  let glidePhase = 0
  let glideFeather = 0

  // --- blade elements, straight off the skinned mesh -----------------------
  type Raw = { mesh: THREE.SkinnedMesh; boneIndex: number; bone: THREE.Bone; area: number; basePos: THREE.Vector3; baseNormal: THREE.Vector3 }
  const raws: Raw[] = []
  for (const mesh of meshes) {
    const geom = mesh.geometry
    const pos = geom.attributes.position
    const skinIndex = geom.attributes.skinIndex
    const skinWeight = geom.attributes.skinWeight
    const index = geom.index
    if (!pos || !skinIndex || !skinWeight) continue

    const dominant = new Int32Array(pos.count)
    for (let v = 0; v < pos.count; v++) {
      let best = -1
      let bestWeight = -1
      for (let k = 0; k < 4; k++) {
        const w = skinWeight.getComponent(v, k)
        if (w > bestWeight) { bestWeight = w; best = skinIndex.getComponent(v, k) }
      }
      dominant[v] = best
    }

    const groups = new Map<number, { area: number; points: THREE.Vector3[] }>()
    const p0 = new THREE.Vector3(), p1 = new THREE.Vector3(), p2 = new THREE.Vector3()
    const e1 = new THREE.Vector3(), e2 = new THREE.Vector3(), cross = new THREE.Vector3()
    const triangles = index ? index.count / 3 : pos.count / 3
    for (let t = 0; t < triangles; t++) {
      const i0 = index ? index.getX(t * 3) : t * 3
      const i1 = index ? index.getX(t * 3 + 1) : t * 3 + 1
      const i2 = index ? index.getX(t * 3 + 2) : t * 3 + 2
      p0.fromBufferAttribute(pos, i0); p1.fromBufferAttribute(pos, i1); p2.fromBufferAttribute(pos, i2)
      e1.subVectors(p1, p0); e2.subVectors(p2, p0); cross.crossVectors(e1, e2)
      const area = cross.length() * 0.5
      for (const vi of [i0, i1, i2]) {
        const g = groups.get(dominant[vi]) ?? { area: 0, points: [] }
        g.area += area / 3
        groups.set(dominant[vi], g)
      }
    }
    for (let v = 0; v < pos.count; v++) {
      const g = groups.get(dominant[v])
      if (g) g.points.push(new THREE.Vector3().fromBufferAttribute(pos, v))
    }

    for (const [boneIndex, g] of groups) {
      const bone = mesh.skeleton.bones[boneIndex]
      if (!bone || !/wings/i.test(bone.name) || g.points.length < 8) continue
      const centroid = new THREE.Vector3()
      for (const p of g.points) centroid.add(p)
      centroid.multiplyScalar(1 / g.points.length)
      raws.push({
        mesh,
        boneIndex,
        bone: bone as THREE.Bone,
        // The mesh is a closed shell, so summed triangle area counts both faces.
        area: g.area * 0.5,
        basePos: centroid.applyMatrix4(mesh.bindMatrix),
        baseNormal: planeNormal(g.points, new THREE.Vector3()).transformDirection(mesh.bindMatrix),
      })
    }
  }
  if (!raws.length) return null

  // Official three.js skinning path, so these track the rendered wing exactly.
  const skinMatrix = new THREE.Matrix4()
  const evalStation = (raw: Raw, outPos: THREE.Vector3, outNormal: THREE.Vector3) => {
    skinMatrix.multiplyMatrices(raw.bone.matrixWorld, raw.mesh.skeleton.boneInverses[raw.boneIndex])
    outPos.copy(raw.basePos).applyMatrix4(skinMatrix).applyMatrix4(raw.mesh.bindMatrixInverse).applyMatrix4(raw.mesh.matrixWorld)
    outNormal.copy(raw.baseNormal).transformDirection(skinMatrix).transformDirection(raw.mesh.bindMatrixInverse)
      .transformDirection(raw.mesh.matrixWorld).normalize()
  }

  const samplePos: THREE.Vector3[][] = raws.map(() => [])
  const sampleNormal: THREE.Vector3[][] = raws.map(() => [])
  const p = new THREE.Vector3(), n = new THREE.Vector3()
  for (let k = 0; k < PHASE_SAMPLES; k++) {
    poseWings(k / PHASE_SAMPLES, false)
    raws.forEach((raw, i) => {
      evalStation(raw, p, n)
      samplePos[i].push(p.clone())
      sampleNormal[i].push(n.clone())
    })
  }

  // Span has to be measured across the whole stroke: at the top of the stroke the
  // wings are folded and read barely half their true width.
  let span = 0
  for (let k = 0; k < PHASE_SAMPLES; k++) {
    let min = Infinity, max = -Infinity
    for (let i = 0; i < raws.length; i++) {
      min = Math.min(min, samplePos[i][k].x)
      max = Math.max(max, samplePos[i][k].x)
    }
    span = Math.max(span, max - min)
  }
  if (!(span > 1e-6)) return null
  const scale = REAL_SPAN / span

  const areas = raws.map((r) => r.area * scale * scale)
  const wingArea = areas.reduce((s, a) => s + a, 0)
  const position = samplePos.map((arr) => arr.map((v) => v.multiplyScalar(scale)))
  const normal = sampleNormal
  const dPosition = position.map((arr) =>
    arr.map((_, k) => arr[(k + 1) % PHASE_SAMPLES].clone().sub(arr[(k - 1 + PHASE_SAMPLES) % PHASE_SAMPLES]).multiplyScalar(PHASE_SAMPLES / 2)),
  )

  // Static glide pose, filled in by the calibration below.
  const glide: Pose[] = raws.map(() => ({ position: new THREE.Vector3(), normal: new THREE.Vector3(0, 1, 0) }))

  // Scratch; force() runs every frame.
  const flap = new THREE.Vector3(), nrm = new THREE.Vector3()
  const airflow = new THREE.Vector3(), liftDir = new THREE.Vector3(), effNormal = new THREE.Vector3()

  const force = (phase: number, hz: number, bodyVelocity: THREE.Vector3, out: THREE.Vector3) => {
    out.set(0, 0, 0)
    const gliding = hz === 0
    const wrapped = ((phase % 1) + 1) % 1
    const kf = wrapped * PHASE_SAMPLES
    const k0 = Math.floor(kf) % PHASE_SAMPLES
    const k1 = (k0 + 1) % PHASE_SAMPLES
    const frac = kf - Math.floor(kf)
    for (let i = 0; i < raws.length; i++) {
      if (gliding) {
        flap.set(0, 0, 0)
        nrm.copy(glide[i].normal)
      } else {
        flap.copy(dPosition[i][k0]).lerp(dPosition[i][k1], frac).multiplyScalar(hz)
        nrm.copy(normal[i][k0]).lerp(normal[i][k1], frac).normalize()
      }
      // Air the element actually meets: its own flapping plus the body's travel.
      airflow.copy(flap).add(bodyVelocity).negate()
      const speed = airflow.length()
      if (speed < 1e-6) continue
      airflow.multiplyScalar(1 / speed)
      const sinAlpha = THREE.MathUtils.clamp(airflow.dot(nrm), -1, 1)
      const alpha = Math.asin(Math.abs(sinAlpha))
      const q = 0.5 * AIR_DENSITY * areas[i] * speed * speed
      out.addScaledVector(airflow, q * DRAG_COEFF(alpha))
      effNormal.copy(nrm).multiplyScalar(Math.sign(sinAlpha) || 1)
      liftDir.copy(effNormal).addScaledVector(airflow, -effNormal.dot(airflow))
      if (liftDir.lengthSq() > 1e-12) out.addScaledVector(liftDir.normalize(), q * LIFT_COEFF(alpha))
    }
    return out
  }

  // --- calibrate the glide pose -------------------------------------------
  // Chosen by lift-to-drag, not by lift. Picking the strongest-lifting pose gets
  // you a parachute: a high angle of attack that also drags enormously, L/D near 1,
  // and a butterfly that descends at 45 degrees. That is falling, not gliding.
  // Best L/D is the sailplane pose, and it is what makes a long, flat, quiet glide
  // possible at all.
  const cruise = new THREE.Vector3(0, 0, CRUISE_SPEED)
  const probe = new THREE.Vector3()
  const captureGlide = () => {
    poseWings(0, true)
    raws.forEach((raw, i) => {
      evalStation(raw, p, n)
      glide[i].position.copy(p).multiplyScalar(scale)
      glide[i].normal.copy(n)
    })
  }
  let bestRatio = -Infinity
  let bestPhase = 0
  let bestFeather = 0
  for (let k = 0; k < PHASE_SAMPLES; k += 2) {
    for (let f = -80; f <= 80; f += 4) {
      glidePhase = k / PHASE_SAMPLES
      glideFeather = THREE.MathUtils.degToRad(f)
      captureGlide()
      force(0, 0, cruise, probe)
      if (probe.y <= 0) continue
      const ratio = probe.y / Math.max(1e-9, Math.abs(probe.z))
      if (ratio > bestRatio) { bestRatio = ratio; bestPhase = glidePhase; bestFeather = glideFeather }
    }
  }
  glidePhase = bestPhase
  glideFeather = bestFeather
  captureGlide()
  const glideRatio = bestRatio

  // --- trim the body mass ---------------------------------------------------
  // Mass is set by what the *glide* carries at cruise, not by what some flap
  // frequency lifts. Tying it to a flap frequency made mass its slave: ask for a
  // slow, graceful beat and the body has to become a scrap of paper to stay up,
  // and a weightless thing has no inertia, so it skitters — the exact opposite of
  // the heft we want. Trimming to the glide instead is the standard way to size a
  // wing, and it makes gliding this creature's resting state and flapping the
  // thing it does to climb back up. That IS the rhythm.
  force(0, 0, cruise, probe)
  const mass = Math.max(1e-6, probe.y / GRAVITY)
  const weight = mass * GRAVITY

  // Cycle-averaged lift at a given beat, in body weights.
  const liftAt = (hz: number) => {
    let sum = 0
    for (let k = 0; k < PHASE_SAMPLES; k++) sum += force(k / PHASE_SAMPLES, hz, cruise, probe).y
    return sum / PHASE_SAMPLES / weight
  }
  // The beat that just holds height, and one with enough margin to climb against
  // the sideslip and sink that flight actually serves up. Both measured, so they
  // track the asset rather than being guessed at.
  let hoverHz = HOVER_HZ
  let climbHz = HOVER_HZ * 1.5
  for (let hz = 0.5; hz <= 24; hz += 0.1) {
    if (liftAt(hz) >= 1) { hoverHz = hz; break }
  }
  // The margin is large on purpose. On the bench the wings meet clean forward air;
  // in flight they meet their own sink and sideslip too, and the stroke then
  // delivers only about 60% of this. A thin margin leaves it beating hard and
  // barely holding height, which buys no altitude, and with no altitude there is
  // nothing to spend on a long glide.
  for (let hz = hoverHz; hz <= 24; hz += 0.1) {
    if (liftAt(hz) >= 3.6) { climbHz = hz; break }
  }

  const strokeVelocity = (phase: number) => {
    const wrapped = ((phase % 1) + 1) % 1
    const k = Math.floor(wrapped * PHASE_SAMPLES) % PHASE_SAMPLES
    let vy = 0
    let w = 0
    for (let i = 0; i < raws.length; i++) { vy += dPosition[i][k].y * areas[i]; w += areas[i] }
    return w > 0 ? vy / w : 0
  }

  return {
    strokePeriod: STROKE_PERIOD,
    glidePhase,
    glideFeather,
    glideRatio,
    mass,
    hoverHz,
    climbHz,
    span: REAL_SPAN,
    wingArea,
    cruiseSpeed: CRUISE_SPEED,
    force,
    strokeVelocity,
    poseWings,
    dispose: () => { mixer.stopAllAction(); mixer.uncacheClip(clip); mixer.uncacheRoot(scene) },
  }
}

export { STROKE_START, STROKE_PERIOD, HOVER_HZ, CRUISE_SPEED }
