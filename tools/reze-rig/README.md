# reze-rig `fbx2vmd` (vendored)

`fbx2vmd.mjs` is a build of **[reze-rig](https://github.com/AmyangXYZ/reze-rig)**'s
`scripts/fbx2vmd.ts`, the FBX → MMD VMD retargeter, by Amyang (MIT; see `LICENSE`).
All of the conversion logic is reze-rig's. It is vendored here only so ag-rip can turn
its exports into VMD on its own, with nothing but Node.js.

| | |
| --- | --- |
| Source | https://github.com/AmyangXYZ/reze-rig, `scripts/fbx2vmd.ts` + `lib/` |
| Commit | `d3ed8332c9fc0243017f01524e4c5a6783cd62ef` ("Hold one whole-degree fov per shot, so a zoom doesn't jitter") |
| Bundled with it | `reze-engine` 0.55.2 (VMD writer, PMX loader) |
| License | MIT, © 2026 Amyang (`LICENSE`, copied from reze-rig) |

What it does (from reze-rig): retargets a character FBX onto an MMD model's skeleton,
carries the FBX's blend-shape animation into the VMD as MMD morphs (names mapped for the
target model, eyes fitted), and turns a camera FBX into a camera VMD sized by the
character it films. Documentation and changes live in reze-rig; improve it there, then
rebuild this file.

## Use

`python ag.py vmd <skin> --target-pmx <model.pmx>` (`agtools/to_vmd.py`) converts every
sequence in `AG_fbx_anim/<cid>/cameras/` (`.character.fbx` + `.camera.fbx`) into
`AG_vmd/<cid>/cameras/` and copies the sequence's `.wav` beside the VMDs. Directly:

```
node tools/reze-rig/fbx2vmd.mjs <x>.character.fbx <x>.camera.fbx --out <dir> \
     --target-pmx <model.pmx> --no-bind-ref
```

`--no-bind-ref`: ag-rip's FBX already carry the true rest pose (a rest key written by
`export_anim_fbx`), so anchoring to reze-rig's bundled `Idle.fbx` changes nothing - the
output is byte-identical - and that file is not vendored.

Not vendored: target models. The 托特 PMX that reze-rig's morph map is built for ships
with its author's terms (请勿二次配布 - no redistribution; copyright 深空之眼), so point
`--target-pmx` at your own copy (e.g. a reze-rig checkout's `public/models/托特/托特.pmx`).

## Rebuild (after changing reze-rig)

```
cd <reze-rig> && npm i
npx esbuild scripts/fbx2vmd.ts --bundle --platform=node --format=esm \
    --outfile=<ag-rip>/tools/reze-rig/fbx2vmd.mjs
```

then update the commit line above.
