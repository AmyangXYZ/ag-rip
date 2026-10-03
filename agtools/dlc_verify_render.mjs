// V3 render pairs, the reze side (docs/dlc-fidelity-plan.md). Driven by agtools/dlc_verify_render.py.
//
//     node agtools/dlc_verify_render.mjs <job.json>
//
// job.json: { zip, out, frames: [clip frame, ...], isolate: bool, width, height, isoWidth,
//             url (default http://localhost:3000), puppeteer (path of puppeteer-core) }
//
// Opens reze-design (the user's dev server, in a browser of its own), resets to the default
// scene (the cast the take's motion lands on), imports the take's zip through the app's own
// Import scene input, and then plays the take OFFLINE the way lib/video-export.ts does: the
// render loop stopped, the cast paused and seeked to 0, then one engine.renderFrame(1/30) per
// clip frame with a requestAnimationFrame in between, so the app's own tick applies the
// timeline (prop visibility, keyed prop uniforms, lamps) at each frame. Particle effects
// therefore integrate from the take's start exactly as they do in playback.
//
// At each listed clip frame it captures the canvas at width x height (the base render,
// <out>/reze_<frame>.png) and, with isolate, once more per effect and per prop that is on at
// that frame with just that one switched off (renderFrame(0): nothing advances, only the
// toggle differs), at isoWidth (<out>/iso_<frame>_<kind><index>.png). It writes
// <out>/render.json: per frame the effects and props it toggled, by name.
import { createRequire } from "node:module"
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs"
import { pathToFileURL } from "node:url"
import path from "node:path"

const job = JSON.parse(readFileSync(process.argv[2], "utf8"))
const W = job.width ?? 1600, H = job.height ?? 900
const IW = job.isoWidth ?? 400, IH = Math.round((IW * H) / W)
mkdirSync(job.out, { recursive: true })
const req = createRequire(pathToFileURL(path.join(job.puppeteer, "package.json")).href)
const puppeteer = (await import(pathToFileURL(req.resolve("puppeteer-core")).href)).default
const chrome = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"].find(existsSync)
const browser = await puppeteer.launch({
  executablePath: chrome, headless: false, protocolTimeout: 3600000,
  // silent: nothing a take plays may reach the speakers
  args: ["--mute-audio", "--autoplay-policy=user-gesture-required", "--enable-unsafe-webgpu", "--window-position=-2400,0", "--window-size=1600,1000", "--no-first-run", "--no-default-browser-check"],
  defaultViewport: { width: 1580, height: 900 },
})
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const log = (...a) => console.log("[render]", ...a)
const errors = []
try {
  const page = await browser.newPage()
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 300)) })
  page.on("pageerror", (e) => errors.push(`[pageerror] ${e.message.slice(0, 300)}`))
  page.on("dialog", (d) => d.accept())
  // and muted in the page too: every media element muted, every AudioContext held suspended
  await page.evaluateOnNewDocument(() => {
    const play = HTMLMediaElement.prototype.play
    HTMLMediaElement.prototype.play = function () { this.muted = true; this.volume = 0; return play.call(this) }
    for (const name of ["AudioContext", "webkitAudioContext"]) {
      const AC = window[name]
      if (!AC) continue
      AC.prototype.resume = function () { return this.suspend() }
      window[name] = class extends AC { constructor(...a) { super(...a); this.suspend() } }
    }
  })
  await page.goto(job.url ?? "http://localhost:3000", { waitUntil: "networkidle2", timeout: 300000 })
  await page.waitForFunction(() => !!window.__reze, { timeout: 300000 })
  await sleep(6000)
  // the default scene: its model is the cast the patch's castMotion lands on (the palette
  // is typed into, so it is retried when a busy page drops the keys)
  const hasCast = () => page.evaluate(() => window.__reze.getModelNames?.().includes("reze") && !window.__reze.nativeStage).catch(() => false)
  for (let attempt = 0; attempt < 4 && !(await hasCast()); attempt++) {
    await page.bringToFront()
    await page.mouse.click(790, 450)
    await page.keyboard.press("Escape")
    await page.keyboard.down("Control"); await page.keyboard.press("k"); await page.keyboard.up("Control")
    await sleep(1500); await page.keyboard.type("Reset to default scene"); await sleep(1500); await page.keyboard.press("Enter")
    for (let i = 0; i < 60 && !(await hasCast()); i++) await sleep(2000)
  }
  if (!(await hasCast())) throw new Error("no default-scene cast to carry the take's motion")
  await sleep(6000)
  const input = await page.$('input[type=file][accept=".zip,.json,application/zip,application/json"]')
  await input.uploadFile(job.zip)
  const state = () => page.evaluate(() => {
    const e = window.__reze
    let anim = null
    for (const i of e.modelInstances.values()) {
      if (i.isStage || i.isProp) continue
      const p = i.model.getAnimationProgress()
      if (p.duration > 0) anim = p.animationName
    }
    return { stage: e.nativeStage?.name ?? null, models: e.modelInstances.size, effects: e.effects.length, anim }
  })
  let s
  for (let i = 0; i < 300; i++) {
    s = await state().catch(() => null)
    if (s && s.stage && /character/.test(s.anim ?? "")) break
    await sleep(2000)
  }
  log("imported", JSON.stringify(s))
  if (!s?.stage) throw new Error("the zip did not load a native stage")
  if (!/character/.test(s.anim ?? "")) throw new Error("the take's motion is not on the cast: the transport would not run")
  // pipelines, textures and the effects' pictures settle
  await sleep(20000)

  await page.evaluate((W, H, IW, IH, keep, poses) => {
    const e = window.__reze
    const raf = () => new Promise((r) => requestAnimationFrame(() => r()))
    const fxName = (f) => (/^\/\/ (\S.*?):/m.exec(f.wgsl) ?? [, "?"])[1]
    const cv = document.createElement("canvas")
    const ctx = cv.getContext("2d")
    const grab = (w, h) => {
      cv.width = w; cv.height = h
      ctx.clearRect(0, 0, w, h)
      ctx.drawImage(e.canvas, 0, 0, w, h)
      return cv.toDataURL("image/png")
    }
    const cast = () => [...e.modelInstances.values()].filter((i) => !i.isStage && i.model.getAnimationProgress().duration > 0).map((i) => i.model)
    let frame = 0
    // the take's own effects only: the default scene's (on the cast) are off throughout
    const ours = (f) => !keep || keep.includes(fxName(f))
    const pose = (f) => {
      if (!poses) return
      const p = poses[Math.min(f, poses.length - 1)]
      e.setCameraPose({ target: p[0], rotation: p[1], distance: 0, fov: p[2] })
    }
    window.__v = {
      start() {
        e.stopRenderLoop()
        e.setRenderSize(W, H)
        e.effects.forEach((f, i) => e.setEffectInfluence(i, ours(f) ? 1 : 0))
        pose(0)
        for (const m of cast()) { m.pause(); m.seek(0) }
        e.resetPhysics()
        e.setAudioTime(0)
        e.renderFrame(0)
        for (const m of cast()) m.play()
        frame = 0
      },
      async stepTo(f) {
        while (frame < f) {
          frame++
          pose(frame)
          e.setAudioTime(frame / 30)
          e.renderFrame(1 / 30)
          await raf() // the app's tick: the timeline at the new time
        }
      },
      // what is on now, by index: effects in their window, props drawn
      live() {
        const fx = []
        e.effects.forEach((f, i) => { if (f.weight > 0 && ours(f)) fx.push([i, fxName(f)]) })
        const props = []
        // props, and the cast too (its footprint is the user's model, not the game's
        // character, and the analysis leaves it out of the scene's difference)
        for (const inst of e.modelInstances.values()) {
          if (inst.isStage || !inst.model.visible || /particle_emitters/.test(inst.name)) continue
          props.push(inst.isProp ? inst.name : `cast:${inst.name}`)
        }
        // the stage's blended renderers (glow cards, light shafts, water sheets): its
        // opaque geometry is the stage itself and is not switched
        const st = e.nativeStage
        const stage = []
        if (st) {
          const path = new Map(st.pkg.renderers.map((r) => [r.id, r.path ?? r.id]))
          for (const id of new Set(st.transparent.map((it) => it.renderer.id))) stage.push([id, `stage:${path.get(id)}`])
        }
        return { fx, props, stage }
      },
      base() { e.renderFrame(0); return grab(W, H) },
      // one effect (by index) or one prop (by name) off for one render, then back
      without(kind, key) {
        if (kind === "stage") {
          const st = e.nativeStage
          const was = st.transparent
          st.transparent = was.filter((it) => it.renderer.id !== key)
          e.renderFrame(0)
          const url = grab(IW, IH)
          st.transparent = was
          return url
        }
        if (kind === "fx") {
          const was = e.getEffectInfluence(key)
          e.setEffectInfluence(key, 0)
          e.renderFrame(0)
          const url = grab(IW, IH)
          e.setEffectInfluence(key, was)
          return url
        }
        key = key.replace(/^cast:/, "")
        const mats = e.getModel(key).getMaterials().map((m) => m.name)
        const was = mats.map((m) => e.isMaterialVisible(key, m))
        for (const m of mats) e.setMaterialVisible(key, m, false)
        e.renderFrame(0)
        const url = grab(IW, IH)
        mats.forEach((m, j) => e.setMaterialVisible(key, m, was[j]))
        return url
      },
      baseSmall() { e.renderFrame(0); return grab(IW, IH) },
      end() {
        for (const m of cast()) m.pause()
        e.setCameraPose(null)
        e.setRenderSize(null)
        e.runRenderLoop()
      },
    }
  }, W, H, IW, IH, job.effects ?? null, job.poses ?? null)

  const save = (file, url) => writeFileSync(path.join(job.out, file), Buffer.from(url.split(",")[1], "base64"))
  const report = { zip: job.zip, frames: {} }
  await page.evaluate(() => window.__v.start())
  for (const f of [...job.frames].sort((a, b) => a - b)) {
    await page.evaluate((f) => window.__v.stepTo(f), f)
    save(`reze_${f}.png`, await page.evaluate(() => window.__v.base()))
    const live = await page.evaluate(() => window.__v.live())
    const entry = { effects: [], props: [] }
    if (job.isolate) {
      save(`iso_${f}_base.png`, await page.evaluate(() => window.__v.baseSmall()))
      for (const [i, name] of live.fx) {
        save(`iso_${f}_fx${i}.png`, await page.evaluate((i) => window.__v.without("fx", i), i))
        entry.effects.push({ key: `fx${i}`, name })
      }
      for (const [j, name] of live.props.entries()) {
        save(`iso_${f}_prop${j}.png`, await page.evaluate((n) => window.__v.without("prop", n), name))
        entry.props.push({ key: `prop${j}`, name })
      }
      for (const [j, [id, name]] of live.stage.entries()) {
        save(`iso_${f}_stage${j}.png`, await page.evaluate((id) => window.__v.without("stage", id), id))
        entry.props.push({ key: `stage${j}`, name })
      }
    } else {
      entry.effects = live.fx.map(([i, name]) => ({ key: `fx${i}`, name }))
      entry.props = live.props.map((name, j) => ({ key: `prop${j}`, name }))
    }
    report.frames[f] = entry
    log(`frame ${f}: ${entry.effects.length} effects, ${entry.props.length} props`)
  }
  await page.evaluate(() => window.__v.end())
  report.errors = [...new Set(errors)].slice(0, 40)
  writeFileSync(path.join(job.out, "render.json"), JSON.stringify(report, null, 1))
} finally {
  await browser.close()
}
