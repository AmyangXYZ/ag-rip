# AetherGazer animation extraction

Pulls characters out of the game's asset bundles and produces **one animated FBX per
clip**, rigged and textured, ready to retarget (e.g. to MMD VMD via reze-rig).

```
python ag.py list                # who's in there
python ag.py find 1095           # what clips they have
python ag.py build 1095          # -> AG_fbx_anim/1095/1095@stand.fbx, @run.fbx, ...
python ag.py audit 1095          # prove nothing was missed
```

`ag.py --help` lists every command. Each tool in `agtools/` also runs standalone.

## Doing a new character

`build` is the whole thing — rig, prefab and every clip, in dependency order:

```
python ag.py build 1170
```

Budget roughly 1–2 min for the rig, ~1 min for the prefab, and a few seconds per
clip. Then check the log:

- `basis fit: … residual p90 <1e-3` on every set — the rig and the clips agree.
- `+root@hip` on each clip — body motion landed on the hip where retargeters read it.
- a `N bone(s) differ from the prefab` line naming a handful of `*_Fix` helpers is
  normal; a set being *refused* is not.

Then `python ag.py audit <id>` to confirm nothing outside the hero tree was missed.
Clips reported as belonging to partner characters are theirs, not a gap.

### Where things stand

Built: **1095** (395), **1076** (223), **1022** (198), **1034** (168), **1083** (101),
**1017** (55), **6089** (16), **6046** (15). `AG_fbx`/`AG_sample` also hold rigs and
prefabs for 1020 and 1170, so those two only need `ag.py anim`.

**1075 is blocked by a Blender bug.** Every full-body rig AssetStudioMod writes for it
crashes Blender's FBX importer with `KeyError: root` at
`mesh.armature_setup[self]` — a mesh skinned across two armatures, which the importer
cannot resolve. Blender 4.1 and 5.2 both fail, some option combinations crash the
process outright, and a clean re-export changes nothing. The file itself is fine:
reze-rig's own parser reads all 155 models, matching the prefab exactly. The fallback,
if this character is wanted, is to build the armature from the prefab (which
`unity_yaml.parse_prefab` already returns in full) instead of importing the rig FBX;
the result would be skeleton-only, which is all the VMD path needs.

`AG_vmd/` is a by-product — regenerate it with reze-rig's `fbx2vmd`, not this repo.

## Reproducing from a fresh game install

Everything needed lives in this folder — `ag.py` plus `agtools/`. Back up those and
nothing else; all outputs regenerate from the game.

```
python ag.py restore      # un-hash and de-pad the bundle cache   (~51 GB, ~1 h)
python ag.py rip-anims    # rip every AnimationClip               (hours, ~26 GB)
python ag.py build <id>   # per character from then on
```

The first two only need redoing when the game updates.

**`restore`** is the step without which nothing else works. The game stores assets as
`<h0>/<h1>/<md5>.ys` with the real paths in `AssetHash_Info.bytes`, and pads every
Unity bundle with 1–16 leading NUL bytes so AssetRipper and AssetStudio refuse to
recognise them. This un-hashes and strips the padding into `Windows_restored/`.

**`rip-anims`** drives a headless AssetRipper over the restored bundles in ~500 MB
chunks — pointed at everything at once it exhausts memory — keeping only `.anim` and
`.controller`. Progress is recorded per chunk in `AG_animations/_rip_results.json`,
so an interrupted run resumes instead of starting over.

> **Known gap: the clip library is incomplete.** Only 4 of 20 `comeffect` chunks have
> been ripped (those 4 produced 4,996 clips); **7.7 GiB is still unripped**, along with
> a little of `comscene`. `comeffect` is where team-up ultimates and costume DLC poses
> live, so more body animation is probably in there. Finish it with
> `python ag.py rip-anims --only comeffect` (hours), then `ag.py find --reindex`.

## The flow

```
StreamingAssets/Windows_restored/comchar/.../hero/<id>.ys
   |
   |-- AssetStudioModCLI --> AG_fbx/<id>/          rigged, textured T-pose FBX
   |-- AssetRipper -------> AG_sample/<id>/        prefab = the real bind pose
   `-- AssetRipper -------> AG_animations/         clips as Unity YAML (.anim)
                                  |
                     export_anim_fbx.py binds them together
                                  |
                                  v
                       AG_fbx_anim/<id>/<model>@<clip>.fbx
```

Neither tool can do this alone. AssetStudioMod exports the rig beautifully but cannot
bind this game's clips — `-m animator --fbx-animation all` yields FBX with zero
armatures, zero meshes, zero actions. AssetRipper recovers every clip as readable
generic YAML (uncompressed, with real transform paths) but cannot write FBX. This
repo is the join.

## Commands

| | |
| --- | --- |
| `ag.py list` | every character with clip counts |
| `ag.py find <id>` | clips for one character; `--grep`, `--costume`, `--paths`, `--json` |
| `ag.py audit <id>` | walk the whole tree and prove nothing was missed |
| `ag.py build <id>` | rig + prefab + every clip, in dependency order |
| `ag.py rig <id>` | rigged FBX only → `AG_fbx/` |
| `ag.py prefab <id>` | bind pose only → `AG_sample/` |
| `ag.py anim <id>` | clips → FBX → `AG_fbx_anim/`; `--dry-run` reports fit and coverage |
| `ag.py names` | build the ID → name contact sheet |
| `ag.py rename <map>` | relabel the extracted tree from a name mapping |

Options after the ID are forwarded, so `ag.py anim 1095 --costume 109500 --clips stand`
works, as does `ag.py build 1095 --dry-run`.

## Clip layout

Most clips live under `AG_animations/ComChar/ArtResources/Char/Hero/<id>/<id><costume><suffix>/`
— suffix `""` is the battle model, `ui` the menu model, `display` the showcase poses.
1095 has 150 there, across 4 costumes × 3 sets.

**But not all of them.** Two other roots hold real body animation and are scanned too
(`EXTRA_ROOTS` in `agtools/clip_sets.py`):

- `ComEffect/.../EffectChar/Hero/<id>/…` — costume DLC poses
- `ComEffect/.../CooperateUniqueSkill/<a>_<b>/<id><costume>/` — team-up ultimates,
  one subfolder per participant. Only the subfolder whose *own* name is this
  character's 6-digit costume ID is theirs; the rest animate the partner's rig.

For 1095 that's 29 more clips, so 179 in total.

**Clips from outside the model's bundle name their bones by hash.** Unity stores an
animation binding's target as a CRC32 of the transform path, and AssetRipper can only
turn that back into a name when the matching hierarchy was loaded with the clip. The
home-screen interaction sets are not, so nearly every curve arrives called
`path_0x1136AEEC_...` and binds to nothing — an idle exported with **6 usable bones**
(root through the spine to the head, which is exactly what "only head and root move"
looks like) instead of a full body. The hash is plain CRC32, so
`unity_yaml.resolve_hashed_paths()` inverts it against the rig's own paths: **6 bones
→ 60**. The export log reports `[N hashed paths resolved]`. Anything still hashed is
VFX/particle nodes with no bone counterpart, and `d_<id>_Bone*` hair/cloth bones stay
unanimated in these clips by design — the home screen drives them with a physics solver
rather than keyframes.

**Classify clips individually, never by sampling the folder.** These folders mix real
body animation (`combo_skill`, `Take 001`) with timeline and material curves that drive
no skeleton at all (`Recorded (12)`), and which one sorts first is luck. Sampling the
first clip wrote off 12 whole folders. `clip_sets.is_skeletal()` peeks each file for
`Bip001`.

**Sets that share a model stem must be prefixed or they silently overwrite.** Two
independent cases, both of which cost real clips before they were caught:

- *Extra sets*: three team-up pairings each ship a `combo_skill` for the same costume,
  and `109502act`/`109502dlc` both hold `action1_1`. They take the prefix of the folder
  they came from — `1095@1095_1111_combo_skill.fbx`, `109502@act_action1_1.fbx`.
- *Main sets*: `display`, `capture` and `dorm` all reuse the **battle** rig's stem and
  all ship `pose0..pose7`, so 1022's capture set overwrote its display set and lost 10
  clips while still reporting "198 exported". Those suffixes now prefix too:
  `1022@display_pose0.fbx` vs `1022@capture_pose0.fbx`. (`ui` is safe — its stem
  already differs.)

Export count and file count must agree; if they don't, something collided.

Anything else in the tree carrying the ID is genuinely not skeletal: `Effect/Animations`
(a single VFX object), `Oath/FaceTimeline` (facial floats, zero duration), BattleUI
widgets. `ag.py audit <id>` walks the whole tree and reports any skeletal clip the
exporter doesn't cover.

## Three things that are easy to get wrong

Each of these produced output that looked plausible and was wrong; all are handled,
and the notes are here so they don't get reintroduced.

**The bind pose must be the rest pose.** Blender writes each bone node's *static*
`Lcl Rotation` from the pose at the **current scene frame**. Export while parked on a
posed frame and every clip ships a different "bind" baked from its own first frame — an
idle exports a bent-elbow bind. Any retarget expressing motion as a delta from bind then
hinges those joints backwards, limbs only, torso fine. `export_anim_fbx.py` writes a
rest key at frame −10 and parks there before exporting. Symptom if it regresses: the
bind differs between two clips of the same character.

**Body motion has to land on the hip.** The game animates the Unity `root` node
(travel) and the Biped root bone `Bip001` (body rotation, up to ~177°), leaving
`Bip001 Pelvis` completely static. Retargeters read the *hip*, and a bare Biped root is
normally unmapped, so as-authored all of it is discarded and the legs reach for a body
that never moves. Both are folded down into the hip by default; `--no-fold-root` keeps
the game's layout.

**The coordinate basis is solved, not assumed.** All 48 signed axis permutations are
scored against the prefab's rest pose versus the armature's, and the winner is used only
if the fit holds across the skeleton (1095: `C = diag(-1,1,1)`, p90 residual ~4e-6). That
doubles as a guard — a rig and clips from different skeletons blow the residual up and
the set is refused rather than silently exported wrong.

The verdict comes from the **p90** residual, not the maximum. Real rigs carry a few
mirrored helper bones whose bind frame sits exactly 180° from the prefab's: on 109503,
three `Bone_L_Thigh_Fix*` bones out of 191 were enough to veto 16 perfectly good clips
when the check used the worst bone. Outliers are now named in the log instead of
failing the set. Relatedly, ordinary bones take their fallback rest (for channels a clip
doesn't animate) from the **rig's own bind**, not the prefab — so where the two disagree,
the bone stays at the pose its skinning was authored against instead of flipping.

## Character names

The game ships **no** ID → name table anywhere in StreamingAssets, DataBase, or the
Unity persistent data; it comes from the server, and community wikis don't publish the
internal IDs. It does ship per-ID portrait art, so `ag.py names` extracts one image per
character and writes a contact sheet — read the names off it, save as `names.json`, and
`ag.py find` accepts names from then on. 87 of 88 playable (`1xxx`) characters have art;
`6xxx` IDs are NPC/boss/servant models.

## Stages

| | |
| --- | --- |
| `ag.py scenes [--grep X]` | stage index: 544 codes across `comscene`, `comsceneq`, `comeffect`, `levels` (`--index` rebuilds, ~5 min) |
| `ag.py stages [--char 1095]` | DLC skin → stage table + pictures → `AG_stage_names/` (`contact_sheet.html`) |
| `ag.py stage x343` | one stage → standalone Unity project → `AG_stages/x343/` (`comeffect/x100` picks the folder; `sourcespace` = all 7 modifier-mode spaces in one project, a scene each) |
| `ag.py cams 109501 104701` | a skin's authored camera sequences (victory pose + every DLC home-screen interaction) -> `AG_fbx_anim/<cid>/cameras/<skin>@<seq>.camera.fbx` + `.character.fbx` + `.camera.json` + previews - see "Camera sequences" |
| `agtools/stage_thumbs.py --prefix x` | render each stage (glb → Blender) → `AG_stage_names/render/` |
| `agtools/bundle_deps.py <bundle>` | a bundle's full dependency closure (`--index` rebuilds the CAB map, ~8 min) |

### The hand-off

`ag.py stage` is where this repo's part of a stage ends: a standalone Unity
project that renders like the game, with the scene, its materials, its lighting
rig and its textures in place. reze-design's `tools/stages/unity_to_glb.py`
reads that project and takes it the rest of the way, through Blender, to the
`.glb` the app loads.

Open the project in Unity to check a stage against the game before converting
it — `AGTools/` carries the pipeline, the shadow and post-FX stand-ins and a
camera, so a play-mode frame is the reference every conversion is measured
against.

- **Dependencies.** Bundles name each other only by internal `CAB-<hash>` file name.
  `bundle_deps.py` maps those to files by reading each bundle's UnityFS directory
  block (cache: `AG_cache/cab_index.json`), then follows `externals` transitively.
  A stage is its scene + `_dep` bundle *plus* that closure (x343: 260 bundles, 368 MB).
- **Skin → stage links are derived**; the game's own table is in the client Lua, which
  ships encrypted (`splash/amds/p08_tolua_dll.bytes.encrypted`). Each link says how it
  was found (`named`, `deps`, `manual`); fix or add links in `AG_stage_names/links.json`.
  `textureconfig/scenechangeui/item/<skin>` is the home-scene switcher card per DLC
  skin, `stage_s/<code>` the battle stage-select thumbnail.
- **Modifier mode** spaces (`SourceSpace` table in `config.ys`): one effect prefab per
  pantheon under `comeffect/effect/sourcespacescene/x0N` - 通用 x01, 奥山 x05, 尼罗 x03,
  真樱 x02, 圣树 x04, 众星 x06, 天垣 x07.
- **Home scenes** (the non-character home backgrounds, `AG_stage_names/home/<id>.png`):
  6000 office over the city = `x10` / `x10a`; 6100 rotunda with the spiral ramp =
  `x202` / `x202a` (both verified against the pictures). The `x20x` codes are the
  home-scene set (x203 = 6017; x201 and x204 look like 6001 and 6018, unverified).
  `comeffect/x100`, `x100_new`, `x102`, `x104` are effect arenas, not home scenes.
  Some stages (x10, x10a, x100) save their root inactive - the game enables it on
  load - so the export switches scene roots on (listed in `ag_stage.json`
  `activated_roots`).
- **Unity projects render with the game's own shaders.** `agtools/decompile_shaders.py`
  decompiles all 334 (D3D11 bytecode -> ShaderLab/HLSL, every keyword variant) into
  `AG_shaders/` with `tools/USCSandbox`, patched for this game
  (`tools/uscsandbox-aethergazer.patch`). The output is **bit-exact**: DXBC registers
  are typeless, so temps are `uint4` raw bits and every instruction reads its operands
  in its own type (`asfloat`/`asint`/`uint`); compares give `0xFFFFFFFF`/0, `log` is
  log2, integer immediates keep their bits (`AG_BITEXACT=0` gives the old value-domain
  output). `agtools/fix_cbuffers.py` turns the Forward+ bin/tile tables back into real
  cbuffers. Other patch items: 2022 blob segments, property-bound render state, custom
  cbuffers, declaration guards, texture-vs-sampler naming, shadow compare sampling,
  dynamic matrix-array indexing.
- **The pipeline stand-in is a port of the game's decompiled pipeline** (see "Reverse
  engineering" below), in `agtools/unity/`, copied into each project's `Assets/AGTools`:
  `AGSimPipeline.cs` (LightingFeatures, SetupRenderFeature, SetupCubeReflection,
  SetupEnvironmentLighting SH packing, UpdateRenderSettings fog/probe/time globals,
  Forward+ `PLUS_LIGHTING` light list + bins/tiles + LTC area-light LUT),
  `AGVolumes.cs` (SceneSetting defaults + volume-profile overrides, blended by
  priority/weight), `AGSimShadows.cs` (cascaded main-light shadows through the game's
  SHADOWCASTER passes, CascadeShadowSetting), `AGSimPostFX.cs` (the game's BloomPass
  and FinalPass: tonemapping, exposure, contrast, ACES, invert/grayness/darkness; the
  colour-grading LUT baked each frame from the volume stack, as ColorGradingLutPass),
  `AGStageCamera.cs` (stage camera / home viewpoints). Linear colour space with
  `lightsUseLinearIntensity = false`, as the game. Game components get their real
  serialized fields back (`gen_mono_scripts.py`); textures are the exact GPU data,
  HDR intact (`native_textures.py`, PNG previews in `_png_textures/`).
  Editor helpers: `AGOpenStage.cs` (opens the stage scene, forces Linear),
  `AGEditorPlayback.cs` (menu AG > Animate in Edit Mode: animators, particles and
  scrolling materials run without Play), `AGPrefabScenes.cs` (a scene per prefab
  stage), `AGLighting.cs` (baked/realtime GI off in every scene: the game ships no Unity
  lightmaps and its shaders' `LIGHTMAP_ON` variants don't compile, so an editor
  "Generate Lighting" bake turns a stage magenta), `AGManifest.cs`, `AGStageShot.cs` (headless screenshot:
  `Unity.exe -batchmode -quit -projectPath <proj> -executeMethod AGStageShot.Run -agScene <scene> -agOut <png prefix> [-agView]`).
  Not reproduced in Unity (references shipped for the port): SSAO/GTAO, volumetric
  lighting/fog, light cookies, character/regional shadows, PPR.
  TAA deliberately off.
- **Iterating.** After changing `AG_shaders/` or `agtools/unity/`, update existing
  projects without re-exporting: `python agtools/stage_unity.py x305 x202 --refresh`
  (re-installs shaders + helpers, re-runs fix-ups, reference pack and manifest). Close
  the project in Unity first: a batch run on an open project exits with code 1.
  Home scenes have no camera in the data: add a viewpoint to `VIEWPOINTS` in
  `stage_unity.py` (find one with `AGStageShot ... -agView`).
- **The render pipeline assets** are in player data, not the stages:
  `agtools/extract_pipeline.py` -> `AG_pipeline/` (53 pipeline shaders decompiled,
  16 compute shaders as DXBC + disassembly + binding/cbuffer layouts, pipeline
  materials, the HDR bloom kernel, SMAA tables, the LTC area-light LUT read out of the
  decrypted metadata, and every renderer feature with its resource wiring).
  `agtools/volume_catalog.py` -> `AG_pipeline/volume_components.json` lists every
  scene-tuning class across all 544 stages with field names and each stage's values.
- **Handoff for the port.** Each project is self-contained for re-implementing the
  renderer elsewhere: `ExportedProject/PORTING.md` (layout, how the game turns scene
  data into shader inputs, what is not reproduced and where its reference is),
  `ag_render_manifest.json` (written by `AGManifest.cs` in the batch Unity run: every
  rendering component and volume-profile component with all fields, lights, probes,
  shader/keyword census, and the exact global uniforms, arrays and keywords fed to the
  shaders; guids resolved to asset paths) and `AG_reference/` (a copy of
  `AG_pipeline/` + this stage's slice of `volume_components.json`).
  Built with Unity 6000.6.1f1 (the game is 2022.3.62f3; both import cleanly).
  Open `AG_stages/<code>/ExportedProject`, not the folder above it.

## Camera sequences

`ag.py cams <skin>` exports every camera the game authors for a skin: the victory pose
(`storytimeline/win/<skin>_win_uitpose`) and the DLC home-screen interactions
(`uitimeline/charactor/<skin>`: `debut`, `action1_1`, `touch1`, `touch2`). Each is a
Timeline prefab: a camera rig (`<x>_cam/rotation&position` + `lookat` + a Cinemachine
VirtualCamera) driven by a recorded clip, the character (bound at runtime at the timeline
root) playing excerpts of its clips, camera-cut blends and props.

- **Camera**: evaluated as Cinemachine does - rig transforms from the clip, Composer aim
  at `lookat` + `m_TrackedObjectOffset` (no damping, centred), `m_Lens.Dutch` roll,
  animated `m_Lens.FieldOfView` (vertical). Clip-driven Cinemachine fields are stored
  as CRC32-hashed script attributes and are resolved by name.
- **Character**: the timeline's clip schedule (start, clip-in, duration per excerpt) is
  merged into one Unity clip and exported by `export_anim_fbx` itself, so
  `.character.fbx` has exactly the conventions of the per-clip FBXs (rest key, facing,
  root fold) - frame 0 = timeline 0. Do not splice exported FBXs in Blender instead: the
  re-export loses the rest-key bind pose and the retargeted facing comes out wrong.
- **Camera FBX**: placed on the same rig import with a Unity->Blender mapping fitted on the
  rest joints (residual ~1e-8), written with `export_anim_fbx`'s FBX settings, keyed on
  the same frames. `.camera.json` holds the per-frame pose in Unity space for engines.
- Previews are rendered from the final FBXs re-imported fresh (what a consumer sees).
- **To VMD**: `python ag.py vmd <skin> --target-pmx <model.pmx>` writes, per sequence,
  `<seq>.character.vmd` (motion + facial/lip morphs) and `<seq>.camera.vmd` into
  `AG_dlc_scene/<skin>/`, with the `.wav` beside them. The conversion is
  **[reze-rig](https://github.com/AmyangXYZ/reze-rig)**'s `fbx2vmd` (MIT), vendored as a
  single Node bundle in `tools/reze-rig/` - see its README for the source commit, what
  it does and how to rebuild it. Target models are not included (the 托特 PMX forbids
  redistribution); pass your own.
- **Facial + lips + voice** (DLC interactions): the sequences play on the home-screen
  model (`<skin>ui_tpose`: Eye / Eyebrow / Mouth / Pupil meshes with named blend shapes).
  Facial curves come from the timeline's facial track (109501: `FacialAni*` clips; 104701:
  inside the body clips) and are decoded straight from the bundle by `unity_clip.py` -
  AssetRipper's YAML merges blend-shape curves it cannot name, losing them. Bindings are
  CRC32(renderer path) / CRC32(channel name). Lip sync is the game's own pre-analysed data
  (`crilipsexdata/<lang>.ys`, per voice cue, 30 fps A/I/U/E/O -> `Mouth_a..o`, overriding
  the facial mouth while the voice plays, as `CriLipsExPlayer` does). Written into the
  character FBX as standard blend-shape channel animation (zh lips; `--lang zh,ja` adds
  `.character.ja.fbx`). Audio is one track per sequence, `<skin>@<seq>.wav`: the voice cue
  mixed with the scene music/SFX (reze-engine plays a single audio track). `ag.py voice` decodes CRI ACB/AWB banks to WAV with
  vgmstream (`tools/vgmstream`, which knows the game's HCA key): voice from
  `Voice/<lang>/` (zh installed; ja needs the game's Japanese voice pack downloaded first),
  scene music/SFX from `ui_scene_<skin>.acb`. `agtools/cri_utf.py` reads @UTF tables.
- Shot changes authored as a sub-0.1 s whip (a few keys flinging the camera across the set,
  or a stray key at an old shot's spot - 104701 debut) are exported as clean cuts: the
  outgoing shot holds, then one jump (`held_frames` in the JSON). At 30 fps they were one or
  two frames of unrelated views.
- Not included: the 1.5 s blend from/to the game's home camera at the start/end (the
  home camera is placed by game code; the cut times are in the JSON).

## Reverse engineering the render pipeline (tools/re)

The pipeline's C# is IL2CPP-compiled into `GameAssembly.dll`; its metadata was
encrypted. Recovered offline from the installed files:

| step | tool | output |
| --- | --- | --- |
| decrypt `global-metadata.dat` (custom "CODEPHIL" container: magic `0x1357FEDA`, 256-byte key, 64-byte VM program, 64-byte blocks) by running the game's own block routine (rva `0x6e11f0`) under Unicorn | `tools/re/codephil.py metadata` | `AG_cache/re/global-metadata.dat` (IL2CPP v31) |
| names, field offsets, method addresses, string literals | `tools/Il2CppDumper` | `AG_cache/re/dump/` (`dump.cs`, `script.json`, `il2cpp.h`, `stringliteral.json`) |
| which methods use a shader property (literal xrefs) | `tools/re/xrefs.py _AcceptLightProbe ...` | method + offset list |
| pipeline types for Ghidra (2.2k structs, 3k signatures) | `tools/re/reduce_header.py` | `AG_cache/re/pipeline.h`, `signatures.txt` |
| decompile every pipeline method with names + types | Ghidra 12 headless (`tools/ghidra_12.1.3_PUBLIC`, JDK in `tools/jdk`), `tools/re/AGTypes.java` + `AGDecomp.java` | `AG_cache/re/decomp_all/*.c` (3,067 methods) |

The live pipeline is `UnityEngine.Rendering.Replica*` (URP-derived); `SceneSetting.Update`
writes scene values into `VolumeDefaultsManager` defaults (EnvironmentSetting,
CharacterEnvironmentSetting, PostProcessSetting) which volume profiles override;
`ReplicaExt.ForwardFeature.UpdateRenderSettings`, `SetupCubeReflection`, `BloomPass`,
`FinalPass`, `LightingFeatures`, `PlusLightingFeature`, `AreaLightFeature` turn them
into shader globals - `agtools/unity/AGSimPipeline.cs` follows them method by method.
Example: bloom `_Params = (0.77, 100, threshold, threshold*0.5)`, threshold not
gamma-converted. `tools/re/annotate.py` labels volume-parameter slots and static
fields in the decompiled C.
HybridCLR DLLs (`splash/amds|huds`, "CDPH" containers) use a different, sectioned
format - not decoded yet.

Re-run: `python tools/re/codephil.py metadata`, Il2CppDumper on it, then
`tools/ghidra_12.1.3_PUBLIC/support/analyzeHeadless.bat AG_cache/re/ghidra GA -import|-process ...`
(see the script headers).

## Credits

- **[reze-rig](https://github.com/AmyangXYZ/reze-rig)** (MIT, Amyang): FBX -> MMD VMD
  retargeting, blend-shape -> morph mapping and camera VMDs. `tools/reze-rig/fbx2vmd.mjs`
  is a build of its `scripts/fbx2vmd.ts`, vendored unchanged so ag-rip runs on its own.
- AssetRipper, AssetStudioMod, USCSandbox (patched), Il2CppDumper, Ghidra, vgmstream -
  third-party tools used from `tools/`, not redistributed here.

## Requirements

- **Blender 3.x-4.3** (auto-detected: newest install under Program Files or
  `~/Downloads/blender-*`, preferring a legacy-API build). **Not 4.4+** - that
  release replaced Actions with the slotted system, removing `action.fcurves` and
  `action.groups`, which this exporter writes curves through. On a 4.4+-only
  machine it fails loudly with `'Action' object has no attribute 'fcurves'`;
  install 4.1 LTS alongside, or port `build_action()` to slots/layers/channelbags.
- AssetStudioModCLI in `tools/`
- AssetRipper (path in `agtools/extract_character.py`)
- Python 3.12 for the launchers; the binding itself runs inside Blender's Python
- Stages: Unity 6000.6.1f1 (path in `agtools/stage_unity.py`); `tools/USCSandbox`
  (upstream checkout + `tools/uscsandbox-aethergazer.patch`, built with the .NET SDK
  in `tools/dotnet` into `tools/uscs`; then `python tools/uscs_builtin_names.py`)
- VMD: Node.js (runs the vendored reze-rig bundle)
- Audio: `tools/vgmstream` (vgmstream-cli release zip), Python `UnityPy`
- Reverse engineering: `tools/Il2CppDumper`, `tools/ghidra_12.1.3_PUBLIC`, JDK 21 in
  `tools/jdk`, Python `unicorn` + `capstone`

Only the scripts are in git (`.gitignore` is a whitelist): the game install, every
`AG_*` output folder and the third-party tools above stay local.

## Known gaps

- Twist bones (`Bone_L_UpArmTwist01`, `Bip001 L ForeTwist`) and the `d_<id>_Bone*`
  hair/cloth dynamics bones are animated in the source but have no MMD counterpart, so
  they go inert after retargeting.
- The rig arrives at 1/100 scale (character ≈ 0.03 Blender units), inherited from
  AssetStudioMod — the T-pose source has it too. Harmless downstream, but it means a
  default Blender camera renders black: the near clip plane swallows the model.
