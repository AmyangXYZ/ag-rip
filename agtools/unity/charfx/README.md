# charfx: character rendering ports

These files make an Aether Gazer character (`SimPipeline/Character/Debug` and `SimPipeline/Character/Eye`) receive the inputs that the game gives it in the home/DLC scenes. Before this, the character looked flat and grey in the Unity reference project because none of the per-character inputs were set.

Everything is ported from the game's own code:

- the Ghidra decompile of `GameAssembly.dll`: `AG_cache/re/decomp_charfx/*.c` (new, `CharacterEffect`) and `AG_cache/re/decomp_all/*.c`;
- the ILSpy decompile of the hot-update DLL `Battle.Simulator.Render`: `AG_cache/re/hotfix/BattleSimulatorRender_src`.

Every constant comes from that code or from the prefab or scene data.

## Files

| File | Replaces | What it does | Game source |
| --- | --- | --- | --- |
| `P08.RenderPipeline/CharacterEffect.cs` | stub `Assets/Scripts/P08.RenderPipeline/CharacterEffect.cs` (keep its `.meta`/guid) | Keeps one `MaterialPropertyBlock` shared by all of the character's renderers (see below). Also sets `renderingLayerMask = 0x40000001` and turns the UI light (`UseUILightPosition`). | `CharacterEffect.Awake / OnEnable / OnDisable / LateUpdate / UpdateMaterials / UseUILightPosition`, `CharacterEffect.ShaderIds`, `CharacterEffectShaderIds`, `DitherEffect`, `InterferenceEffect`, `EnvironmentEffect`, `EnvironmentEffectUtil`, `EyeEffect` |
| `Battle.Simulator.Render/CharacterRenderController.cs` | stub `Assets/Scripts/Battle.Simulator.Render/CharacterRenderController.cs` | Switches renderers on and off per category. It writes no shader state. | ILSpy, verbatim |
| `AGTools/AGSimCharacter.cs` | new, goes next to `AGSimPipeline.cs` | Static `Camera.onPreCull` hook that sets the pipeline's character-only globals and draws the character passes (see below). | `LightingFeatures.SetupTowardMatrix / SetupShadows / GetWorldToShadow / SetupRenderFeature`, `DrawShadowPass.Execute`, `CharacterGlobalParams.Execute`, `CharacterFeature.Create / AddRenderPasses`, `OverrideEffectPass` + `OverriderEffectSystem.OverriderEffectRendering`, `CharacterSceneEnvironment` |

`CharacterEffect.cs` writes these values into the shared property block:

- `_World2Face` = face transform `worldToLocalMatrix`, updated every frame it changes.
- `_LocalLightDir` and `_LocalLightColor`, from `_localLightInclination`, `_localLightAzimuth`, `_localLightIntensity` and `_localLightColor`.
- `_UseFaceReceiveShadow` and `_DisableFaceSDFShadow`.
- `_ExtralEmissionColor`.
- `_Fill*` and `_Dissolve*`.
- `_Rimlight*`, but only when a setter changes them. Otherwise the material values are used, as in the game.
- `_DitherAlpha`.
- `_Noise`, `_GeometryOutlineColor` and `_SimTimeScale`.
- Per-renderer ambient SH, both `unity_SH*` and `_Replica_SH*`, from `_environmentEffect`.

`AGSimCharacter.cs` does the following on every camera:

- Sets `sim_TowardMatrix`.
- Sets `_OutlineMaxOffsetMultiplier`.
- Draws the character shadow map when the scene has a SimMainLight with `_castCharacterShadow` and `SceneSetting._supportDirectionalLight` is set. It then sets the `sim_CharacterShadow*` globals and keyword `SIM_DIRECTIONAL_SHADOW`.
- Adds an `AfterForwardOpaque` command buffer that draws, in order:
  1. `CharHairShadow`
  2. `CharFaceShadow`
  3. `CharHairTransE`
  4. the eye `Override1`, `Override2` and `Override3` passes.

## Install (into a generated project, e.g. `AG_dlc_play/<skin>/unity/ExportedProject`)

1. **CharacterEffect**: copy `P08.RenderPipeline/CharacterEffect.cs` over `Assets/Scripts/P08.RenderPipeline/CharacterEffect.cs`. Keep the existing `.meta`, which holds the stub's guid, so the prefab's serialized component stays bound.
2. **CharacterRenderController**: copy `Battle.Simulator.Render/CharacterRenderController.cs` over the stub in `Assets/Scripts/Battle.Simulator.Render/` in the same way.
3. **AGSimCharacter**: copy `AGTools/AGSimCharacter.cs` to `Assets/AGTools/`. It compiles into Assembly-CSharp, next to `AGSimPipeline.cs` and `AGVolumes.cs`.
4. **Automated install**: `agtools/dlc_play.py` `install_ports` only walks `("P08.Timeline", "P08.Main")` under `agtools/unity/dlc`. Two changes are needed:
   - Add `"P08.RenderPipeline"` and `"Battle.Simulator.Render"` to that tuple, with `charfx` as a second source root, and copy `charfx/AGTools/*` along with the dlc `AGTools`.
   - Add `"AGSimCharacter.cs"` to `PIPELINE_SOURCES` (read from `charfx/AGTools`) so `AGDlcRecord` also records `sim_TowardMatrix` and the `sim_Character*` globals.

The asmdefs need nothing extra. `CharacterEffect` and `CharacterRenderController` only use UnityEngine. `AGSimCharacter` uses `CharacterEffect` (the P08.RenderPipeline asmdef is auto-referenced by Assembly-CSharp) and `AGVolumes`. Neither needs Cinemachine or Timeline.

All three files compile against the Unity 6000.6.1f1 reference DLLs with no warnings. They use `FindObjectsByType<T>()` without a sort mode and no `GetInstanceID`.

`AGSimCharacter` hooks itself via `[RuntimeInitializeOnLoadMethod]` and `[InitializeOnLoadMethod]`. A batch capture that renders without entering Play mode should call `AGSimCharacter.Refresh()` after loading, next to `AGSimPipeline.Refresh()`.

## Values for 107402 (`107402ui_custom` in X306a)

**CharacterEffect data:**

- Local light: inclination 24, azimuth -28, intensity 1, white. This gives `_LocalLightDir = (-0.4289, 0.4067, 0.8066, 0)` in `sim_TowardMatrix` space: right, up, toward the camera on the horizontal plane. This light, not a scene light, is the character's key light when `SIM_DIRECTIONAL_SHADOW` is off.
- Face transform: `point_light_dir`.
- Environment colours: 0.8 / 0.5 / 0.3.

**X306a SceneSetting:**

- `_supportDirectionalLight` is 0, so there is no character shadow map and `SIM_DIRECTIONAL_SHADOW` stays off.
- `_groundShadowEnable` is 0.
- `_receiveShadow` is 0, so `_ReceiveSceneShadow` is 0.
- `_probeLightingScale` is 0. The character's ambient/light colour is therefore `_ProbeLightingBase = (0.615, 0.633, 0.745)`, and the SH only matters when scaled.

**Scene and prefab:** no SimMainLight, no CharacterSceneEnvironment and no CharacterPointLightController. The `_custom` prefab has no Light, so `UseUILightPosition` marks the model as a battle model and does nothing.

## Not ported (inactive in the default home/DLC look)

- **Effects:** CharacterEffectOverrider, fur, ghost, slice, image/SeperateRT, OutlineRT and selected outline.
- **Ground shadow:** GroundShadowSystem, used only when `SceneSetting._groundShadowEnable` is set.
- **CharacterPointLight\*:** it feeds `CHARACTER_ADDITIONAL_LIGHT`, which only the PBR character shaders compile.
- **BokehDepthOfField character mask:** a post-processing effect.
