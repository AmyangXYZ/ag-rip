// Writes <project>/ag_render_manifest.json: everything a re-implementation of the game's
// renderer (e.g. the WebGPU port) needs per scene, in one place:
//   - every rendering component, all serialized fields (SceneSetting, SimMainLight,
//     ReplicaAdditionalLightData, CharacterSceneEnvironment, Volume + every profile
//     component: CascadeShadowSetting, SSAOSetting, VolumetricLightingSetting,
//     ColorAdjustments, LiftGammaGain, PostProcessSetting, EnvironmentSetting, ...)
//   - every Light (type, colour, intensity, range, cone, shadows, bias, transform)
//   - reflection sources, sky/skybox inputs, renderer/material/shader/keyword census
//   - the exact global uniforms + keywords the pipeline stand-in (AGSimPipeline)
//     feeds the game shaders, i.e. the derived values (SH, fog packing, light tables)
// Batch: Unity.exe -batchmode -quit -projectPath <p> -executeMethod AGManifest.Run
// Object references come out as EditorJsonUtility writes them ({fileID, guid, type});
// stage_unity.py adds the asset path next to every guid afterwards.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class AGManifest
{
    static readonly string[] Namespaces = { "UnityEngine.Rendering", "UnityEngine.Pipelines", "Qworld" };
    static readonly string[] Classes = { "SceneSetting", "SceneSettingFogConfig", "BakeSetting", "DynamicObjectBinding",
                                         "CharacterSceneEnvironment", "LocalVolumetricFog", "LightmappedLOD",
                                         "InnerSceneSetting", "QWSceneDitherComponennt" };
    // every global the stand-in sets (AGSimPipeline / AGSimShadows), i.e. the game's
    // pipeline inputs to the stage shaders
    static readonly string[] GlobalVectors = {
        "SimMainLightDir", "SimMainLightColor", "SimMainLightColorNoInt", "SimSceneTint",
        "_MainLightPosition", "_MainLightColor",
        "_Replica_SHAr", "_Replica_SHAg", "_Replica_SHAb", "_Replica_SHBr", "_Replica_SHBg", "_Replica_SHBb", "_Replica_SHC",
        "sim_EnvCube_BoxMin", "sim_EnvCube_BoxMax", "sim_EnvCube_ProbePosition",
        "sim_FogColor", "sim_FogColor2", "sim_FogParams", "sim_FogDirectionalColor", "sim_FogDirectionalDir",
        "sim_DynFogColor", "sim_DynFogParams", "_SubtractiveShadowColor", "_ProbeLightingBase", "_ShadowScatterColor",
        "_MainLightShadowParams", "_CascadeShadowSplitSpheres0", "_CascadeShadowSplitSpheres1", "_CascadeShadowSplitSpheres2",
        "_CascadeShadowSplitSpheres3", "_CascadeShadowSplitSphereRadii", "_MainLightShadowOffset0",
        "_MainLightShadowOffset1", "_MainLightShadowmapSize", "sim_ShadowBias", "sim_ShadowLightDirection",
        "_PlusLightingParams0", "_PlusLightingParams1", "sim_Time", "_AmbientOcclusionParam" };
    static readonly string[] GlobalFloats = { "sim_EnvCubeScale", "sim_FogRotation", "sim_FogDirectionalFalloff",
        "_AcceptLightProbe", "_ProbeLightingScale", "sim_VertexAmbientScale", "_ReceiveSceneShadow",
        "_EnableShadowScatter", "_ShadowScatterRange" };
    static readonly string[] GlobalArrays = { "SimAdditionalLightPosition", "SimAdditionalLightColor",
        "SimAdditionalLightAttenuation", "SimAdditionalLightSpotDir",
        "_AdditionalLightsPosition", "_AdditionalLightsColor", "_AdditionalLightsAttenuation",
        "_AdditionalLightsSpotDir", "_AdditionalLightsExtra" };
    static readonly string[] Keywords = { "SIM_MAIN_LIGHT", "HAS_DEPTH_BUFFER", "SIM_ADDITIONAL_LIGHT", "PLUS_LIGHTING", "MAIN_LIGHT_SHADOWS",
        "SIM_REGIONAL_SHADOW", "sim_FOG_LINEAR", "sim_DYN_FOG_LINEAR", "sim_DYN_FOG_EXP", "sim_DYN_FOG_EXP_SQ", "LIGHTMAP_ON" };

    static bool Wanted(MonoBehaviour mb)
    {
        var t = mb.GetType();
        return (t.Namespace != null && Namespaces.Any(n => t.Namespace.StartsWith(n))) || Classes.Contains(t.Name);
    }

    static string Path(Transform t) => t.parent ? Path(t.parent) + "/" + t.name : t.name;

    static string Obj(UnityEngine.Object o) => o ? $"{{\"name\":{Q(o.name)},\"type\":{Q(o.GetType().Name)},\"asset\":{Q(AssetDatabase.GetAssetPath(o))}}}" : "null";
    static string Q(string s) => s == null ? "null" : "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    static string F(float x) => float.IsNaN(x) || float.IsInfinity(x) ? "null" : x.ToString("R", System.Globalization.CultureInfo.InvariantCulture);
    static string V(Vector4 v) => $"[{F(v.x)},{F(v.y)},{F(v.z)},{F(v.w)}]";
    static string V3(Vector3 v) => $"[{F(v.x)},{F(v.y)},{F(v.z)}]";
    static string C(Color c) => $"[{F(c.r)},{F(c.g)},{F(c.b)},{F(c.a)}]";

    static string Component(UnityEngine.Object c) =>
        $"{{\"class\":{Q(c.GetType().FullName)},\"object\":{Q(c is Component cc ? Path(cc.transform) : c.name)},\"fields\":{EditorJsonUtility.ToJson(c)}}}";

    // one Unity launch per project: prefab scenes (if any), then the manifest
    public static void Batch()
    {
        AGPrefabScenes.Build();
        AGLighting.Lock();
        Run();
    }

    public static void Run()
    {
        var project = Directory.GetParent(Application.dataPath).FullName;
        var scenes = AssetDatabase.FindAssets("t:Scene", new[] { "Assets" }).Select(AssetDatabase.GUIDToAssetPath)
                                  .Where(p => !p.StartsWith("Assets/Editor")).OrderBy(p => p).ToList();
        var sb = new StringBuilder();
        sb.Append("{\"generator\":\"agtools/unity/AGManifest.cs\",\"unity\":").Append(Q(Application.unityVersion))
          .Append(",\"colorSpace\":").Append(Q(PlayerSettings.colorSpace.ToString()))
          .Append(",\"pipelineReference\":\"_pipeline_reference (AG_pipeline: pipeline shaders, compute, features, volume_components.json)\"")
          .Append(",\"scenes\":[");
        for (int si = 0; si < scenes.Count; si++)
        {
            EditorSceneManager.OpenScene(scenes[si]);
            var pipe = AGSimPipeline.Ensure();
            pipe.Refresh();
            var cam = GameObject.Find("AGStageCamera (generated)")?.GetComponent<Camera>();
            if (cam) { cam.targetTexture = new RenderTexture(64, 64, 24); cam.Render(); cam.targetTexture = null; }

            if (si > 0) sb.Append(",");
            sb.Append("{\"scene\":").Append(Q(scenes[si]));

            var comps = UnityEngine.Object.FindObjectsByType<MonoBehaviour>(FindObjectsInactive.Include, FindObjectsSortMode.InstanceID).Where(m => m && Wanted(m)).ToList();
            sb.Append(",\"components\":[").Append(string.Join(",", comps.Select(Component))).Append("]");

            // volume profiles and their components (ScriptableObjects referenced by Volumes)
            var profiles = new HashSet<UnityEngine.Object>();
            foreach (var v in comps.Where(c => c.GetType().Name == "Volume"))
            {
                var f = v.GetType().GetField("sharedProfile");
                if (f?.GetValue(v) is UnityEngine.Object p && p) profiles.Add(p);
            }
            var profileJson = new List<string>();
            foreach (var p in profiles)
            {
                var comp = p.GetType().GetField("components")?.GetValue(p) as System.Collections.IEnumerable;
                var parts = new List<string>();
                if (comp != null) foreach (var o in comp) if (o is UnityEngine.Object uo && uo) parts.Add(Component(uo));
                profileJson.Add($"{{\"profile\":{Obj(p)},\"components\":[{string.Join(",", parts)}]}}");
            }
            sb.Append(",\"volumeProfiles\":[").Append(string.Join(",", profileJson)).Append("]");

            var lights = UnityEngine.Object.FindObjectsByType<Light>(FindObjectsInactive.Include, FindObjectsSortMode.InstanceID);
            sb.Append(",\"lights\":[").Append(string.Join(",", lights.Select(l =>
                $"{{\"object\":{Q(Path(l.transform))},\"active\":{(l.enabled && l.gameObject.activeInHierarchy ? "true" : "false")},\"type\":{Q(l.type.ToString())}," +
                $"\"color\":{C(l.color)},\"intensity\":{F(l.intensity)},\"range\":{F(l.range)},\"spotAngle\":{F(l.spotAngle)},\"innerSpotAngle\":{F(l.innerSpotAngle)}," +
                $"\"shadows\":{Q(l.shadows.ToString())},\"shadowStrength\":{F(l.shadowStrength)},\"shadowBias\":{F(l.shadowBias)},\"shadowNormalBias\":{F(l.shadowNormalBias)}," +
                $"\"cullingMask\":{l.cullingMask},\"renderingLayerMask\":{l.renderingLayerMask},\"cookie\":{Obj(l.cookie)}," +
                $"\"position\":{V3(l.transform.position)},\"forward\":{V3(l.transform.forward)},\"rotation\":{V(new Vector4(l.transform.rotation.x, l.transform.rotation.y, l.transform.rotation.z, l.transform.rotation.w))}}}"))).Append("]");

            var probes = UnityEngine.Object.FindObjectsByType<ReflectionProbe>(FindObjectsInactive.Include, FindObjectsSortMode.InstanceID);
            sb.Append(",\"reflectionProbes\":[").Append(string.Join(",", probes.Select(p =>
                $"{{\"object\":{Q(Path(p.transform))},\"texture\":{Obj(p.texture)},\"customBakedTexture\":{Obj(p.customBakedTexture)},\"center\":{V3(p.bounds.center)},\"size\":{V3(p.size)},\"boxProjection\":{(p.boxProjection ? "true" : "false")},\"intensity\":{F(p.intensity)}}}"))).Append("]");

            var rs = UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsInactive.Include, FindObjectsSortMode.InstanceID);
            var byShader = rs.SelectMany(r => r.sharedMaterials.Where(m => m).Select(m => m))
                             .GroupBy(m => m.shader ? m.shader.name : "null")
                             .Select(g => $"{{\"shader\":{Q(g.Key)},\"materials\":{g.Distinct().Count()},\"keywordSets\":[{string.Join(",", g.Select(m => Q(string.Join(" ", m.shaderKeywords.OrderBy(k => k)))).Distinct())}]}}");
            sb.Append($",\"renderers\":{{\"count\":{rs.Length},\"castShadows\":{rs.Count(r => r.shadowCastingMode != UnityEngine.Rendering.ShadowCastingMode.Off)},\"byShader\":[{string.Join(",", byShader)}]}}");

            var ambient = $"{{\"mode\":{Q(RenderSettings.ambientMode.ToString())},\"sky\":{C(RenderSettings.ambientSkyColor)},\"equator\":{C(RenderSettings.ambientEquatorColor)},\"ground\":{C(RenderSettings.ambientGroundColor)},\"fog\":{(RenderSettings.fog ? "true" : "false")},\"skybox\":{Obj(RenderSettings.skybox)}}}";
            sb.Append(",\"unityRenderSettings\":").Append(ambient);

            // derived shader inputs, as the stand-in computed them for this scene
            sb.Append(",\"shaderGlobals\":{");
            var g = new List<string>();
            foreach (var n in GlobalVectors) g.Add($"{Q(n)}:{V(Shader.GetGlobalVector(n))}");
            foreach (var n in GlobalFloats) g.Add($"{Q(n)}:{F(Shader.GetGlobalFloat(n))}");
            foreach (var n in GlobalArrays)
            {
                var a = Shader.GetGlobalVectorArray(n);
                int used = n.StartsWith("_AdditionalLights") ? pipe.plusLightCount : pipe.additionalLightCount;
                g.Add($"{Q(n)}:[{(a == null ? "" : string.Join(",", a.Take(Math.Max(used, 0)).Select(V)))}]");
            }
            g.Add($"\"sim_EnvCube\":{Obj(Shader.GetGlobalTexture("sim_EnvCube"))}");
            var lt = Shader.GetGlobalFloatArray("_AdditionalLightsLightTypes");
            g.Add($"\"_AdditionalLightsLightTypes\":[{(lt == null ? "" : string.Join(",", lt.Take(pipe.plusLightCount).Select(F)))}]");
            sb.Append(string.Join(",", g)).Append("}");
            sb.Append(",\"shaderKeywords\":{").Append(string.Join(",", Keywords.Select(k => $"{Q(k)}:{(Shader.IsKeywordEnabled(k) ? "true" : "false")}"))).Append("}");
            var post = cam ? cam.GetComponent<AGSimPostFX>() : null;
            if (post) sb.Append(",\"postFX\":").Append(EditorJsonUtility.ToJson(post));
            sb.Append(",\"pipelineStandIn\":").Append(EditorJsonUtility.ToJson(pipe));
            sb.Append("}");
            Debug.Log("AGManifest: " + scenes[si]);
        }
        sb.Append("]}");
        File.WriteAllText(System.IO.Path.Combine(project, "ag_render_manifest.json"), sb.ToString());
        Debug.Log("AGManifest done: " + scenes.Count + " scene(s)");
    }
}
