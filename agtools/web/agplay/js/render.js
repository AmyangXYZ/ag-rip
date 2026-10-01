// Draws a recorded frame the way Unity's built-in forward renderer and the pipeline
// stand-in (AGSimPipeline, AGSimShadows, AGSimCharacter) drew it, with the game's own
// shaders translated to WGSL (agtools/shader_wgsl.py):
//   skinning + particles -> main-light shadow atlas (cascades as recorded, SHADOWCASTER)
//   -> opaque queue (ALWAYS, FORWARDBASE; front to back) -> character passes (recorded order)
//   -> opaque colour / depth grab -> transparent queue (back to front)
// Every uniform is filled by name: per-draw built-ins, per-frame (camera) built-ins, the
// renderer's property block, the material, then the pipeline's recorded globals. A name
// found nowhere is reported once (missingReport) - that list is the audit of what the port
// does not yet supply.
import * as U from "./unity.js"

const DRAWN_MAIN = new Set(["ALWAYS", "FORWARDBASE"])
const INT_MEMBERS = new Set(["unity_LightIndices"])
const HDR_FORMAT = "rgba16float"
const DEPTH_FORMAT = "depth24plus-stencil8"
const SHADOW_FORMAT = "depth32float"

const SKIN_WGSL = /* wgsl */ `
struct Params { count: u32, shapes: u32, bones: u32, pad: u32 }
@group(0) @binding(0) var<uniform> P: Params;
@group(0) @binding(1) var<storage, read> src: array<f32>;          // pos(3) nrm(3) tan(4) per vertex
@group(0) @binding(2) var<storage, read> skin: array<f32>;         // bone index(4) weight(4) per vertex
@group(0) @binding(3) var<storage, read> palette: array<mat4x4<f32>>; // bone.localToWorld * bindpose
@group(0) @binding(4) var<storage, read> shapes: array<f32>;       // per active shape: weight, delta offset
@group(0) @binding(5) var<storage, read> deltas: array<f32>;       // dpos(3) dnrm(3) dtan(3) per vertex, per frame
@group(0) @binding(6) var<storage, read_write> outPos: array<f32>;
@group(0) @binding(7) var<storage, read_write> outNrm: array<f32>;
@group(0) @binding(8) var<storage, read_write> outTan: array<f32>;
@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) id: vec3<u32>) {
  let v = id.x;
  if (v >= P.count) { return; }
  var p = vec3<f32>(src[v*10u], src[v*10u+1u], src[v*10u+2u]);
  var n = vec3<f32>(src[v*10u+3u], src[v*10u+4u], src[v*10u+5u]);
  var t = vec4<f32>(src[v*10u+6u], src[v*10u+7u], src[v*10u+8u], src[v*10u+9u]);
  for (var s = 0u; s < P.shapes; s++) {
    let w = shapes[s*2u];
    let base = u32(shapes[s*2u+1u]) + v*9u;
    p += w * vec3<f32>(deltas[base], deltas[base+1u], deltas[base+2u]);
    n += w * vec3<f32>(deltas[base+3u], deltas[base+4u], deltas[base+5u]);
    t = vec4<f32>(t.xyz + w * vec3<f32>(deltas[base+6u], deltas[base+7u], deltas[base+8u]), t.w);
  }
  var m = mat4x4<f32>(vec4<f32>(0.0), vec4<f32>(0.0), vec4<f32>(0.0), vec4<f32>(0.0));
  if (P.bones > 0u) {
    for (var k = 0u; k < 4u; k++) {
      let bi = u32(skin[v*8u+k]);
      let bw = skin[v*8u+4u+k];
      m += bw * palette[bi];
    }
  } else {
    m = mat4x4<f32>(vec4<f32>(1.0,0.0,0.0,0.0), vec4<f32>(0.0,1.0,0.0,0.0), vec4<f32>(0.0,0.0,1.0,0.0), vec4<f32>(0.0,0.0,0.0,1.0));
  }
  let wp = m * vec4<f32>(p, 1.0);
  let wn = normalize((m * vec4<f32>(n, 0.0)).xyz);
  let wt = normalize((m * vec4<f32>(t.xyz, 0.0)).xyz);
  outPos[v*3u] = wp.x; outPos[v*3u+1u] = wp.y; outPos[v*3u+2u] = wp.z;
  outNrm[v*3u] = wn.x; outNrm[v*3u+1u] = wn.y; outNrm[v*3u+2u] = wn.z;
  outTan[v*4u] = wt.x; outTan[v*4u+1u] = wt.y; outTan[v*4u+2u] = wt.z; outTan[v*4u+3u] = t.w;
}`

const BLIT_WGSL = /* wgsl */ `
@group(0) @binding(0) var src: texture_2d<f32>;
@group(0) @binding(1) var smp: sampler;
struct VO { @builtin(position) pos: vec4<f32>, @location(0) uv: vec2<f32> }
@vertex fn vs(@builtin(vertex_index) i: u32) -> VO {
  var p = array<vec2<f32>, 3>(vec2<f32>(-1.0, -1.0), vec2<f32>(3.0, -1.0), vec2<f32>(-1.0, 3.0));
  var o: VO;
  o.pos = vec4<f32>(p[i], 0.0, 1.0);
  // row 0 of the frame is the top of the picture here: screen top samples v = 0
  o.uv = vec2<f32>((p[i].x + 1.0) * 0.5, (1.0 - p[i].y) * 0.5);
  return o;
}
fn toSrgb(c: vec3<f32>) -> vec3<f32> {
  let lo = c * 12.92;
  let hi = 1.055 * pow(max(c, vec3<f32>(0.0)), vec3<f32>(1.0 / 2.4)) - 0.055;
  return select(hi, lo, c <= vec3<f32>(0.0031308));
}
@fragment fn fsLinear(i: VO) -> @location(0) vec4<f32> {
  let c = textureSample(src, smp, i.uv);
  return vec4<f32>(toSrgb(clamp(c.rgb, vec3<f32>(0.0), vec3<f32>(1.0))), 1.0);
}
@fragment fn fsPlain(i: VO) -> @location(0) vec4<f32> {
  return vec4<f32>(textureSample(src, smp, i.uv).rgb, 1.0);
}`

const DEPTH_COPY_WGSL = /* wgsl */ `
@group(0) @binding(0) var src: texture_depth_2d;
@vertex fn vs(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
  var p = array<vec2<f32>, 3>(vec2<f32>(-1.0, -1.0), vec2<f32>(3.0, -1.0), vec2<f32>(-1.0, 3.0));
  return vec4<f32>(p[i], 0.0, 1.0);
}
@fragment fn fs(@builtin(position) pos: vec4<f32>) -> @location(0) vec4<f32> {
  let d = textureLoad(src, vec2<i32>(pos.xy), 0);
  return vec4<f32>(d, d, d, d);
}`

function semantic(name) {
  let s = name.replace(/^in_u002e_var_u002e_/, "").replace(/_+$/, "")
  if (s === "TEXCOORD") s = "TEXCOORD0"
  if (s === "COLOR0") s = "COLOR"
  return s
}

export class GameRenderer {
  constructor(device, data, width, height) {
    this.device = device
    this.data = data
    this.scene = data.scene
    this.width = width
    this.height = height
    this.missing = new Map()        // uniform name -> count of draws that wanted it
    this.errors = []
    this.pipelines = new Map()
    this.modules = new Map()
    this.drawCache = new Map()
    this.materials = new Map(this.scene.materials.map((m) => [m.id, m]))
    this.renderers = new Map(this.scene.renderers.map((r) => [r.id, r]))
    this.meshInfo = new Map(this.scene.meshes.map((m) => [m.id, m]))
    this.textureInfo = new Map(this.scene.textures.map((t) => [t.id, t]))
    this.defaults = data.variants._textureDefaults || {}
    this.debug = globalThis.__agdebug || {}            // {blank: {texName: "white"|"black"|"cube"}, only: materialName substring}
  }

  // ---------------------------------------------------------------- resources
  async init(onProgress) {
    const d = this.device
    this.zero = d.createBuffer({ size: 64, usage: GPUBufferUsage.VERTEX })
    // a mesh without vertex colours reads white in Unity (other missing streams read zero)
    this.white = d.createBuffer({ size: 64, usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST })
    d.queue.writeBuffer(this.white, 0, new Float32Array(16).fill(1))
    this.linearClamp = d.createSampler({ magFilter: "linear", minFilter: "linear", mipmapFilter: "linear", addressModeU: "clamp-to-edge", addressModeV: "clamp-to-edge" })
    this.shadowSampler = d.createSampler({ compare: "greater-equal", magFilter: "linear", minFilter: "linear" })
    this.pointClamp = d.createSampler({})
    this.defaultTex = {}
    const solid = (name, rgba) => {
      const t = d.createTexture({ size: [1, 1], format: "rgba8unorm", usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST })
      d.queue.writeTexture({ texture: t }, new Uint8Array(rgba), { bytesPerRow: 4 }, [1, 1])
      this.defaultTex[name] = t.createView()
    }
    solid("white", [255, 255, 255, 255]); solid("black", [0, 0, 0, 0]); solid("gray", [128, 128, 128, 255])
    solid("grey", [128, 128, 128, 255]); solid("bump", [128, 128, 255, 255]); solid("red", [255, 0, 0, 255])
    solid("", [255, 255, 255, 255])
    const cube = d.createTexture({ size: [1, 1, 6], format: "rgba8unorm", usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST })
    d.queue.writeTexture({ texture: cube }, new Uint8Array(24), { bytesPerRow: 4, rowsPerImage: 1 }, [1, 1, 6])
    this.blackCube = cube.createView({ dimension: "cube" })
    this.noShadow = d.createTexture({ size: [1, 1], format: SHADOW_FORMAT, usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.RENDER_ATTACHMENT })
    this.textures = new Map()
    let done = 0
    const total = this.scene.textures.length + this.scene.meshes.length
    await Promise.all(this.scene.textures.map(async (t) => {
      try { this.textures.set(t.id, await this.loadTexture(t)) } catch (e) { this.errors.push(`texture ${t.id} ${t.name}: ${e}`) }
      onProgress?.(++done / total)
    }))
    this.meshes = new Map()
    await Promise.all(this.scene.meshes.map(async (m) => {
      const buf = await fetch(`${this.data.base}/meshes/${m.id}.bin`).then((r) => r.arrayBuffer())
      this.meshes.set(m.id, this.uploadMesh(m, buf))
      onProgress?.(++done / total)
    }))
    this.skinPipeline = d.createComputePipeline({ layout: "auto", compute: { module: d.createShaderModule({ code: SKIN_WGSL }), entryPoint: "main" } })
    this.blitModule = d.createShaderModule({ code: BLIT_WGSL })
    this.depthCopyModule = d.createShaderModule({ code: DEPTH_COPY_WGSL })
    this.skinned = new Map()
    this.particleBuffers = new Map()
    this.resize(this.width, this.height)
  }

  resize(w, h) {
    const d = this.device
    this.width = w; this.height = h
    const tex = (format, usage) => d.createTexture({ size: [w, h], format, usage })
    const RT = GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_SRC | GPUTextureUsage.COPY_DST
    this.color = tex(HDR_FORMAT, RT)
    this.depth = tex(DEPTH_FORMAT, GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING)
    this.opaqueCopy = tex(HDR_FORMAT, RT)
    this.depthCopy = tex("r32float", RT)
    this.drawCache.clear()
  }

  async loadTexture(t) {
    const d = this.device
    const faces = t.cube ? 6 : 1
    const mips = t.cube ? (t.mipsExported || 1) : Math.max(1, Math.floor(Math.log2(Math.max(t.width, t.height))) + 1)
    const format = t.hdr ? "rgba16float" : t.srgb ? "rgba8unorm-srgb" : "rgba8unorm"
    const tex = d.createTexture({
      size: [t.width, t.height, faces], format, mipLevelCount: t.cube ? mips : t.hdr ? 1 : mips,
      usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST | GPUTextureUsage.RENDER_ATTACHMENT,
    })
    if (t.cube) {
      // every face and mip, sampled along the WebGPU face directions on the Unity side
      // (AGCubeFace): the files are face-major, <id>_f<face>_m<mip>.bin, rows as uploaded
      await Promise.all(t.files.map(async (file, k) => {
        let f = Math.floor(k / mips)
        const m = k % mips, s = Math.max(1, t.width >> m)
        if (this.debug.cubeSwapY && (f === 2 || f === 3)) f = 5 - f
        const buf = await fetch(`${this.data.base}/textures/${file}`).then((r) => r.arrayBuffer())
        d.queue.writeTexture({ texture: tex, origin: [0, 0, f], mipLevel: m }, buf, { bytesPerRow: s * 8 }, [s, s, 1])
      }))
      return { tex, view: tex.createView({ dimension: "cube" }), sampler: d.createSampler({ magFilter: "linear", minFilter: "linear", mipmapFilter: "linear" }), info: t }
    }
    for (let f = 0; f < faces; f++) {
      const url = `${this.data.base}/textures/${t.files[f]}`
      if (t.hdr) {
        // raw RGBA16F rows as Unity holds them (row 0 = v 0): upload unchanged
        const buf = await fetch(url).then((r) => r.arrayBuffer())
        d.queue.writeTexture({ texture: tex, origin: [0, 0, f] }, buf, { bytesPerRow: t.width * 8 }, [t.width, t.height, 1])
      } else {
        // PNG rows run top down; Unity's texture row 0 is v 0 (the bottom): flip on upload
        const blob = await fetch(url).then((r) => r.blob())
        const bmp = await createImageBitmap(blob, { imageOrientation: "flipY", colorSpaceConversion: "none", premultiplyAlpha: "none" })
        d.queue.copyExternalImageToTexture({ source: bmp }, { texture: tex, origin: [0, 0, f] }, [t.width, t.height])
      }
    }
    if (!t.hdr && !t.cube && mips > 1) this.generateMips(tex, format, mips)
    const wrap = { Repeat: "repeat", Clamp: "clamp-to-edge", Mirror: "mirror-repeat", MirrorOnce: "mirror-repeat" }[t.wrap] ?? "repeat"
    const filter = t.filter === "Point" ? "nearest" : "linear"
    const sampler = d.createSampler({
      addressModeU: wrap, addressModeV: wrap, addressModeW: wrap, magFilter: filter, minFilter: filter,
      mipmapFilter: t.filter === "Trilinear" ? "linear" : "nearest",
      maxAnisotropy: filter === "linear" && t.filter === "Trilinear" ? Math.min(16, Math.max(1, t.aniso || 1)) : 1,
    })
    return { tex, view: tex.createView({ dimension: t.cube ? "cube" : "2d" }), sampler, info: t }
  }

  generateMips(tex, format, levels) {
    const d = this.device
    if (!this.mipModule) {
      this.mipModule = d.createShaderModule({ code: `
        @group(0) @binding(0) var src: texture_2d<f32>;
        @group(0) @binding(1) var smp: sampler;
        struct VO { @builtin(position) pos: vec4<f32>, @location(0) uv: vec2<f32> }
        @vertex fn vs(@builtin(vertex_index) i: u32) -> VO {
          var p = array<vec2<f32>, 3>(vec2<f32>(-1.0, -1.0), vec2<f32>(3.0, -1.0), vec2<f32>(-1.0, 3.0));
          var o: VO; o.pos = vec4<f32>(p[i], 0.0, 1.0); o.uv = vec2<f32>((p[i].x + 1.0) * 0.5, (1.0 - p[i].y) * 0.5); return o;
        }
        @fragment fn fs(i: VO) -> @location(0) vec4<f32> { return textureSample(src, smp, i.uv); }` })
      this.mipPipelines = {}
    }
    const pipe = this.mipPipelines[format] ??= d.createRenderPipeline({
      layout: "auto", vertex: { module: this.mipModule, entryPoint: "vs" },
      fragment: { module: this.mipModule, entryPoint: "fs", targets: [{ format }] }, primitive: { topology: "triangle-list" },
    })
    const enc = d.createCommandEncoder()
    for (let l = 1; l < levels; l++) {
      const bg = d.createBindGroup({ layout: pipe.getBindGroupLayout(0), entries: [
        { binding: 0, resource: tex.createView({ baseMipLevel: l - 1, mipLevelCount: 1 }) },
        { binding: 1, resource: this.linearClamp }] })
      const pass = enc.beginRenderPass({ colorAttachments: [{ view: tex.createView({ baseMipLevel: l, mipLevelCount: 1 }), loadOp: "clear", storeOp: "store" }] })
      pass.setPipeline(pipe); pass.setBindGroup(0, bg); pass.draw(3); pass.end()
    }
    d.queue.submit([enc.finish()])
  }

  uploadMesh(m, buf) {
    const d = this.device
    const gpu = d.createBuffer({ size: Math.max(16, Math.ceil(buf.byteLength / 4) * 4), usage: GPUBufferUsage.VERTEX | GPUBufferUsage.INDEX | GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST })
    d.queue.writeBuffer(gpu, 0, buf)
    return { info: m, gpu, cpu: buf }
  }

  // ---------------------------------------------------------------- skinning
  skinState(rid, rend, mesh) {
    let s = this.skinned.get(rid)
    if (s) return s
    const d = this.device, info = mesh.info, n = info.vertexCount
    const f32 = (name) => { const st = info.streams[name]; return st ? new Float32Array(mesh.cpu, st[0], n * st[1]) : null }
    const pos = f32("POSITION"), nrm = f32("NORMAL"), tan = f32("TANGENT")
    const src = new Float32Array(n * 10)
    for (let v = 0; v < n; v++) {
      src.set(pos.subarray(v * 3, v * 3 + 3), v * 10)
      if (nrm) src.set(nrm.subarray(v * 3, v * 3 + 3), v * 10 + 3)
      if (tan) src.set(tan.subarray(v * 4, v * 4 + 4), v * 10 + 6); else src[v * 10 + 9] = 1
    }
    const bi = f32("BLENDINDICES"), bw = f32("BLENDWEIGHTS")
    const skin = new Float32Array(n * 8)
    if (bi && bw) for (let v = 0; v < n; v++) { skin.set(bi.subarray(v * 4, v * 4 + 4), v * 8); skin.set(bw.subarray(v * 4, v * 4 + 4), v * 8 + 4) }
    const storage = (arr, extra = 0) => {
      const b = d.createBuffer({ size: Math.max(16, arr.byteLength), usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST | extra })
      d.queue.writeBuffer(b, 0, arr)
      return b
    }
    const shapes = info.blendShapes || []
    // blend shape deltas live in the mesh file already: bind the whole file as the delta buffer
    const out = (floats) => d.createBuffer({ size: n * floats * 4, usage: GPUBufferUsage.STORAGE | GPUBufferUsage.VERTEX })
    s = {
      n, src: storage(src), skin: storage(skin), shapes,
      palette: d.createBuffer({ size: Math.max(64, (info.bindposes?.length || 1) * 64), usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST }),
      shapeList: d.createBuffer({ size: Math.max(16, shapes.length * 8 + 8), usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST }),
      params: d.createBuffer({ size: 16, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST }),
      pos: out(3), nrm: out(3), tan: out(4),
      bindposes: (info.bindposes || []).map(U.mat),
    }
    s.bind = d.createBindGroup({ layout: this.skinPipeline.getBindGroupLayout(0), entries: [
      { binding: 0, resource: { buffer: s.params } }, { binding: 1, resource: { buffer: s.src } },
      { binding: 2, resource: { buffer: s.skin } }, { binding: 3, resource: { buffer: s.palette } },
      { binding: 4, resource: { buffer: s.shapeList } }, { binding: 5, resource: { buffer: mesh.gpu } },
      { binding: 6, resource: { buffer: s.pos } }, { binding: 7, resource: { buffer: s.nrm } }, { binding: 8, resource: { buffer: s.tan } }] })
    this.skinned.set(rid, s)
    return s
  }

  skin(enc, seq, rid, rec) {
    const rend = this.renderers.get(rid)
    const mesh = this.meshes.get(rend.mesh)
    if (!mesh || !rec.bones) return false
    const s = this.skinState(rid, rend, mesh)
    const bones = seq.bones(rec.bones)
    const pal = new Float32Array(s.bindposes.length * 16)
    for (let i = 0; i < s.bindposes.length; i++) pal.set(U.mul(bones.subarray(i * 16, i * 16 + 16), s.bindposes[i]), i * 16)
    this.device.queue.writeBuffer(s.palette, 0, pal)
    // active blend shapes: weight / frame weight, and the delta block of that frame (Unity:
    // a single-frame shape scales its deltas by weight / frameWeight)
    const list = []
    ;(rec.blend || []).forEach((w, i) => {
      if (!w) return
      const fr = s.shapes[i]?.frames
      if (!fr?.length) return
      const f = fr[fr.length - 1]
      list.push(w / f.weight, f.offset / 4)
    })
    this.device.queue.writeBuffer(s.shapeList, 0, new Float32Array(list.length ? list : [0, 0]))
    this.device.queue.writeBuffer(s.params, 0, new Uint32Array([s.n, list.length / 2, s.bindposes.length, 0]))
    const pass = enc.beginComputePass()
    pass.setPipeline(this.skinPipeline); pass.setBindGroup(0, s.bind); pass.dispatchWorkgroups(Math.ceil(s.n / 64)); pass.end()
    return true
  }

  uploadParticles(seq, rid, rec) {
    const p = seq.particles(rec.particles)
    if (!p || !p.nv) return null
    const d = this.device
    let b = this.particleBuffers.get(rid)
    const need = { pos: p.nv * 12, uv: p.nv * 16, color: p.nv * 4, index: p.ni * 4 }
    if (!b || Object.keys(need).some((k) => b[k].size < need[k])) {
      const mk = (size, usage) => d.createBuffer({ size: Math.max(16, Math.ceil(size * 1.5 / 4) * 4), usage: usage | GPUBufferUsage.COPY_DST })
      b = { pos: mk(need.pos, GPUBufferUsage.VERTEX), uv: mk(need.uv, GPUBufferUsage.VERTEX), color: mk(need.color, GPUBufferUsage.VERTEX), index: mk(need.index, GPUBufferUsage.INDEX) }
      this.particleBuffers.set(rid, b)
    }
    d.queue.writeBuffer(b.pos, 0, p.pos); d.queue.writeBuffer(b.uv, 0, p.uv)
    d.queue.writeBuffer(b.color, 0, p.color); d.queue.writeBuffer(b.index, 0, p.index)
    return { ...b, ni: p.ni }
  }

  // ---------------------------------------------------------------- pipelines
  module(code) {
    let m = this.modules.get(code)
    if (!m) {
      m = this.device.createShaderModule({ code })
      m.getCompilationInfo().then((ci) => ci.messages.filter((x) => x.type === "error").forEach((x) => this.errors.push(`wgsl: ${x.message}`)))
      this.modules.set(code, m)
    }
    return m
  }

  pipeline(variant, state, streams, target) {
    // the winding follows the projection's Y flip: the shadow atlas is drawn unflipped
    const frontFace = target === "shadow"
      ? this.debug.shadowFrontFace || ((this.debug.shadowFlip ?? false) ? "cw" : "ccw")
      : this.debug.frontFace || "cw"
    const key = `${variant.name}|${JSON.stringify(state)}|${streams.map((s) => s.format).join(",")}|${target}|${frontFace}|${this.debug.opaque || ""}`
    let p = this.pipelines.get(key)
    if (p) return p
    const info = variant.info
    const buffers = info.vertexInputs.map((vi, k) => ({
      arrayStride: streams[k].stride, stepMode: "vertex",
      attributes: [{ shaderLocation: vi.location, offset: 0, format: streams[k].format }],
    }))
    const st = { ...(state || {}), ...(this.debug.opaque ? { Blend: ["One", "Zero"], ZWrite: ["On"], Cull: ["Off"] } : {}) }
    const blend = st.Blend?.length && !(String(st.Blend[0]).toLowerCase() === "off") ? (() => {
      const b = st.Blend.map(String)
      const color = { srcFactor: U.blendFactor(b[0]), dstFactor: U.blendFactor(b[1] ?? "zero"), operation: U.blendOp(st.BlendOp?.[0]) }
      const alpha = b.length >= 4 ? { srcFactor: U.blendFactor(b[2]), dstFactor: U.blendFactor(b[3]), operation: U.blendOp(st.BlendOp?.[1] ?? st.BlendOp?.[0]) } : color
      return color.srcFactor === "one" && color.dstFactor === "zero" && alpha.srcFactor === "one" && alpha.dstFactor === "zero" ? undefined : { color, alpha }
    })() : undefined
    const shadow = target === "shadow"
    const stencil = (() => {
      if (!st.StencilRef && !st.StencilComp) return undefined
      const face = { compare: U.compare(st.StencilComp?.[0] ?? "Always"), passOp: U.stencilOp(st.StencilPass?.[0]), failOp: U.stencilOp(st.StencilFail?.[0]), depthFailOp: U.stencilOp(st.StencilZFail?.[0]) }
      return { face, readMask: +(st.StencilReadMask?.[0] ?? 255), writeMask: +(st.StencilWriteMask?.[0] ?? 255) }
    })()
    const desc = {
      layout: this.layoutFor(variant),
      vertex: { module: this.module(variant.vert), entryPoint: "vert", buffers },
      fragment: shadow ? { module: this.module(variant.frag), entryPoint: "frag", targets: [] }
        : { module: this.module(variant.frag), entryPoint: "frag", targets: [{ format: HDR_FORMAT, blend, writeMask: U.colorMask(st.ColorMask?.[0]) }] },
      // Unity's D3D winding: clockwise is front, also for the Y-flipped texture passes
      primitive: { topology: "triangle-list", cullMode: U.cull(st.Cull?.[0]), frontFace },
      depthStencil: {
        format: shadow ? SHADOW_FORMAT : DEPTH_FORMAT,
        depthWriteEnabled: U.zwrite(st.ZWrite?.[0]),
        depthCompare: U.depthCompare(st.ZTest?.[0]),
        ...(stencil && !shadow ? { stencilFront: stencil.face, stencilBack: stencil.face, stencilReadMask: stencil.readMask, stencilWriteMask: stencil.writeMask } : {}),
        ...(st.Offset ? { depthBias: -Math.round(+st.Offset[1] || 0), depthBiasSlopeScale: -(+st.Offset[0] || 0) } : {}),
      },
    }
    try {
      p = { pipeline: this.device.createRenderPipeline(desc), stencilRef: st.StencilRef ? +st.StencilRef[0] : 0 }
    } catch (e) {
      this.errors.push(`pipeline ${variant.name}: ${e.message}`)
      p = null
    }
    this.pipelines.set(key, p)
    return p
  }

  // An explicit layout from the variant's binding map: the translated modules declare every
  // uniform and texture the decompiled block did, used or not, and an "auto" layout would
  // drop the unused ones and refuse a bind group that names them.
  layoutFor(variant) {
    if (variant.layout) return variant.layout
    const entries = Object.values(variant.info.bindings).map((b) => {
      const visibility = (b.stages.includes("vertex") ? GPUShaderStage.VERTEX : 0) | (b.stages.includes("fragment") ? GPUShaderStage.FRAGMENT : 0)
      if (b.space === "uniform") return { binding: b.binding, visibility, buffer: { type: "uniform" } }
      if (/^sampler_comparison/.test(b.type)) return { binding: b.binding, visibility, sampler: { type: "comparison" } }
      if (/^sampler/.test(b.type)) return { binding: b.binding, visibility, sampler: { type: "filtering" } }
      if (/texture_depth/.test(b.type)) return { binding: b.binding, visibility, texture: { sampleType: "depth" } }
      const dim = /texture_cube/.test(b.type) ? "cube" : /texture_3d/.test(b.type) ? "3d" : "2d"
      return { binding: b.binding, visibility, texture: { sampleType: "float", viewDimension: dim } }
    })
    variant.bindLayout = this.device.createBindGroupLayout({ entries })
    variant.layout = this.device.createPipelineLayout({ bindGroupLayouts: [variant.bindLayout] })
    return variant.layout
  }

  // vertex streams a variant's inputs read, from a mesh, skinned output or particle buffers
  streamsFor(variant, mesh, skinned, particles) {
    return variant.info.vertexInputs.map((vi) => {
      const sem = semantic(vi.name)
      if (particles) {
        if (sem === "POSITION") return { buffer: particles.pos, offset: 0, stride: 12, format: "float32x3" }
        if (sem === "TEXCOORD0") return { buffer: particles.uv, offset: 0, stride: 16, format: "float32x4" }
        if (sem === "COLOR") return { buffer: particles.color, offset: 0, stride: 4, format: "unorm8x4" }
        return { buffer: this.zero, offset: 0, stride: 0, format: "float32x4" }
      }
      if (skinned) {
        if (sem === "POSITION") return { buffer: skinned.pos, offset: 0, stride: 12, format: "float32x3" }
        if (sem === "NORMAL") return { buffer: skinned.nrm, offset: 0, stride: 12, format: "float32x3" }
        if (sem === "TANGENT") return { buffer: skinned.tan, offset: 0, stride: 16, format: "float32x4" }
      }
      const st = mesh?.info.streams[sem]
      if (!st) return { buffer: sem === "COLOR" ? this.white : this.zero, offset: 0, stride: 0, format: "float32x4" }
      const dim = st[1]
      return { buffer: mesh.gpu, offset: st[0], stride: dim * 4, format: dim === 1 ? "float32" : `float32x${dim}` }
    })
  }

  // ---------------------------------------------------------------- uniforms by name
  lookup(name, ctx) {
    for (const src of ctx.sources) {
      const v = src[name]
      if (v !== undefined && v !== null) return v
    }
    // naga renames a member ending in a digit with a trailing "_" (_PlusLightingParams0_)
    if (name.endsWith("_")) {
      const bare = name.replace(/_+$/, "")
      for (const src of ctx.sources) {
        const v = src[bare]
        if (v !== undefined && v !== null) return v
      }
      name = bare
    }
    // a texture's _ST / _TexelSize / _HDR, from the material's texture slot
    const m = /^(.*)_(ST|TexelSize|HDR)$/.exec(name)
    if (m) {
      const slot = ctx.material?.props?.[m[1]]
      if (m[2] === "ST") return slot?.st ?? [1, 1, 0, 0]
      if (m[2] === "HDR") return [1, 1, 0, 0]
      const t = slot?.tex && this.textureInfo.get(slot.tex)
      if (t) return [1 / t.width, 1 / t.height, t.width, t.height]
      return [1, 1, 1, 1]
    }
    return undefined
  }

  fill(struct, ctx, cache) {
    const size = Math.max(16, struct.size)
    let buf = cache?.data
    if (!buf || buf.byteLength !== size) buf = new ArrayBuffer(size)
    const views = { f32: new Float32Array(buf), i32: new Int32Array(buf), u32: new Uint32Array(buf) }
    views.f32.fill(0)
    for (const m of struct.members) this.fillMember(m, ctx, views)
    return buf
  }

  // one member into a block's memory; false when no source has it
  fillMember(m, ctx, views) {
    const v = this.lookup(m.name, ctx)
    const at = m.offset / 4
    const n = m.size / 4
    if (v === undefined) {
      if (!ctx.silent) this.missing.set(m.name, (this.missing.get(m.name) || 0) + 1)
      views.f32.fill(0, at, at + n)
      return false
    }
    // integer bit patterns (light masks, bins) stay integers: through a float they could be
    // NaNs, which a Float32Array does not keep bit for bit
    const bits = v instanceof Uint32Array
    const flat = typeof v === "number" ? [v] : v.flat ? v.flat(2) : v
    const target = bits ? views.u32 : INT_MEMBERS.has(m.name) ? views.i32 : views.f32
    const arr = /^array<vec4<\w+>,\s*(\d+)>/.exec(m.type)
    if (arr && Array.isArray(v) && v.length === +arr[1] && typeof v[0] === "number") {
      // a scalar array (float[N] in HLSL, widened to float4[N]): one element per 16-byte
      // slot, read through .x
      for (let k = 0; k < v.length; k++) target[at + k * 4] = v[k]
    } else {
      for (let k = 0; k < Math.min(n, flat.length); k++) target[at + k] = flat[k]
    }
    return true
  }

  // ---------------------------------------------------------------- a frame
  frameContext(seq, f, camOverride) {
    const fr = seq.frames[f]
    const g = { ...(fr.globals || {}), ...(this.debug.globals || {}) }
    const cam = fr.cam
    const view = U.mat(camOverride?.view ?? cam.view)
    const proj = U.gpuProjection(U.mat(camOverride?.proj ?? cam.proj), true)
    const vp = U.mul(proj, view)
    const perFrame = {
      _WorldSpaceCameraPos: g._WorldSpaceCameraPos ?? cam.pos,
      unity_MatrixV: view, unity_MatrixInvV: U.invert(view), unity_MatrixVP: vp, unity_MatrixP: proj,
      glstate_matrix_projection: proj,
      _ProjectionParams: g._ProjectionParams, _ScreenParams: g._ScreenParams, _ZBufferParams: g._ZBufferParams,
      unity_OrthoParams: g.unity_OrthoParams, _Time: g._Time, _SinTime: g._SinTime, _CosTime: g._CosTime,
      unity_DeltaTime: g.unity_DeltaTime,
    }
    return { fr, g, perFrame, view, proj, vp, camPos: cam.pos }
  }

  plusLighting(n) {
    // AGSimPipeline.SetupPlusLighting: one tile, one depth bin holding every light
    const words = Math.max(1, Math.ceil(n / 32))
    const zb = new Uint32Array(4096), tile = new Uint32Array(16384)
    zb[0] = n > 0 ? ((n - 1) << 16) >>> 0 : 0xffff
    for (let w = 0; w < words; w++) {
      const bits = Math.min(Math.max(n - w * 32, 0), 32)
      const m = bits >= 32 ? 0xffffffff : ((1 << bits) >>> 0) - 1
      zb[1 + w] = m >>> 0
      tile[w] = m >>> 0
    }
    return { _PlusLighting_ZBins: zb, _PlusLighting_Tiles: tile }
  }

  textureFor(name, binding, ctx) {
    const type = binding.type
    // debugging: debug.blank = {name: "white" | "black" | "cube"} replaces a texture
    const blank = this.debug?.blank?.[name]
    if (blank) return { view: blank === "cube" ? this.blackCube : this.defaultTex[blank], sampler: this.linearClamp }
    const slot = ctx.material?.props?.[name]
    let id = slot?.tex ?? ctx.mpb?.[name]?.tex ?? this.scene.globalTextures?.[name]
    if (id && this.textures.has(id)) return this.textures.get(id)
    const rt = ctx.targets?.[name]
    if (rt) return rt
    if (/texture_depth/.test(type)) return { view: this.noShadowView ??= this.noShadow.createView(), sampler: this.shadowSampler }
    if (/texture_cube/.test(type)) return { view: this.blackCube, sampler: this.linearClamp }
    const def = this.defaults[ctx.material?.shader]?.[name] ?? "white"
    return { view: this.defaultTex[def] ?? this.defaultTex.white, sampler: this.linearClamp }
  }

  // one draw: pipeline + bind group with its uniforms filled for this frame
  // `slot` separates draws of one renderer pass within a frame (the shadow cascades): each
  // needs its own uniform buffers, the writes all land before the GPU runs the passes
  encodeDraw(pass, item, ctx, target, slot = 0) {
    const { variant, state, rid, sub } = item
    const rend = this.renderers.get(rid)
    const mesh = rend.mesh ? this.meshes.get(rend.mesh) : null
    const skinned = item.skinned ? this.skinned.get(rid) : null
    const streams = this.streamsFor(variant, mesh, skinned, item.particles)
    const pl = this.pipeline(variant, state, streams, target)
    if (!pl) return
    const info = variant.info
    const material = this.materials.get(item.mat)
    const rec = item.rec
    const m = skinned || item.particles ? U.IDENTITY : U.mat(rec.m)
    const perDraw = {
      unity_ObjectToWorld: m, unity_WorldToObject: U.invert(m),
      unity_WorldTransformParams: [0, 0, 0, U.det3(m) < 0 ? -1 : 1],
      unity_LightData: rec.mpb?.unity_LightData ?? [0, 0, 0, 0],
      unity_LightIndices: rec.mpb?.unity_LightIndices ?? [[0, 0, 0, 0], [0, 0, 0, 0]],
      unity_RenderingLayer: new Uint32Array([rec.layerBits != null ? rec.layerBits >>> 0 : 1, 0, 0, 0]),
      unity_LODFade: [1, 1, 0, 0],
    }
    // the value maps without their texture slots, once per material and per record
    this.propCache ??= new Map()
    let props = this.propCache.get(material)
    if (!props) {
      props = {}
      for (const [k, v] of Object.entries(material?.props || {})) if (!(v && typeof v === "object" && "tex" in v)) props[k] = v
      this.propCache.set(material, props)
    }
    let mpb = rec._values
    if (!mpb) {
      mpb = {}
      for (const [k, v] of Object.entries(rec.mpb || {})) if (!(v && typeof v === "object" && "tex" in v)) mpb[k] = v
      Object.defineProperty(rec, "_values", { value: mpb, enumerable: false })
    }
    const lctx = { material, mpb: rec.mpb, sources: [perDraw, ctx.perFrame, ctx.override || {}, mpb, props, ctx.g, ctx.plus], silent: false, targets: ctx.targets }
    const key = `${rid}|${sub}|${variant.name}|${target}|${item.passIndex}|${slot}`
    let cache = this.drawCache.get(key)
    if (!cache) {
      cache = { buffers: {}, bind: null, textures: {} }
      this.drawCache.set(key, cache)
    }
    const entries = []
    let texChanged = !cache.bind
    for (const [name, b] of Object.entries(info.bindings)) {
      if (b.space === "uniform") {
        const struct = info.structs[name]
        if (!struct) continue
        // What a block depends on, so it is filled and uploaded only when that changed:
        //   UnityPerFrame   the camera pass (one shared buffer per pass and variant)
        //   UnityPerDraw    this renderer's record (every frame for skinned / particles)
        //   everything else the frame's globals, this renderer's property block, the
        //                   pass overrides (shadow bias per cascade); the material is fixed
        if (name === "UnityPerFrame") {
          const k = `${ctx.passKey}|${variant.name}`
          ctx.shared ??= new Map()
          let sb = ctx.shared.get(k)
          if (!sb) {
            this.frameBuffers ??= new Map()
            let gpu = this.frameBuffers.get(k)
            const data = this.fill(struct, lctx)
            if (!gpu) this.frameBuffers.set(k, gpu = this.device.createBuffer({ size: data.byteLength, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST }))
            this.device.queue.writeBuffer(gpu, 0, data)
            ctx.shared.set(k, sb = gpu)
          }
          entries.push({ binding: b.binding, resource: { buffer: sb } })
          continue
        }
        let gb = cache.buffers[name]
        let stale
        if (name === "UnityPerDraw") stale = !gb || item.skinned || item.particles || gb.rec !== rec.m || gb.layer !== rec.layerBits
        else {
          stale = !gb || gb.mpb !== rec.mpb || gb.pass !== ctx.passKey
          if (!stale) {
            // only the globals that changed since this block was built: rewrite those members
            // and upload their bytes alone
            struct.names ??= struct.members.map((m) => m.name.replace(/_+$/, ""))
            const views = gb.views ??= { f32: new Float32Array(gb.data), i32: new Int32Array(gb.data), u32: new Uint32Array(gb.data) }
            for (let i = 0; i < struct.members.length; i++) {
              if ((this.changedAt.get(struct.names[i]) || 0) <= gb.built) continue
              const m = struct.members[i]
              this.fillMember(m, lctx, views)
              this.device.queue.writeBuffer(gb.gpu, m.offset, gb.data, m.offset, m.size)
            }
            gb.built = ctx.renderCount
          }
        }
        if (stale) {
          const data = this.fill(struct, lctx, gb)
          if (!gb) gb = cache.buffers[name] = { gpu: this.device.createBuffer({ size: data.byteLength, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST }), data }
          gb.data = data
          gb.views = null
          gb.built = ctx.renderCount
          gb.mpb = rec.mpb
          gb.pass = ctx.passKey
          gb.rec = rec.m
          gb.layer = rec.layerBits
          this.device.queue.writeBuffer(gb.gpu, 0, data)
        }
        entries.push({ binding: b.binding, resource: { buffer: gb.gpu } })
      } else if (/^texture/.test(b.type)) {
        const t = this.textureFor(name, b, lctx)
        if (cache.textures[name] !== t.view) { cache.textures[name] = t.view; texChanged = true }
        entries.push({ binding: b.binding, resource: t.view })
      } else if (/^sampler/.test(b.type)) {
        const texName = name.replace(/^sampler_?/, "")
        const guess = texName.startsWith("_") || info.bindings[texName] ? texName : `_${texName}`
        const tb = info.bindings[texName] ? texName : info.bindings[`_${texName}`] ? `_${texName}` : guess
        const t = this.textureFor(tb, info.bindings[tb] || { type: "texture_2d" }, lctx)
        const smp = /comparison/.test(b.type) ? this.shadowSampler : (t.sampler || this.linearClamp)
        entries.push({ binding: b.binding, resource: smp })
      }
    }
    if (texChanged || cache.pipeline !== pl.pipeline) {
      try {
        cache.bind = this.device.createBindGroup({ layout: variant.bindLayout, entries })
        cache.pipeline = pl.pipeline
      } catch (e) {
        this.errors.push(`bind ${variant.name}: ${e.message}`)
        return
      }
    }
    pass.setPipeline(pl.pipeline)
    pass.setBindGroup(0, cache.bind)
    pass.setStencilReference?.(pl.stencilRef)
    streams.forEach((s, k) => pass.setVertexBuffer(k, s.buffer, s.offset))
    if (item.particles) {
      pass.setIndexBuffer(item.particles.index, "uint32")
      pass.drawIndexed(item.particles.ni)
      return
    }
    const smd = mesh.info.submeshes[Math.min(sub, mesh.info.submeshes.length - 1)]
    pass.setIndexBuffer(mesh.gpu, "uint32", smd.offset, smd.count * 4)
    pass.drawIndexed(smd.count)
  }

  passesOf(matId, lightModes) {
    const skip = this.debug.skipModes || []
    return (this.data.variants[matId] || []).filter((p) => lightModes.has(p.lightMode) && !skip.includes(p.lightMode) && this.data.shaders[p.variant])
  }

  render(seq, f, outView, outFormat) {
    const d = this.device
    const ctx = this.frameContext(seq, f)
    const state = seq.renderersAt(f)
    // the frame's globals, as one version number: blocks built from them are rebuilt only
    // when they change (the light table, fog, SH are constant through a sequence)
    // per global name, the render in which its value last changed: a block is rebuilt only
    // when one of its own members did (_Time changes every frame, the light table never)
    this.renderCount = (this.renderCount || 0) + 1
    this.lastValues ??= new Map()
    this.changedAt ??= new Map()
    const cur = { ...ctx.g, ...(ctx.fr.lightLayerMaskBits ? { _AdditionalLightsLayerMasks: ctx.fr.lightLayerMaskBits } : {}) }
    for (const [k, v] of Object.entries(cur)) {
      const js = JSON.stringify(v)
      if (this.lastValues.get(k) !== js) { this.lastValues.set(k, js); this.changedAt.set(k, this.renderCount) }
    }
    ctx.renderCount = this.renderCount
    ctx.passKey = "main"
    // AGSimPipeline pads its light table with zero colours past the last light
    const cols = ctx.g._AdditionalLightsColor || []
    let n = ctx.fr.plusLightCount
    if (n == null) { n = 0; cols.forEach((c, i) => { if (c && (c[0] || c[1] || c[2])) n = i + 1 }) }
    ctx.plus = this.plusLighting(n)
    // the light layer masks as their integer bits (floats reinterpreted, as the shader reads them)
    if (ctx.fr.lightLayerMaskBits) {
      const bits = new Uint32Array(255)
      ctx.fr.lightLayerMaskBits.forEach((b, i) => { bits[i] = b >>> 0 })
      // one element per 16-byte register (float4[N] read .x): bits at every 4th word
      const wide = new Uint32Array(255 * 4)
      bits.forEach((b, i) => { wide[i * 4] = b })
      ctx.g._AdditionalLightsLayerMasks = wide
    }
    const enc = d.createCommandEncoder()
    const skinned = new Set(), particles = new Map()
    for (const [rid, rec] of state) {
      const rend = this.renderers.get(rid)
      if (!rend || !rec.on) continue
      if (rend.kind === "skinned" && this.skin(enc, seq, rid, rec)) skinned.add(rid)
      if (rend.kind === "particles" && rec.particles) {
        const p = this.uploadParticles(seq, rid, rec)
        if (p) particles.set(rid, p)
      }
    }
    // -- main light shadow atlas, as AGSimShadows drew it
    const sh = ctx.fr.shadow
    if (sh) {
      if (!this.shadowAtlas || this.shadowAtlas.width !== sh.resolution) {
        this.shadowAtlas = d.createTexture({ size: [sh.resolution, sh.resolution], format: SHADOW_FORMAT, usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_SRC })
        this.shadowView = this.shadowAtlas.createView()
      }
      const pass = enc.beginRenderPass({ colorAttachments: [], depthStencilAttachment: { view: this.shadowView, depthClearValue: 0, depthLoadOp: "clear", depthStoreOp: "store" } })
      const casterModes = new Set(["SHADOWCASTER"])
      sh.cascades.forEach((c, ci) => {
        // checked texel by texel against the atlas Unity drew (AGDlcRecord -agShadowAt): the
        // cascades go in unflipped, tile rectangles as given, wound the other way round
        // (pipeline()); depths agree to 3e-5
        const flip = this.debug.shadowFlip ?? false, vflip = this.debug.shadowViewportFlip ?? false
        const view = U.mat(c.view), proj = U.gpuProjection(U.mat(c.proj), flip)
        const vp = U.mul(proj, view)
        const H = sh.resolution
        pass.setViewport(c.rect[0], vflip ? H - c.rect[1] - c.rect[3] : c.rect[1], c.rect[2], c.rect[3], 0, 1)
        const cctx = { ...ctx, perFrame: { ...ctx.perFrame, unity_MatrixV: view, unity_MatrixInvV: U.invert(view), unity_MatrixVP: vp, unity_MatrixP: proj, glstate_matrix_projection: proj },
          override: { sim_ShadowBias: c.bias }, targets: {}, passKey: `shadow${ci}`, shared: new Map() }
        for (const [rid, sub] of c.draws) {
          const rec = state.get(rid), rend = this.renderers.get(rid)
          if (!rec || !rend) continue
          const matId = rend.materials[sub] ?? rec.mats?.[sub]
          const cm = this.materials.get(matId)
          if (this.debug.skipCasters && cm && this.debug.skipCasters.some((x) => cm.name.includes(x) || cm.keywords.includes(x))) continue
          for (const p of this.passesOf(matId, casterModes))
            this.encodeDraw(pass, { variant: this.data.shaders[p.variant], state: p.state, rid, sub, mat: matId, rec, skinned: skinned.has(rid), passIndex: p.pass }, cctx, "shadow", ci)
        }
      })
      pass.end()
    }
    ctx.targets = {
      _MainLightShadowmapTexture: sh ? { view: this.shadowView, sampler: this.shadowSampler } : null,
      _OpaqueTexture: { view: this.opaqueCopy.createView(), sampler: this.linearClamp },
      _DepthIntermediate: { view: this.depthCopy.createView(), sampler: this.pointClamp },
      _CameraDepthTexture: { view: this.depthCopy.createView(), sampler: this.pointClamp },
    }
    // -- collect draws
    const opaque = [], transparent = []
    for (const [rid, rec] of state) {
      const rend = this.renderers.get(rid)
      if (!rend || !rec.on) continue
      if (rend.kind === "skinned" && !skinned.has(rid)) continue
      const p = rend.kind === "particles" ? particles.get(rid) : null
      if (rend.kind === "particles" && !p) continue
      const mats = rec.mats || rend.materials
      const center = this.worldCenter(rend, rec, skinned.has(rid) || !!p)
      const dist = Math.hypot(center[0] - ctx.camPos[0], center[1] - ctx.camPos[1], center[2] - ctx.camPos[2])
      mats.forEach((matId, sub) => {
        if (!matId) return
        const mat = this.materials.get(matId)
        if (this.debug.only && !this.debug.only.some((o) => mat.name.includes(o) || mat.shader.includes(o))) return
        for (const pv of this.passesOf(matId, DRAWN_MAIN)) {
          const item = { variant: this.data.shaders[pv.variant], state: pv.state, rid, sub, mat: matId, rec, skinned: skinned.has(rid), particles: p, passIndex: pv.pass, queue: mat.queue, dist }
          ;(mat.queue > 2500 ? transparent : opaque).push(item)
        }
      })
    }
    opaque.sort((a, b) => a.queue - b.queue || a.dist - b.dist || a.passIndex - b.passIndex)
    transparent.sort((a, b) => a.queue - b.queue || b.dist - a.dist || a.passIndex - b.passIndex)
    // -- opaque + character passes
    let pass = enc.beginRenderPass({
      colorAttachments: [{ view: this.color.createView(), clearValue: [0, 0, 0, 0], loadOp: "clear", storeOp: "store" }],
      depthStencilAttachment: { view: this.depth.createView(), depthClearValue: 0, depthLoadOp: "clear", depthStoreOp: "store", stencilClearValue: 0, stencilLoadOp: "clear", stencilStoreOp: "store" },
    })
    for (const it of opaque) this.encodeDraw(pass, it, ctx, "main")
    for (const [rid, matId, sub, passIdx] of ctx.fr.charPasses || []) {
      const rec = state.get(rid)
      if (!rec) continue
      const pv = (this.data.variants[matId] || []).find((p) => p.pass === passIdx)
      if (!pv || !this.data.shaders[pv.variant] || (this.debug.skipModes || []).includes(pv.lightMode)) continue
      this.encodeDraw(pass, { variant: this.data.shaders[pv.variant], state: pv.state, rid, sub, mat: matId, rec, skinned: skinned.has(rid), passIndex: pv.pass }, ctx, "main")
    }
    pass.end()
    // -- the opaque grab (AGSimPipeline: _OpaqueTexture, _DepthIntermediate)
    enc.copyTextureToTexture({ texture: this.color }, { texture: this.opaqueCopy }, [this.width, this.height])
    this.copyDepth(enc)
    // -- transparent
    pass = enc.beginRenderPass({
      colorAttachments: [{ view: this.color.createView(), loadOp: "load", storeOp: "store" }],
      depthStencilAttachment: { view: this.depth.createView(), depthLoadOp: "load", depthStoreOp: "store", stencilLoadOp: "load", stencilStoreOp: "store" },
    })
    for (const it of transparent) this.encodeDraw(pass, it, ctx, "main")
    pass.end()
    // debugging: the shadow atlas as grey (near = white)
    if (this.debug.showShadow && this.shadowView) {
      this.shadowDebugModule ??= d.createShaderModule({ code: `
        @group(0) @binding(0) var src: texture_depth_2d;
        @vertex fn vs(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
          var p = array<vec2<f32>, 3>(vec2<f32>(-1.0, -1.0), vec2<f32>(3.0, -1.0), vec2<f32>(-1.0, 3.0));
          return vec4<f32>(p[i], 0.0, 1.0);
        }
        @fragment fn fs(@builtin(position) pos: vec4<f32>) -> @location(0) vec4<f32> {
          let dims = vec2<f32>(textureDimensions(src));
          let uv = pos.xy / vec2<f32>(${this.width}.0, ${this.height}.0);
          let d = textureLoad(src, vec2<i32>(uv * dims), 0);
          return vec4<f32>(d * 20.0, d * 20.0, d * 20.0, 1.0);
        }` })
      const pipe = d.createRenderPipeline({ layout: "auto", vertex: { module: this.shadowDebugModule, entryPoint: "vs" },
        fragment: { module: this.shadowDebugModule, entryPoint: "fs", targets: [{ format: outFormat }] }, primitive: { topology: "triangle-list" } })
      const bg = d.createBindGroup({ layout: pipe.getBindGroupLayout(0), entries: [{ binding: 0, resource: this.shadowView }] })
      const pass = enc.beginRenderPass({ colorAttachments: [{ view: outView, loadOp: "clear", clearValue: [0, 0, 0, 1], storeOp: "store" }] })
      pass.setPipeline(pipe); pass.setBindGroup(0, bg); pass.draw(3); pass.end()
      d.queue.submit([enc.finish()])
      return
    }
    // -- post (AGSimPostFX: bloom, then Final with the colour-grading LUT), then present
    // the recorder reads the post values before Unity runs the image effects, so the first
    // frames may have none: the sequence's first complete set stands in (they are constant)
    seq.firstPost ??= seq.frames.find((f) => f.post?.lut)?.post
    const post = ctx.fr.post?.lut ? ctx.fr.post : seq.firstPost, pv = this.data.variants._post
    if (post && pv?.final?.[0] && !this.debug.noPost) {
      this.postChain(enc, ctx, post, pv)
      this.present(enc, outView, outFormat, this.ldr, "fsPlain")   // the post chain keeps D3D's clip space: upright
    } else {
      this.present(enc, outView, outFormat)
    }
    d.queue.submit([enc.finish()])
  }

  // AGSimPostFX.OnRenderImage / Bloom, pass for pass
  postChain(enc, ctx, post, pv) {
    const d = this.device
    const W = this.width, H = this.height
    const rt = (key, w, h, format) => {
      this.rts ??= {}
      const k = `${key}|${w}|${h}|${format}`
      return this.rts[k] ??= { tex: d.createTexture({ size: [w, h], format, usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING }), w, h, format }
    }
    const view = (r) => ({ view: r.view ??= r.tex.createView(), sampler: this.linearClamp, w: r.w, h: r.h })
    const scene = { view: this.color.createView(), sampler: this.linearClamp, w: W, h: H }
    let bloomTex = { view: this.defaultTex.black, sampler: this.linearClamp, w: 1, h: 1 }
    const contrast = post.final.contrast ?? 1
    if (post.bloomEnabled && contrast > 0 && pv.bloom?.every(Boolean)) {
      let w = Math.max(1, Math.floor(W / 2)), h = Math.max(1, Math.floor(H / 2))
      const mips = Math.min(16, Math.max(1, Math.floor(Math.log2(Math.max(w, h)) - 1)))
      const down = [], up = []
      for (let i = 0; i < mips; i++) {
        down.push(rt(`down${i}`, w, h, "rgba16float")); up.push(rt(`up${i}`, w, h, "rgba16float"))
        w = Math.max(1, Math.floor(w / 2)); h = Math.max(1, Math.floor(h / 2))
      }
      const bloom = (pass, src, dst, extra = {}) => this.fullscreen(enc, pv.bloom[pass], dst, { _MainTex: src, ...extra }, [post.bloom || {}, ctx.g])
      bloom(0, scene, down[0])
      for (let i = 1; i < mips; i++) {
        bloom(1, view(down[i - 1]), up[i])
        bloom(2, view(up[i]), down[i])
      }
      for (let i = mips - 2; i >= 0; i--) {
        const low = i === mips - 2 ? down[i + 1] : up[i + 1]
        bloom(3, view(down[i]), up[i], { _MainTexLowMip: view(low) })
      }
      bloomTex = view(mips === 1 ? down[0] : up[0])
    }
    this.ldr = rt("ldr", W, H, "rgba8unorm-srgb")
    const lut = post.lut && this.textures.get(post.lut)
    const lutTex = lut ? { view: lut.view, sampler: this.linearClamp, w: lut.info.width, h: lut.info.height } : { view: this.defaultTex.white, sampler: this.linearClamp, w: 1, h: 1 }
    this.fullscreen(enc, pv.final[0], this.ldr, { _MainTex: scene, SimPipelineBloom: bloomTex, _ColorGraddingLut: lutTex }, [post.final, ctx.g])
  }

  // one fullscreen pass of a translated post shader into a target
  fullscreen(enc, variantName, target, textures, valueSources) {
    const d = this.device
    const variant = this.data.shaders[variantName]
    if (!variant) return
    const info = variant.info
    this.fsPipelines ??= new Map()
    const key = `${variantName}|${target.format}`
    let pipe = this.fsPipelines.get(key)
    if (!pipe) {
      pipe = d.createRenderPipeline({ layout: this.layoutFor(variant),
        vertex: { module: this.module(variant.vert), entryPoint: "vert", buffers: info.vertexInputs.map((vi) => ({ arrayStride: 0, attributes: [{ shaderLocation: vi.location, offset: 0, format: "float32x4" }] })) },
        fragment: { module: this.module(variant.frag), entryPoint: "frag", targets: [{ format: target.format }] },
        primitive: { topology: "triangle-list" } })
      this.fsPipelines.set(key, pipe)
    }
    const sizes = Object.fromEntries(Object.entries(textures).map(([n, t]) => [`${n}_TexelSize`, [1 / t.w, 1 / t.h, t.w, t.h]]))
    const perFrame = { _ScreenParams: [this.width, this.height, 1 + 1 / this.width, 1 + 1 / this.height] }
    const lctx = { material: null, sources: [sizes, ...valueSources, perFrame], silent: true }
    const entries = []
    for (const [name, b] of Object.entries(info.bindings)) {
      if (b.space === "uniform") {
        const struct = info.structs[name]
        if (!struct) continue
        const data = this.fill(struct, lctx)
        const gb = d.createBuffer({ size: data.byteLength, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST })
        d.queue.writeBuffer(gb, 0, data)
        entries.push({ binding: b.binding, resource: { buffer: gb } })
      } else if (/^texture/.test(b.type)) {
        entries.push({ binding: b.binding, resource: (textures[name] || { view: this.defaultTex.black }).view })
      } else if (/^sampler/.test(b.type)) {
        entries.push({ binding: b.binding, resource: /comparison/.test(b.type) ? this.shadowSampler : this.linearClamp })
      }
    }
    const bg = d.createBindGroup({ layout: variant.bindLayout, entries })
    const pass = enc.beginRenderPass({ colorAttachments: [{ view: target.view ??= target.tex.createView(), loadOp: "clear", clearValue: [0, 0, 0, 0], storeOp: "store" }] })
    pass.setPipeline(pipe); pass.setBindGroup(0, bg)
    info.vertexInputs.forEach((_, k) => pass.setVertexBuffer(k, this.zero))
    pass.draw(3); pass.end()
  }

  worldCenter(rend, rec, identity) {
    const mesh = rend.mesh && this.meshInfo.get(rend.mesh)
    const c = mesh?.bounds?.center ?? [0, 0, 0]
    return identity || !rec.m ? c : U.transformPoint(U.mat(rec.m), c)
  }

  copyDepth(enc) {
    const d = this.device
    this.depthCopyPipeline ??= d.createRenderPipeline({ layout: "auto", vertex: { module: this.depthCopyModule, entryPoint: "vs" },
      fragment: { module: this.depthCopyModule, entryPoint: "fs", targets: [{ format: "r32float" }] }, primitive: { topology: "triangle-list" } })
    const bg = d.createBindGroup({ layout: this.depthCopyPipeline.getBindGroupLayout(0), entries: [{ binding: 0, resource: this.depth.createView({ aspect: "depth-only" }) }] })
    const pass = enc.beginRenderPass({ colorAttachments: [{ view: this.depthCopy.createView(), loadOp: "clear", storeOp: "store" }] })
    pass.setPipeline(this.depthCopyPipeline); pass.setBindGroup(0, bg); pass.draw(3); pass.end()
  }

  // to the canvas: the linear HDR frame clamped to sRGB (no post), or the post chain's sRGB
  // result copied as it is ("fsPlain"; the canvas is written through an -srgb view)
  present(enc, outView, outFormat, src, fs = "fsLinear") {
    const d = this.device
    this.presentPipelines ??= {}
    const pipe = this.presentPipelines[outFormat + fs] ??= d.createRenderPipeline({ layout: "auto",
      vertex: { module: this.blitModule, entryPoint: "vs" },
      fragment: { module: this.blitModule, entryPoint: fs, targets: [{ format: outFormat }] }, primitive: { topology: "triangle-list" } })
    const view = src ? (src.view ??= src.tex.createView()) : this.color.createView()
    const bg = d.createBindGroup({ layout: pipe.getBindGroupLayout(0), entries: [{ binding: 0, resource: view }, { binding: 1, resource: this.linearClamp }] })
    const pass = enc.beginRenderPass({ colorAttachments: [{ view: outView, clearValue: [0, 0, 0, 1], loadOp: "clear", storeOp: "store" }] })
    pass.setPipeline(pipe); pass.setBindGroup(0, bg); pass.draw(3); pass.end()
  }

  missingReport() {
    return [...this.missing.entries()].sort((a, b) => b[1] - a[1])
  }

  // debugging: the shadow atlas as last drawn, raw depth floats row by row (for comparing
  // with the atlas Unity drew, AGDlcRecord -agShadowAt)
  async readShadow() {
    const t = this.shadowAtlas
    if (!t) return null
    const n = t.width, row = n * 4
    const buf = this.device.createBuffer({ size: row * n, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ })
    const enc = this.device.createCommandEncoder()
    enc.copyTextureToBuffer({ texture: t, aspect: "depth-only" }, { buffer: buf, bytesPerRow: row }, [n, n])
    this.device.queue.submit([enc.finish()])
    await buf.mapAsync(GPUMapMode.READ)
    const out = new Float32Array(buf.getMappedRange().slice(0))
    buf.destroy()
    return out
  }
}
