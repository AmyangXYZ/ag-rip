// The recording AGDlcRecord wrote (Unity side) and the shaders dlc_web.py translated:
//   data/scene.json       meshes, textures, materials, renderers, global keywords/textures
//   data/variants.json    material -> passes [{pass, lightMode, variant, state}]
//   data/shaders/*.wgsl   one translated variant per (shader, pass, keywords)
//   data/<seq>.frames.jsonl + .bin   one line per frame; renderers only when they changed

export async function loadJSON(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${url}: ${r.status}`)
  return r.json()
}

export async function loadScene(base) {
  const [scene, variants] = await Promise.all([loadJSON(`${base}/scene.json`), loadJSON(`${base}/variants.json`)])
  const names = new Set()
  for (const [id, passes] of Object.entries(variants)) {
    if (id.startsWith("_")) continue          // _textureDefaults, _post
    for (const p of passes) names.add(p.variant)
  }
  for (const list of Object.values(variants._post || {})) for (const n of list) if (n) names.add(n)
  const shaders = {}
  await Promise.all([...names].map(async (n) => {
    const [vert, frag, info] = await Promise.all([
      fetch(`${base}/shaders/${n}.vert.wgsl`).then((r) => r.text()),
      fetch(`${base}/shaders/${n}.frag.wgsl`).then((r) => r.text()),
      loadJSON(`${base}/shaders/${n}.json`),
    ])
    shaders[n] = { name: n, vert, frag, info }
  }))
  return { base, scene, variants, shaders }
}

// A sequence's frames with random access: each renderer's record in effect at frame f is
// its latest change at or before f.
export class Sequence {
  static async load(base, name, onProgress) {
    const [text, bin] = await Promise.all([
      fetch(`${base}/${name}.frames.jsonl`).then((r) => r.text()),
      fetch(`${base}/${name}.frames.bin`).then((r) => r.arrayBuffer()),
    ])
    const lines = text.split("\n").filter((l) => l.length)
    const frames = new Array(lines.length)
    for (let i = 0; i < lines.length; i++) {
      frames[i] = JSON.parse(lines[i])
      if (onProgress && i % 100 === 0) onProgress(i / lines.length)
    }
    return new Sequence(name, frames, bin)
  }

  constructor(name, frames, bin) {
    this.name = name
    this.frames = frames
    this.bin = bin
    this.changes = new Map()        // rid -> [[frame, record], ...]
    frames.forEach((fr, i) => {
      for (const [rid, rec] of Object.entries(fr.r)) {
        let list = this.changes.get(rid)
        if (!list) this.changes.set(rid, (list = []))
        list.push([i, rec])
      }
    })
  }

  get length() { return this.frames.length }

  // renderer records in effect at frame f (per-frame data - bones, blend weights,
  // particles - only when recorded at exactly this frame)
  renderersAt(f) {
    const out = new Map()
    for (const [rid, list] of this.changes) {
      let lo = 0, hi = list.length - 1, at = -1
      while (lo <= hi) {
        const mid = (lo + hi) >> 1
        if (list[mid][0] <= f) { at = mid; lo = mid + 1 } else hi = mid - 1
      }
      if (at < 0) continue
      const [frame, rec] = list[at]
      out.set(rid, frame === f ? rec : { ...rec, bones: undefined, blend: undefined, particles: undefined, stale: frame !== f })
    }
    return out
  }

  bones(ref) {
    const [offset, count] = ref
    return new Float32Array(this.bin, offset, count * 16)
  }

  particles(ref) {
    if (!ref) return null
    const [offset, nv, ni] = ref
    let o = offset
    const pos = new Float32Array(this.bin, o, nv * 3); o += nv * 12
    const uv = new Float32Array(this.bin, o, nv * 4); o += nv * 16
    const color = new Uint8Array(this.bin, o, nv * 4); o += nv * 4
    const index = new Uint32Array(this.bin, o, ni)
    return { nv, ni, pos, uv, color, index }
  }
}
