import coreFragment from '../vendor/singularity-black-hole/singularity-core.glsl?raw'

export type BlackHoleFlowRenderer = {
  canvas: HTMLCanvasElement
  isReady: () => boolean
  render: (time: number, energy: number) => void
  destroy: () => void
}

const WIDTH = 640
const HEIGHT = 330
const VERTEX = `#version 300 es
in vec2 position;
void main() { gl_Position = vec4(position, 0.0, 1.0); }
`
const FRAGMENT = `#version 300 es
${coreFragment}
`

type Uniforms = Record<string, WebGLUniformLocation | null>

function hash(x: number, y: number, seed: number) {
  let value = Math.imul(x + seed * 1013, 374761393) + Math.imul(y - seed * 733, 668265263)
  value = Math.imul(value ^ (value >>> 13), 1274126177)
  return ((value ^ (value >>> 16)) >>> 0) / 4294967295
}

function smooth(value: number) {
  return value * value * (3 - 2 * value)
}

function octaveNoise(x: number, y: number, cells: number, seed: number) {
  const px = (x / 256) * cells
  const py = (y / 256) * cells
  const x0 = Math.floor(px)
  const y0 = Math.floor(py)
  const tx = smooth(px - x0)
  const ty = smooth(py - y0)
  const wrap = (value: number) => ((value % cells) + cells) % cells
  const a = hash(wrap(x0), wrap(y0), seed)
  const b = hash(wrap(x0 + 1), wrap(y0), seed)
  const c = hash(wrap(x0), wrap(y0 + 1), seed)
  const d = hash(wrap(x0 + 1), wrap(y0 + 1), seed)
  const top = a + (b - a) * tx
  const bottom = c + (d - c) * tx
  return top + (bottom - top) * ty
}

function makeNoiseTexture(gl: WebGL2RenderingContext) {
  const size = 256
  const data = new Uint8Array(size * size * 4)
  const cells = [4, 8, 16, 32, 64]
  const weights = [0.36, 0.27, 0.19, 0.12, 0.06]
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const offset = (y * size + x) * 4
      for (let channel = 0; channel < 3; channel += 1) {
        let value = 0
        for (let octave = 0; octave < cells.length; octave += 1) {
          value += octaveNoise(x, y, cells[octave], 19 + channel * 37 + octave * 11) * weights[octave]
        }
        data[offset + channel] = Math.round(Math.max(0, Math.min(1, value)) * 255)
      }
      data[offset + 3] = 255
    }
  }

  const texture = gl.createTexture()
  if (!texture) return null
  gl.bindTexture(gl.TEXTURE_2D, texture)
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, size, size, 0, gl.RGBA, gl.UNSIGNED_BYTE, data)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
  gl.generateMipmap(gl.TEXTURE_2D)
  return texture
}

function compile(gl: WebGL2RenderingContext, type: number, source: string) {
  const shader = gl.createShader(type)
  if (!shader) return null
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    console.error('Black-hole shader compile failed:', gl.getShaderInfoLog(shader))
    gl.deleteShader(shader)
    return null
  }
  return shader
}

function link(gl: WebGL2RenderingContext) {
  const vertexShader = compile(gl, gl.VERTEX_SHADER, VERTEX)
  const fragmentShader = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT)
  if (!vertexShader || !fragmentShader) {
    if (vertexShader) gl.deleteShader(vertexShader)
    if (fragmentShader) gl.deleteShader(fragmentShader)
    return null
  }
  const program = gl.createProgram()
  if (!program) return null
  gl.attachShader(program, vertexShader)
  gl.attachShader(program, fragmentShader)
  gl.linkProgram(program)
  gl.deleteShader(vertexShader)
  gl.deleteShader(fragmentShader)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.error('Black-hole shader link failed:', gl.getProgramInfoLog(program))
    gl.deleteProgram(program)
    return null
  }
  return program
}

export function createBlackHoleFlowRenderer(): BlackHoleFlowRenderer | null {
  const canvas = document.createElement('canvas')
  canvas.width = WIDTH
  canvas.height = HEIGHT
  const gl = canvas.getContext('webgl2', {
    alpha: true,
    antialias: false,
    depth: false,
    stencil: false,
    premultipliedAlpha: false,
    preserveDrawingBuffer: false,
    powerPreference: 'default',
  })
  if (!gl) return null

  const program = link(gl)
  const vertexBuffer = gl.createBuffer()
  const noiseTexture = makeNoiseTexture(gl)
  if (!program || !vertexBuffer || !noiseTexture) {
    if (program) gl.deleteProgram(program)
    if (vertexBuffer) gl.deleteBuffer(vertexBuffer)
    if (noiseTexture) gl.deleteTexture(noiseTexture)
    gl.getExtension('WEBGL_lose_context')?.loseContext()
    return null
  }

  gl.bindBuffer(gl.ARRAY_BUFFER, vertexBuffer)
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
    gl.STATIC_DRAW,
  )

  const uniforms: Uniforms = Object.fromEntries(
    ['u_resolution', 'u_time', 'u_energy', 'u_noise'].map((name) => [name, gl.getUniformLocation(program, name)]),
  )
  const position = gl.getAttribLocation(program, 'position')
  let destroyed = false

  gl.disable(gl.DEPTH_TEST)
  gl.disable(gl.BLEND)

  return {
    canvas,
    isReady: () => !destroyed,
    render(time, energy) {
      if (destroyed) return
      gl.viewport(0, 0, WIDTH, HEIGHT)
      gl.clearColor(0, 0, 0, 0)
      gl.clear(gl.COLOR_BUFFER_BIT)
      gl.useProgram(program)
      gl.uniform2f(uniforms.u_resolution, WIDTH, HEIGHT)
      gl.uniform1f(uniforms.u_time, time)
      gl.uniform1f(uniforms.u_energy, energy)
      gl.activeTexture(gl.TEXTURE0)
      gl.bindTexture(gl.TEXTURE_2D, noiseTexture)
      gl.uniform1i(uniforms.u_noise, 0)
      gl.bindBuffer(gl.ARRAY_BUFFER, vertexBuffer)
      gl.enableVertexAttribArray(position)
      gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0)
      gl.drawArrays(gl.TRIANGLES, 0, 6)
      gl.disableVertexAttribArray(position)
    },
    destroy() {
      if (destroyed) return
      destroyed = true
      gl.deleteBuffer(vertexBuffer)
      gl.deleteTexture(noiseTexture)
      gl.deleteProgram(program)
      gl.getExtension('WEBGL_lose_context')?.loseContext()
      canvas.width = 1
      canvas.height = 1
    },
  }
}
