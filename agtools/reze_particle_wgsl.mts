// The WGSL of the game's particle systems, from reze-design's own generator.
//
//     tsx agtools/reze_particle_wgsl.mts <reze-design>/lib/unity-particles.ts < in.json > out.json
//
// in.json: [{ "cls": ParticleClass, "world": number }, ...]  (agtools/dlc_particles.py writes it)
// out.json: [wgsl, ...] in the same order.
//
// The converter does not reimplement particleEffectWgsl: it runs the app's file
// read-only, so the effect source in a scene zip is byte for byte what the app
// would generate for that class (and what lib/effect-textures.ts keys its
// pictures by).
import { pathToFileURL } from "node:url"

const lib = process.argv[2]
if (!lib) {
  console.error("usage: tsx reze_particle_wgsl.mts <reze-design>/lib/unity-particles.ts < in.json")
  process.exit(2)
}
const { particleEffectWgsl } = await import(pathToFileURL(lib).href)
let text = ""
process.stdin.setEncoding("utf8")
for await (const chunk of process.stdin) text += chunk
const items = JSON.parse(text) as { cls: unknown; world: number }[]
process.stdout.write(JSON.stringify(items.map((i) => particleEffectWgsl(i.cls, i.world))))
