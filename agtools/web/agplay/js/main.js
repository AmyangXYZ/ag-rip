// The compare page: the recorded Unity frames beside the same frames drawn by WebGPU with
// the game's translated shaders. data/index.json lists the sequences, which frames have a
// Unity reference image (data/unity/<seq>_<frame>.png, exact) and the full-rate Unity video
// (data/unity/<seq>.webm, every frame, lossy) that plays while the page plays.
import { loadJSON, loadScene, Sequence } from "./data.js"
import { GameRenderer } from "./render.js"

const $ = (id) => document.getElementById(id)
const DATA = new URLSearchParams(location.search).get("data") || "data"
const state = { seq: null, f: 0, playing: false, mode: "side", blinkUnity: false, refs: {} }

function status(msg) { $("status").textContent = msg }

async function main() {
  if (!navigator.gpu) { status("WebGPU is not available in this browser."); return }
  const adapter = await navigator.gpu.requestAdapter({ powerPreference: "high-performance" })
  const features = ["float32-filterable"].filter((f) => adapter.features.has(f))
  const device = await adapter.requestDevice({
    requiredFeatures: features,
    requiredLimits: { maxUniformBufferBindingSize: Math.min(65536, adapter.limits.maxUniformBufferBindingSize), maxBindingsPerBindGroup: adapter.limits.maxBindingsPerBindGroup, maxSampledTexturesPerShaderStage: adapter.limits.maxSampledTexturesPerShaderStage, maxSamplersPerShaderStage: adapter.limits.maxSamplersPerShaderStage },
  })
  device.addEventListener("uncapturederror", (e) => report(`gpu: ${e.error.message}`))
  const index = await loadJSON(`${DATA}/index.json`)
  $("title").textContent = `${index.title || "AG DLC"} — compare`
  document.title = `${index.skin} Compare`
  status("loading scene…")
  const data = await loadScene(DATA)
  const canvas = $("gpu")
  const ctx = canvas.getContext("webgpu")
  const format = navigator.gpu.getPreferredCanvasFormat()
  // written through an sRGB view: the post chain's result is linear, as Unity's sRGB target takes it
  ctx.configure({ device, format, alphaMode: "opaque", viewFormats: [`${format}-srgb`] })
  const renderer = new GameRenderer(device, data, canvas.width, canvas.height)
  await renderer.init((p) => status(`loading textures and meshes… ${(p * 100) | 0}%`))
  state.index = index
  state.renderer = renderer
  state.ctx = ctx
  state.format = format
  state.sequences = {}
  for (const s of index.sequences) {
    const o = document.createElement("option")
    o.value = o.textContent = s.name
    $("seq").append(o)
    state.refs[s.name] = { unity: s.unityFrames || [], unity_raw: s.rawFrames || [], video: s.unityVideo || null }
    state.audio ??= {}
    state.audio[s.name] = s.audio
  }
  state.refset = "unity"
  $("refset").onchange = () => { state.refset = $("refset").value; draw() }
  $("post").onchange = () => { state.renderer.debug.noPost = !$("post").checked; draw() }
  $("seq").onchange = () => openSequence($("seq").value)
  $("frame").oninput = () => { state.f = +$("frame").value; if (state.playing) startAudio(); draw(state.playing) }
  $("snap").onchange = () => draw()
  $("mode").onchange = () => { state.mode = $("mode").value; layout(); draw() }
  $("play").onclick = () => setPlaying(!state.playing)
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space" && state.mode === "blink") { e.preventDefault(); state.blinkUnity = !state.blinkUnity; layout() }
    if (e.code === "ArrowRight") step(1)
    if (e.code === "ArrowLeft") step(-1)
  })
  $("stage").addEventListener("pointermove", (e) => {
    if (state.mode !== "swipe") return
    const r = $("stage").getBoundingClientRect()
    swipe((e.clientX - r.left) / r.width)
  })
  await openSequence(index.sequences[0].name)
  layout()
  window.__agplay = { ready: true, render: renderAt, renderer, state }
}

function report(msg) { $("errors").textContent += msg + "\n" }

async function openSequence(name, playing = false) {
  status(`loading ${name} frames…`)
  state.seq = state.sequences[name] ??= await Sequence.load(DATA, name, (p) => status(`loading ${name} frames… ${(p * 100) | 0}%`))
  state.f = 0
  $("frame").max = state.seq.length - 1
  $("seq").value = name
  const v = $("refv"), src = state.refs[name].video
  if (src && !v.src.endsWith(`${DATA}/${src}`)) {
    // a new source shows nothing until its first frame decodes: frame 0's reference meanwhile
    if (state.refs[name].unity[0] === 0) v.poster = `${DATA}/unity/${name}_0000.png`
    else v.removeAttribute("poster")
    v.src = `${DATA}/${src}`
  } else if (!src) v.removeAttribute("src")
  draw(playing)
}

function step(d) {
  const refs = state.refs[state.seq.name][state.refset]
  if ($("snap").checked && refs.length) {
    const i = refs.findIndex((f) => f >= state.f)
    const j = Math.max(0, Math.min(refs.length - 1, (i < 0 ? refs.length : i) + d))
    state.f = refs[j]
  } else state.f = Math.max(0, Math.min(state.seq.length - 1, state.f + d))
  $("frame").value = state.f
  draw()
}

// Playback runs on the audio's clock (30 fps frames), and like the Unity player moves on
// to the next sequence when one ends: touch1, then touch2, then around again.
function setPlaying(on) {
  state.playing = on
  $("play").textContent = on ? "Pause" : "Play"
  const a = $("audio")
  if (!on) { a.pause(); $("refv").pause(); $("refv").playbackRate = 1; draw(); return }
  startAudio()
  const v = $("refv")
  if (useVideo(true) && v.src && Math.abs(v.currentTime - state.f / 30) > 0.5 / 30) v.currentTime = state.f / 30
  tick()
}

function startAudio() {
  state.clock = { t: state.f / 30, at: performance.now(), start: state.f / 30 }
  const a = $("audio"), src = state.audio[state.seq.name]
  if (!src) { a.pause(); a.removeAttribute("src"); return }
  const url = `${DATA}/${src}`
  if (!a.src.endsWith(url)) a.src = url
  a.currentTime = state.f / 30
  state.clock.start = a.currentTime
  a.play().catch(() => {})
}

// The play clock in seconds: the audio's while it plays; held while the audio is still
// loading or seeking (the video is paused with it); and the wall clock, from where the audio
// left off, when there is no audio or it ends before the frames do (107402 touch1: audio
// 22.63 s, frames 23.03 s).
function playTime() {
  const a = $("audio"), c = state.clock, now = performance.now()
  c.held = false
  if (a.src && !a.paused && !a.ended) {
    // the audio reports playing a while (~0.5 s) before its time moves off the start
    if (a.readyState >= 3 && !a.seeking && a.currentTime > c.start + 0.005) c.t = a.currentTime
    else c.held = true
    c.at = now
    return c.t
  }
  return c.t + (now - c.at) / 1000
}

async function tick() {
  if (!state.playing) return
  let t = playTime()
  const f = Math.floor(t * 30)
  if (f >= state.seq.length) {
    const names = state.index.sequences.map((s) => s.name)
    const next = names[(names.indexOf(state.seq.name) + 1) % names.length]
    await openSequence(next, true)
    state.f = 0
    startAudio()
    t = 0
    if (!state.playing) return
  } else state.f = f
  $("frame").value = state.f
  syncVideo(t)
  draw(true)
  requestAnimationFrame(tick)
}

// The Unity side while playing, or when not limited to the reference frames: the full-rate
// video (with post only; the no-post set has reference frames alone).
function useVideo(playing) {
  return !!state.refs[state.seq.name].video && state.refset === "unity" && (playing || !$("snap").checked)
}

// Keep the video on the play clock (frame n is shown from n/30 s on). It plays by itself
// and is steered by its rate: 10% faster or slower while it is off by more than half a
// frame. Only a real jump (more than 4 frames: a start, a scrub, a stall) seeks it, once,
// and not again until that seek has landed - each seek stalls it while it decodes from
// the keyframe before.
function syncVideo(t) {
  const v = $("refv")
  if (!useVideo(true) || !v.src) return
  if (state.clock.held) { v.pause(); return }
  if (v.paused && !v.ended) v.play().catch(() => {})
  if (v.seeking || v.readyState < 3 || !(t < v.duration - 1 / 30)) return
  const d = v.currentTime - t
  if (Math.abs(d) > 4 / 30) { v.playbackRate = 1; v.currentTime = t }
  else v.playbackRate = Math.abs(d) > 0.5 / 30 ? (d > 0 ? 0.9 : 1.1) : 1
}

// paused on frame f: show exactly that video frame (seek to its middle, wait for it)
async function seekVideo(f) {
  const v = $("refv")
  if (!v.src) return
  v.pause()
  const t = (f + 0.5) / 30
  if (Math.abs(v.currentTime - t) < 1e-3 && v.readyState >= 2) return
  await new Promise((resolve) => {
    const done = () => { clearTimeout(timer); resolve() }
    const timer = setTimeout(done, 1500)
    v.addEventListener("seeked", () => (v.requestVideoFrameCallback && !v.hidden ? v.requestVideoFrameCallback(done) : done()), { once: true })
    v.currentTime = t
  })
}

function nearestRef(f) {
  const refs = state.refs[state.seq.name][state.refset]
  if (!refs.length) return null
  let best = refs[0]
  for (const r of refs) if (Math.abs(r - f) < Math.abs(best - f)) best = r
  return best
}

async function draw(playing = false) {
  if (!state.seq) return
  let f = state.f
  if (!playing && $("snap").checked) {
    const r = nearestRef(f)
    if (r != null) f = state.f = r
    $("frame").value = f
  }
  $("flabel").textContent = `${f} / ${state.seq.length - 1}  (${(f / 30).toFixed(2)} s)`
  renderAt(state.seq.name, f)
  const video = useVideo(playing)
  if (video !== state.video) { state.video = video; layout() }
  const ref = video ? f : nearestRef(f)
  const img = $("ref")
  if (video) {
    if (!playing) await seekVideo(f)
  } else if (ref != null) {
    $("refv").pause()
    const src = `${DATA}/${state.refset}/${state.seq.name}_${String(ref).padStart(4, "0")}.png`
    if (!img.src.endsWith(src)) { img.src = src; await img.decode().catch(() => {}) }
  }
  if (state.mode === "diff" || !playing) score(ref === f, video)
  const miss = state.renderer.missingReport()
  $("missing").textContent = miss.length ? miss.slice(0, 60).map(([n, c]) => `${n}  (${c})`).join("\n") : "none"
  const errs = state.renderer.errors
  if (errs.length) $("errors").textContent = [...new Set(errs)].slice(0, 40).join("\n")
  status(`${state.seq.name} frame ${f}: ${state.seq.frames[f].cam ? "camera ok" : "no camera"}`)
}

function renderAt(seqName, f) {
  const seq = state.sequences[seqName]
  const out = state.ctx.getCurrentTexture()
  const post = !state.renderer.debug.noPost && seq.frames.some((x) => x.post?.lut) && state.renderer.data.variants._post?.final?.[0]
  state.renderer.render(seq, f, post ? out.createView({ format: `${state.format}-srgb` }) : out.createView(), post ? `${state.format}-srgb` : state.format)
}

// the element showing the Unity side: the video or the reference image
const refEl = () => (state.video ? $("refv") : $("ref"))

// mean absolute difference per channel, and a heatmap (black = same, bright = different)
function score(exact, video = false) {
  const img = refEl()
  if (video ? img.readyState < 2 : !img.complete || !img.naturalWidth) { $("score").textContent = ""; return }
  const W = 400, H = 225
  const a = document.createElement("canvas"); a.width = W; a.height = H
  const b = document.createElement("canvas"); b.width = W; b.height = H
  const ca = a.getContext("2d", { willReadFrequently: true }), cb = b.getContext("2d", { willReadFrequently: true })
  ca.drawImage($("gpu"), 0, 0, W, H); cb.drawImage(img, 0, 0, W, H)
  const da = ca.getImageData(0, 0, W, H).data, db = cb.getImageData(0, 0, W, H).data
  let sum = 0
  for (let i = 0; i < da.length; i += 4) sum += Math.abs(da[i] - db[i]) + Math.abs(da[i + 1] - db[i + 1]) + Math.abs(da[i + 2] - db[i + 2])
  const mean = sum / (W * H * 3)
  const cls = mean < 4 ? "ok" : mean < 12 ? "warn" : "bad"
  $("score").innerHTML = `difference <b class="${cls}">${mean.toFixed(1)}</b> / 255${video ? " (Unity video frame, lossy)" : exact ? "" : " (nearest reference)"}`
  if (state.mode === "diff") {
    const dc = $("diff"), cx = dc.getContext("2d")
    const full = document.createElement("canvas"); full.width = dc.width; full.height = dc.height
    const fa = full.getContext("2d"); fa.drawImage($("gpu"), 0, 0, dc.width, dc.height)
    const ga = fa.getImageData(0, 0, dc.width, dc.height)
    fa.drawImage(img, 0, 0, dc.width, dc.height)
    const gb = fa.getImageData(0, 0, dc.width, dc.height)
    const out = cx.createImageData(dc.width, dc.height)
    for (let i = 0; i < out.data.length; i += 4) {
      const d = (Math.abs(ga.data[i] - gb.data[i]) + Math.abs(ga.data[i + 1] - gb.data[i + 1]) + Math.abs(ga.data[i + 2] - gb.data[i + 2])) / 3
      const v = Math.min(255, d * 4)
      out.data[i] = v; out.data[i + 1] = v * 0.6; out.data[i + 2] = 255 - v > 200 ? 0 : 0; out.data[i + 3] = 255
    }
    cx.putImageData(out, 0, 0)
  }
}

function swipe(x) {
  x = Math.max(0, Math.min(1, x))
  $("ref").style.clipPath = $("refv").style.clipPath = `inset(0 0 0 ${x * 100}%)`
  $("divider").style.left = `${x * 100}%`
}

function layout() {
  const m = state.mode
  const stage = $("stage")
  stage.className = m === "side" ? "side" : ""
  $("gpu").hidden = m === "unity" || m === "diff" || (m === "blink" && state.blinkUnity)
  const unityHidden = m === "web" || m === "diff" || (m === "blink" && !state.blinkUnity)
  $("ref").hidden = unityHidden || !!state.video
  $("refv").hidden = unityHidden || !state.video
  $("diff").hidden = m !== "diff"
  $("divider").hidden = m !== "swipe"
  $("ref").style.clipPath = $("refv").style.clipPath = ""
  if (m === "swipe") swipe(0.5)
  const unity = state.video ? "Unity (video)" : "Unity"
  $("tagL").textContent = m === "blink" ? (state.blinkUnity ? unity : "WebGPU") : m === "unity" ? unity : m === "diff" ? "Difference" : "WebGPU"
  $("tagR").textContent = unity
  $("tagR").hidden = !(m === "side" || m === "swipe")
}

main().catch((e) => { status(`failed: ${e.message}`); report(e.stack) })
