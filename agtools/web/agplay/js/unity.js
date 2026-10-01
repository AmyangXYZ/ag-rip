// Unity's conventions, as the D3D11 renderer the game shaders were compiled for sees them.
// WebGPU shares D3D's clip space (y up, depth 0..1) and texture addressing (row 0 first),
// so Unity's own GPU matrices drop in unchanged: reversed Z, and a Y flip whenever it
// renders into a texture - which every pass here does.

// ---- 4x4 matrices, column-major Float32Array (as recorded: m[col * 4 + row])
export function mat(a) { return a instanceof Float32Array ? a : new Float32Array(a) }
export const IDENTITY = mat([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])

export function mul(a, b) {
  const o = new Float32Array(16)
  for (let c = 0; c < 4; c++)
    for (let r = 0; r < 4; r++) {
      let s = 0
      for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]
      o[c * 4 + r] = s
    }
  return o
}

export function invert(m) {
  const a = m, o = new Float32Array(16)
  const a00 = a[0], a01 = a[1], a02 = a[2], a03 = a[3], a10 = a[4], a11 = a[5], a12 = a[6], a13 = a[7]
  const a20 = a[8], a21 = a[9], a22 = a[10], a23 = a[11], a30 = a[12], a31 = a[13], a32 = a[14], a33 = a[15]
  const b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10, b02 = a00 * a13 - a03 * a10
  const b03 = a01 * a12 - a02 * a11, b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12
  const b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30, b08 = a20 * a33 - a23 * a30
  const b09 = a21 * a32 - a22 * a31, b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32
  let det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06
  if (!det) return new Float32Array(IDENTITY)
  det = 1 / det
  o[0] = (a11 * b11 - a12 * b10 + a13 * b09) * det; o[1] = (a02 * b10 - a01 * b11 - a03 * b09) * det
  o[2] = (a31 * b05 - a32 * b04 + a33 * b03) * det; o[3] = (a22 * b04 - a21 * b05 - a23 * b03) * det
  o[4] = (a12 * b08 - a10 * b11 - a13 * b07) * det; o[5] = (a00 * b11 - a02 * b08 + a03 * b07) * det
  o[6] = (a32 * b02 - a30 * b05 - a33 * b01) * det; o[7] = (a20 * b05 - a22 * b02 + a23 * b01) * det
  o[8] = (a10 * b10 - a11 * b08 + a13 * b06) * det; o[9] = (a01 * b08 - a00 * b10 - a03 * b06) * det
  o[10] = (a30 * b04 - a31 * b02 + a33 * b00) * det; o[11] = (a21 * b02 - a20 * b04 - a23 * b00) * det
  o[12] = (a11 * b07 - a10 * b09 - a12 * b06) * det; o[13] = (a00 * b09 - a01 * b07 + a02 * b06) * det
  o[14] = (a31 * b01 - a30 * b03 - a32 * b00) * det; o[15] = (a20 * b03 - a21 * b01 + a22 * b00) * det
  return o
}

export function det3(m) {
  return m[0] * (m[5] * m[10] - m[9] * m[6]) - m[4] * (m[1] * m[10] - m[9] * m[2]) + m[8] * (m[1] * m[6] - m[5] * m[2])
}

export function transformPoint(m, p) {
  const x = p[0], y = p[1], z = p[2]
  return [m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]]
}

// GL.GetGPUProjectionMatrix on D3D: Y flipped when rendering into a texture, and the
// GL depth range [-1, 1] (near -1) remapped to reversed Z [1, 0]:
//   row1 *= -1 (flip),  row2 = 0.5 * (row3 - row2)
export function gpuProjection(p, intoTexture = true) {
  const o = new Float32Array(p)
  for (let c = 0; c < 4; c++) {
    const r2 = p[c * 4 + 2], r3 = p[c * 4 + 3]
    o[c * 4 + 2] = 0.5 * (r3 - r2)
    if (intoTexture) o[c * 4 + 1] = -p[c * 4 + 1]
  }
  return o
}

// ---- render state enums (UnityEngine.Rendering.*), by name or by number
const BLEND = ["zero", "one", "dst", "src", "one-minus-dst", "src-alpha", "one-minus-src", "dst-alpha",
  "one-minus-dst-alpha", "src-alpha-saturated", "one-minus-src-alpha"]
const BLEND_NAMES = {
  zero: "zero", one: "one", dstcolor: "dst", srccolor: "src", oneminusdstcolor: "one-minus-dst",
  srcalpha: "src-alpha", oneminussrccolor: "one-minus-src", dstalpha: "dst-alpha", oneminusdstalpha: "one-minus-dst-alpha",
  srcalphasaturate: "src-alpha-saturated", oneminussrcalpha: "one-minus-src-alpha",
}
export function blendFactor(v) {
  if (typeof v === "number" || /^-?\d+(\.\d+)?$/.test(v)) return BLEND[Math.round(+v)] ?? "one"
  return BLEND_NAMES[String(v).toLowerCase()] ?? "one"
}
const BLEND_OP = ["add", "subtract", "reverse-subtract", "min", "max"]
export function blendOp(v) {
  if (v == null) return "add"
  if (typeof v === "number" || /^\d+$/.test(v)) return BLEND_OP[+v] ?? "add"
  const s = String(v).toLowerCase()
  return s === "sub" ? "subtract" : s === "revsub" ? "reverse-subtract" : s === "min" ? "min" : s === "max" ? "max" : "add"
}
// CompareFunction: 0 Disabled 1 Never 2 Less 3 Equal 4 LessEqual 5 Greater 6 NotEqual 7 GreaterEqual 8 Always
const CMP = [null, "never", "less", "equal", "less-equal", "greater", "not-equal", "greater-equal", "always"]
const CMP_NAMES = { never: 1, less: 2, equal: 3, lequal: 4, lessequal: 4, greater: 5, notequal: 6, gequal: 7, greaterequal: 7, always: 8, disabled: 8 }
export function compare(v, fallback = "always") {
  let n = typeof v === "number" || /^\d+$/.test(v ?? "") ? +v : CMP_NAMES[String(v ?? "").toLowerCase()]
  if (n == null) return fallback
  return CMP[n] ?? "always"
}
// depth tests under reversed Z: Unity flips them itself on D3D
const REVERSE = { less: "greater", "less-equal": "greater-equal", greater: "less", "greater-equal": "less-equal" }
export function depthCompare(v) {
  const c = compare(v ?? "LEqual", "less-equal")
  return REVERSE[c] ?? c
}
// StencilOp: 0 Keep 1 Zero 2 Replace 3 IncrSat 4 DecrSat 5 Invert 6 IncrWrap 7 DecrWrap
const SOP = ["keep", "zero", "replace", "increment-clamp", "decrement-clamp", "invert", "increment-wrap", "decrement-wrap"]
const SOP_NAMES = { keep: 0, zero: 1, replace: 2, incrsat: 3, decrsat: 4, invert: 5, incrwrap: 6, decrwrap: 7 }
export function stencilOp(v) {
  if (v == null) return "keep"
  const n = /^\d+$/.test(String(v)) ? +v : SOP_NAMES[String(v).toLowerCase()]
  return SOP[n ?? 0] ?? "keep"
}
// CullMode: 0 Off 1 Front 2 Back
export function cull(v) {
  if (v == null) return "back"
  const s = String(v).toLowerCase()
  if (s === "0" || s === "off") return "none"
  if (s === "1" || s === "front") return "front"
  return "back"
}
export function zwrite(v) {
  if (v == null) return true
  const s = String(v).toLowerCase()
  return !(s === "0" || s === "off" || s === "false")
}
export function colorMask(v) {
  if (v == null) return 0xf
  const s = String(v).toUpperCase()
  if (/^\d+$/.test(s)) {
    // Unity ColorWriteMask: A 1, B 2, G 4, R 8 -> WebGPU R 1, G 2, B 4, A 8
    const n = +s
    return (n & 8 ? 1 : 0) | (n & 4 ? 2 : 0) | (n & 2 ? 4 : 0) | (n & 1 ? 8 : 0)
  }
  if (s === "0") return 0
  return (s.includes("R") ? 1 : 0) | (s.includes("G") ? 2 : 0) | (s.includes("B") ? 4 : 0) | (s.includes("A") ? 8 : 0)
}

// half-float decode, for RGBA16F texture data kept as Uint16
export function halfToFloat(h) {
  const s = (h & 0x8000) >> 15, e = (h & 0x7c00) >> 10, f = h & 0x03ff
  if (e === 0) return (s ? -1 : 1) * Math.pow(2, -14) * (f / 1024)
  if (e === 31) return f ? NaN : (s ? -Infinity : Infinity)
  return (s ? -1 : 1) * Math.pow(2, e - 15) * (1 + f / 1024)
}
