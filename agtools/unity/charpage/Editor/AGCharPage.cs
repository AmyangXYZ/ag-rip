// Character-page reference renders and look data for one skin (agtools/charpage.py):
//   Unity -batchmode -nosound -quit -projectPath <p> -executeMethod AGCharPage.Batch
// reads ag_charpage.json (written by charpage.py) and, in a fresh scene:
//   - the character-page lighting of the game's hero page (X100): its SceneSetting and
//     CharacterSceneEnvironment values, as serialized in X100.unity, on the project's own
//     (field-only) SceneSetting / CharacterSceneEnvironment components, so the pipeline
//     stand-in (AGSimPipeline, AGSimCharacter, AGSimPostFX, AGGroundShadow) reads them as it
//     reads any scene; no main light, no fog, no scene shadows
//   - the model prefab at the origin in the first frame of its controller's default state
//   - three cameras (full body, waist-up 3/4, face), each rendered 1920x1080 with the game's
//     post (tone curve + bloom) -> ref_<view>.png, without post -> ref_<view>_nopost.png
//     (clamped, sRGB) and ref_<view>_hdr.exr (linear HDR scene colour)
//   - look/look.json + look/maps/*.png|exr: every material on the character (shader,
//     keywords, properties, textures), its CharacterEffect, the renderers and meshes, the
//     property-block values the shaders receive and the pipeline globals per view
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Experimental.Rendering;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

public static class AGCharPage
{
    [Serializable] public class Field { public string comp; public string field; public string kind; public float[] v; public string s; }
    [Serializable] public class Spec
    {
        public string skin; public string prefab; public string outDir;
        public float[] background;          // sRGB clear colour
        public int width = 1920, height = 1080;
        public bool bindPose;               // diagnosis: skip the stand clip
        public Field[] fields;              // SceneSetting / CharacterSceneEnvironment / RenderSettings values
    }

    const BindingFlags F = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
    static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

    static Type FindType(string name) =>
        AppDomain.CurrentDomain.GetAssemblies().SelectMany(a => { try { return a.GetTypes(); } catch { return Type.EmptyTypes; } })
                 .FirstOrDefault(t => t.Name == name && typeof(MonoBehaviour).IsAssignableFrom(t));

    [MenuItem("AG/Character page references")]
    public static void Batch()
    {
        ShaderUtil.allowAsyncCompilation = false;
        var spec = JsonUtility.FromJson<Spec>(File.ReadAllText("ag_charpage.json"));
        Directory.CreateDirectory(spec.outDir);
        EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

        // ---- the scene's lighting components, values from X100
        var env = new GameObject("SceneSetting (X100)");
        var comps = new Dictionary<string, Component>();
        foreach (var name in new[] { "SceneSetting", "CharacterSceneEnvironment" })
        {
            var t = FindType(name);
            if (t == null) { Debug.LogError("AGCharPage: no " + name + " script in this project"); continue; }
            comps[name] = env.AddComponent(t);
        }
        foreach (var f in spec.fields) Apply(f, comps);
        RenderSettings.fog = false;
        RenderSettings.skybox = null;
        RenderSettings.sun = null;

        // ---- the model
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(spec.prefab);
        if (!prefab) { Debug.LogError("AGCharPage: no prefab " + spec.prefab); return; }
        var model = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
        model.transform.position = Vector3.zero;
        model.transform.rotation = Quaternion.identity;
        string pose = spec.bindPose ? "bind pose (requested)" : Pose(model);
        // edit-mode batch renders: skin from the bones as they are now, every render
        foreach (var smr in model.GetComponentsInChildren<SkinnedMeshRenderer>(true))
        {
            smr.forceMatrixRecalculationPerRender = true;
            smr.updateWhenOffscreen = true;
        }

        // ---- cameras (one object, moved per view); tagged main so the stand-in makes none
        var camGo = new GameObject("CharPageCamera") { tag = "MainCamera" };
        var cam = camGo.AddComponent<Camera>();
        cam.allowHDR = true;
        cam.allowMSAA = false;
        cam.clearFlags = CameraClearFlags.SolidColor;
        var bg = spec.background ?? new[] { 0.5f, 0.5f, 0.5f };
        cam.backgroundColor = new Color(bg[0], bg[1], bg[2], 1f);
        cam.nearClipPlane = 0.05f;
        cam.farClipPlane = 100f;
        cam.depthTextureMode = DepthTextureMode.Depth;
        var post = camGo.AddComponent<AGSimPostFX>();

        var pipe = AGSimPipeline.Ensure();
        Refresh(pipe);

        var views = Views(model);
        var globalsPerView = new List<string>();
        foreach (var v in views)
        {
            camGo.transform.position = v.pos;
            camGo.transform.rotation = Quaternion.LookRotation(v.target - v.pos, Vector3.up);
            cam.fieldOfView = v.fov;
            // twice: the first render lets CharacterSceneEnvironment's SH reach the characters
            // (AGSimCharacter sets it before the cull, CharacterEffect reads it on its update)
            for (int pass = 0; pass < 2; pass++)
            {
                Refresh(pipe);
                post.enabled = true;
                Shoot(cam, spec, null, RenderTextureFormat.ARGB32);
            }
            post.enabled = true;
            Shoot(cam, spec, Path.Combine(spec.outDir, $"ref_{v.name}.png"), RenderTextureFormat.ARGB32);
            globalsPerView.Add("\"" + v.name + "\":{\"camera\":" + CamJson(cam) + ",\"globals\":" + Globals() + "}");
            post.enabled = false;
            Refresh(pipe);
            Shoot(cam, spec, Path.Combine(spec.outDir, $"ref_{v.name}_nopost.png"), RenderTextureFormat.ARGB32);
            Shoot(cam, spec, Path.Combine(spec.outDir, $"ref_{v.name}_hdr.exr"), RenderTextureFormat.ARGBHalf);
            post.enabled = true;
        }

        // ---- look data
        WriteLook(spec, model, pose, comps, globalsPerView);
        Debug.Log("AGCharPage done: " + spec.outDir);
    }

    // ---------------------------------------------------------------- scene values
    static void Apply(Field f, Dictionary<string, Component> comps)
    {
        if (f.comp == "RenderSettings")
        {
            var c = f.v.Length >= 3 ? new Color(f.v[0], f.v[1], f.v[2], f.v.Length > 3 ? f.v[3] : 1f) : Color.black;
            switch (f.field)
            {
                case "m_AmbientSkyColor": RenderSettings.ambientSkyColor = c; break;
                case "m_AmbientEquatorColor": RenderSettings.ambientEquatorColor = c; break;
                case "m_AmbientGroundColor": RenderSettings.ambientGroundColor = c; break;
                case "m_SubtractiveShadowColor": RenderSettings.subtractiveShadowColor = c; break;
                case "m_AmbientIntensity": RenderSettings.ambientIntensity = f.v[0]; break;
            }
            return;
        }
        if (!comps.TryGetValue(f.comp, out var comp)) return;
        var fi = comp.GetType().GetField(f.field, F);
        if (fi == null) { Debug.LogWarning($"AGCharPage: {f.comp}.{f.field} not in this project's script"); return; }
        var t = fi.FieldType;
        object val = null;
        if (f.kind == "asset")
        {
            val = string.IsNullOrEmpty(f.s) ? null : AssetDatabase.LoadAssetAtPath(f.s, t);
            if (!string.IsNullOrEmpty(f.s) && val == null) Debug.LogError($"AGCharPage: {f.comp}.{f.field}: no {t.Name} at {f.s}");
        }
        else if (t == typeof(Color)) val = new Color(f.v[0], f.v[1], f.v[2], f.v[3]);
        else if (t == typeof(Vector4)) val = new Vector4(f.v[0], f.v[1], f.v[2], f.v[3]);
        else if (t == typeof(Vector3)) val = new Vector3(f.v[0], f.v[1], f.v[2]);
        else if (t == typeof(Vector2)) val = new Vector2(f.v[0], f.v[1]);
        else if (t == typeof(float)) val = f.v[0];
        else if (t == typeof(bool)) val = f.v[0] != 0f;
        else if (t.IsEnum) val = Enum.ToObject(t, (int)f.v[0]);
        else if (t.IsPrimitive) val = Convert.ChangeType(f.v[0], t, Inv);
        else return;   // nested classes (_splitSetting, _skyboxInputs): left at their defaults
        fi.SetValue(comp, val);
    }

    static void Refresh(AGSimPipeline pipe)
    {
        pipe.Refresh();
        AGSimCharacter.Refresh();
        var gs = Type.GetType("AGGroundShadow, Assembly-CSharp");
        gs?.GetMethod("Refresh", BindingFlags.Public | BindingFlags.Static)?.Invoke(null, null);
        foreach (var ce in Object.FindObjectsByType<CharacterEffect>(FindObjectsSortMode.None)) ce.ForceUpdate();
    }

    // ---------------------------------------------------------------- pose
    // The first frame of the default state of the model's Animator (the character page's
    // stand, e.g. displayCtrl_109503 ui_stand), sampled in edit mode.
    static string Pose(GameObject model)
    {
        foreach (var anim in model.GetComponentsInChildren<Animator>(true))
        {
            var ac = anim.runtimeAnimatorController as AnimatorController;
            if (!ac || ac.layers.Length == 0) continue;
            var sm = ac.layers[0].stateMachine;
            var clip = sm.defaultState ? sm.defaultState.motion as AnimationClip : null;
            if (!clip) continue;
            AnimationMode.StartAnimationMode();
            AnimationMode.BeginSampling();
            AnimationMode.SampleAnimationClip(anim.gameObject, clip, 0f);
            AnimationMode.EndSampling();
            string p = $"{ac.name} / {sm.defaultState.name} = {AssetDatabase.GetAssetPath(clip)} @ 0s";
            Debug.Log("AGCharPage pose: " + p);
            return p;
        }
        Debug.LogWarning("AGCharPage: no animator controller with a default clip - bind pose");
        return "bind pose (no controller)";
    }

    // ---------------------------------------------------------------- views
    struct View { public string name; public Vector3 pos, target; public float fov; }

    static List<View> Views(GameObject model)
    {
        var rs = model.GetComponentsInChildren<Renderer>().Where(r => r.enabled && r.gameObject.activeInHierarchy && !(r is ParticleSystemRenderer)).ToList();
        var b = rs[0].bounds;
        foreach (var r in rs) b.Encapsulate(r.bounds);
        Transform head = model.GetComponentsInChildren<Transform>(true)
            .Where(t => t.name.EndsWith("Head", StringComparison.OrdinalIgnoreCase) || t.name.EndsWith(" Head"))
            .OrderBy(t => t.name.Length).FirstOrDefault();
        // the figure from its skeleton (weapons and sashes reach past it): head to feet,
        // centred on the pelvis
        Transform Bone(string suffix) => model.GetComponentsInChildren<Transform>(true)
            .Where(x => x.name.EndsWith(suffix, StringComparison.OrdinalIgnoreCase)).OrderBy(x => x.name.Length).FirstOrDefault();
        var pelvis = Bone(" Pelvis");
        var feet = new[] { Bone(" L Foot"), Bone(" R Foot") }.Where(x => x).ToArray();
        float top = head ? head.position.y + 0.22f : b.max.y;
        float bottom = feet.Length > 0 ? Mathf.Min(feet.Min(x => x.position.y) - 0.12f, 0f) : b.min.y;
        float H = Mathf.Max(top - bottom, 0.5f);
        // facing: the model looks along its root forward (+Z for the game's characters)
        Vector3 fwd = model.transform.forward;
        Vector3 p0 = pelvis ? pelvis.position : b.center;
        Vector3 c = new Vector3(p0.x, bottom, p0.z);
        const float fov = 30f;
        float t = Mathf.Tan(fov * 0.5f * Mathf.Deg2Rad);
        var list = new List<View>();
        // full body, portrait framing centred on the figure, camera at chest height
        {
            float half = H * 0.54f;
            var target = c + Vector3.up * H * 0.5f;
            list.Add(new View { name = "full", fov = fov, target = target, pos = target + fwd * (half / t) + Vector3.up * H * 0.05f });
        }
        // waist up, 3/4 from the character's right
        {
            var dir = Quaternion.Euler(0f, -35f, 0f) * fwd;
            float half = H * 0.24f;
            var target = c + Vector3.up * H * 0.78f;
            list.Add(new View { name = "waist34", fov = fov, target = target, pos = target + dir * (half / t) + Vector3.up * H * 0.02f });
        }
        // face close-up, slightly turned
        {
            var hp = head ? head.position + Vector3.up * 0.06f : c + Vector3.up * H * 0.93f;
            var dir = Quaternion.Euler(0f, -15f, 0f) * fwd;
            float half = Mathf.Max(0.17f, H * 0.085f);
            list.Add(new View { name = "face", fov = fov, target = hp, pos = hp + dir * (half / t) });
        }
        Debug.Log($"AGCharPage: height {H:0.000} head {(head ? head.name : "-")} bounds {b}");
        return list;
    }

    static void Shoot(Camera cam, Spec spec, string path, RenderTextureFormat fmt)
    {
        int w = spec.width, h = spec.height;
        bool hdr = fmt == RenderTextureFormat.ARGBHalf;
        var rt = new RenderTexture(w, h, 24, fmt, hdr ? RenderTextureReadWrite.Linear : RenderTextureReadWrite.sRGB);
        cam.targetTexture = rt;
        cam.Render();
        cam.targetTexture = null;
        if (path != null)
        {
            var prev = RenderTexture.active;
            RenderTexture.active = rt;
            // RGB only: the final pass masks alpha out (ColorMask RGB), alpha stays 0
            var tex = new Texture2D(w, h, hdr ? TextureFormat.RGBAHalf : TextureFormat.RGB24, false, hdr);
            tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
            tex.Apply();
            RenderTexture.active = prev;
            File.WriteAllBytes(path, hdr ? tex.EncodeToEXR(Texture2D.EXRFlags.CompressZIP) : tex.EncodeToPNG());
            Object.DestroyImmediate(tex);
        }
        rt.Release();
        Object.DestroyImmediate(rt);
    }

    // ---------------------------------------------------------------- look data
    static readonly string[] BlockNames = {
        "_LocalLightDir", "_LocalLightColor", "_UseFaceReceiveShadow", "_DisableFaceSDFShadow", "_World2Face",
        "_ExtralEmissionColor", "_FillOuter", "_FillInner", "_FillClamp", "_FillSoft", "_DissolveFactor", "_DissolveColor",
        "_RimlightColor", "_RimlightThreshold", "_RimlightFade", "_RimlightRange", "_RimlightDir1", "_RimlightDir2",
        "_LocalCharacterHeight", "_ZOffset_Unit", "_DitherAlpha", "_Noise", "_GeometryOutlineColor", "_SimTimeScale",
        "unity_SHAr", "unity_SHAg", "unity_SHAb", "unity_SHBr", "unity_SHBg", "unity_SHBb", "unity_SHC",
        "_Replica_SHAr", "_Replica_SHAg", "_Replica_SHAb", "_Replica_SHBr", "_Replica_SHBg", "_Replica_SHBb", "_Replica_SHC",
        "sim_EnvCube", "unity_RenderingLayer" };

    static readonly Dictionary<Texture, string> _tex = new Dictionary<Texture, string>();
    static readonly List<string> _texJson = new List<string>();
    static string _mapsDir;

    static void WriteLook(Spec spec, GameObject model, string pose, Dictionary<string, Component> comps, List<string> globalsPerView)
    {
        string lookDir = Path.Combine(spec.outDir, "look");
        _mapsDir = Path.Combine(lookDir, "maps");
        Directory.CreateDirectory(_mapsDir);
        _tex.Clear(); _texJson.Clear();
        var mats = new Dictionary<Material, string>();
        var matJson = new List<string>();
        var rendJson = new List<string>();
        var shaders = new HashSet<Shader>();
        var mpb = new MaterialPropertyBlock();
        foreach (var r in model.GetComponentsInChildren<Renderer>(true))
        {
            if (r is ParticleSystemRenderer) continue;
            var ms = r.sharedMaterials;
            var ids = new List<string>();
            foreach (var m in ms)
            {
                if (!m) { ids.Add("null"); continue; }
                if (!mats.TryGetValue(m, out var id))
                {
                    id = "mat" + mats.Count;
                    mats[m] = id;
                    matJson.Add(MaterialJson(m, id));
                    shaders.Add(m.shader);
                }
                ids.Add("\"" + id + "\"");
            }
            Mesh mesh = r is SkinnedMeshRenderer smr ? smr.sharedMesh : r.GetComponent<MeshFilter>()?.sharedMesh;
            var ce = r.GetComponentInParent<CharacterEffect>(true);
            r.GetPropertyBlock(mpb);
            rendJson.Add("{\"path\":\"" + Esc(PathOf(r.transform, model.transform)) + "\",\"type\":\"" + r.GetType().Name +
                         "\",\"enabled\":" + (r.enabled && r.gameObject.activeInHierarchy ? 1 : 0) +
                         ",\"mesh\":" + (mesh ? "{\"name\":\"" + Esc(mesh.name) + "\",\"asset\":\"" + Esc(AssetDatabase.GetAssetPath(mesh)) +
                                                "\",\"vertices\":" + mesh.vertexCount + ",\"submeshes\":" + mesh.subMeshCount + "}" : "null") +
                         ",\"materials\":[" + string.Join(",", ids) + "]" +
                         ",\"renderingLayerMask\":" + r.renderingLayerMask + ",\"shadowCasting\":\"" + r.shadowCastingMode + "\"" +
                         ",\"characterEffect\":" + (ce ? "\"" + Esc(PathOf(ce.transform, model.transform)) + "\"" : "null") +
                         ",\"block\":" + Block(mpb) + "}");
        }
        var ceJson = model.GetComponentsInChildren<CharacterEffect>(true)
            .Select(ce => "{\"path\":\"" + Esc(PathOf(ce.transform, model.transform)) + "\",\"enabled\":" + (ce.isActiveAndEnabled ? 1 : 0) +
                          ",\"face\":\"" + Esc(ce._face ? PathOf(ce._face, model.transform) : "") + "\",\"fields\":" + EditorJsonUtility.ToJson(ce) + "}");
        var shaderStatus = shaders.Select(s =>
        {
            var msgs = ShaderUtil.GetShaderMessages(s);
            return "{\"shader\":\"" + Esc(s.name) + "\",\"hasError\":" + (ShaderUtil.ShaderHasError(s) ? 1 : 0) + ",\"supported\":" + (s.isSupported ? 1 : 0) +
                   ",\"messages\":[" + string.Join(",", msgs.Select(m => "\"" + Esc($"{m.severity}: {m.message} ({m.file}:{m.line})") + "\"")) + "]}";
        });
        foreach (var s in shaders)
            foreach (var m in ShaderUtil.GetShaderMessages(s))
                Debug.Log($"AGCharPage shader {s.name}: {m.severity} {m.message} line {m.line}");
        var scene = comps.Select(kv => "\"" + kv.Key + "\":" + EditorJsonUtility.ToJson(kv.Value));
        var sb = new StringBuilder();
        sb.Append("{\"skin\":\"").Append(spec.skin).Append("\",\"prefab\":\"").Append(Esc(spec.prefab)).Append("\",\"pose\":\"").Append(Esc(pose)).Append("\"")
          .Append(",\n\"notes\":\"props: colours as the shader receives them (linear: Unity converts material colours gamma->linear in a linear project); propsAuthored: colours as authored. Textures: maps/<id>.png (8-bit, sRGB-encoded when srgb=1, else linear data) or .exr (HDR, linear); cubemaps as maps/<id>_f<face>_m<mip>.exr along Unity face order +X,-X,+Y,-Y,+Z,-Z. block: the renderer's MaterialPropertyBlock (CharacterEffect, pipeline) at render time. views: camera and pipeline globals as each reference was rendered.\"")
          .Append(",\n\"shaderStatus\":[").Append(string.Join(",\n", shaderStatus)).Append(']')
          .Append(",\n\"characterEffects\":[").Append(string.Join(",\n", ceJson)).Append(']')
          .Append(",\n\"renderers\":[").Append(string.Join(",\n", rendJson)).Append(']')
          .Append(",\n\"materials\":[").Append(string.Join(",\n", matJson)).Append(']')
          .Append(",\n\"textures\":[").Append(string.Join(",\n", _texJson)).Append(']')
          .Append(",\n\"scene\":{").Append(string.Join(",\n", scene)).Append('}')
          .Append(",\n\"renderSettings\":{\"ambientSky\":").Append(C(RenderSettings.ambientSkyColor)).Append(",\"ambientEquator\":").Append(C(RenderSettings.ambientEquatorColor))
          .Append(",\"ambientGround\":").Append(C(RenderSettings.ambientGroundColor)).Append('}')
          .Append(",\n\"globalKeywords\":[").Append(string.Join(",", Shader.enabledGlobalKeywords.Select(k => "\"" + k.name + "\""))).Append(']')
          .Append(",\n\"views\":{").Append(string.Join(",\n", globalsPerView)).Append("}}");
        File.WriteAllText(Path.Combine(lookDir, "look.json"), sb.ToString());
    }

    static string MaterialJson(Material m, string id)
    {
        var sh = m.shader;
        var sb = new StringBuilder();
        sb.Append("{\"id\":\"").Append(id).Append("\",\"name\":\"").Append(Esc(m.name)).Append("\",\"asset\":\"").Append(Esc(AssetDatabase.GetAssetPath(m)))
          .Append("\",\"shader\":\"").Append(Esc(sh.name)).Append("\",\"queue\":").Append(m.renderQueue)
          .Append(",\"keywords\":[").Append(string.Join(",", m.enabledKeywords.Select(k => "\"" + k.name + "\""))).Append(']')
          .Append(",\"passes\":[").Append(string.Join(",", Enumerable.Range(0, m.passCount).Select(i =>
              "{\"name\":\"" + Esc(m.GetPassName(i)) + "\",\"lightMode\":\"" + Esc(sh.FindPassTagValue(i, new ShaderTagId("LightMode")).name) +
              "\",\"enabled\":" + (m.GetShaderPassEnabled(m.GetPassName(i)) ? 1 : 0) + "}"))).Append(']');
        var props = new List<string>();
        var authored = new List<string>();
        for (int i = 0; i < sh.GetPropertyCount(); i++)
        {
            string name = sh.GetPropertyName(i), v;
            switch (sh.GetPropertyType(i))
            {
                case ShaderPropertyType.Color:
                    var c = m.GetColor(name);
                    v = C(QualitySettings.activeColorSpace == ColorSpace.Linear ? c.linear : c);
                    authored.Add("\"" + name + "\":" + C(c));
                    break;
                case ShaderPropertyType.Vector: v = V4(m.GetVector(name)); break;
                case ShaderPropertyType.Float:
                case ShaderPropertyType.Range: v = Fl(m.GetFloat(name)); break;
                case ShaderPropertyType.Int: v = m.GetInteger(name).ToString(); break;
                case ShaderPropertyType.Texture:
                    var t = m.GetTexture(name);
                    var s = m.GetTextureScale(name); var o = m.GetTextureOffset(name);
                    v = "{\"tex\":" + (t ? "\"" + Tex(t) + "\"" : "null") + ",\"st\":" + V4(new Vector4(s.x, s.y, o.x, o.y)) + "}";
                    break;
                default: continue;
            }
            props.Add("\"" + name + "\":" + v);
        }
        sb.Append(",\"props\":{").Append(string.Join(",", props)).Append("},\"propsAuthored\":{").Append(string.Join(",", authored)).Append("}}");
        return sb.ToString();
    }

    // every texture through a blit, so compressed and non-readable ones come out
    static string Tex(Texture t)
    {
        if (_tex.TryGetValue(t, out var id)) return id;
        id = "t" + _tex.Count;
        _tex[t] = id;
        bool srgb = GraphicsFormatUtility.IsSRGBFormat(t.graphicsFormat);
        bool hdr = GraphicsFormatUtility.IsHDRFormat(t.graphicsFormat) || t is Cubemap;
        var files = new List<string>();
        string safe = string.Concat(t.name.Select(ch => Path.GetInvalidFileNameChars().Contains(ch) ? '_' : ch));
        if (t is Cubemap cube)
        {
            var mat = new Material(Shader.Find("Hidden/AGCubeFace"));
            mat.SetTexture("_Cube", cube);
            for (int f = 0; f < 6; f++)
                for (int mip = 0; mip < cube.mipmapCount; mip++)
                {
                    int s = Mathf.Max(1, cube.width >> mip);
                    string file = $"{id}_{safe}_f{f}_m{mip}.exr";
                    mat.SetFloat("_Face", f); mat.SetFloat("_Mip", mip);
                    ReadBack(null, mat, s, s, true, false, Path.Combine(_mapsDir, file));
                    files.Add(file);
                }
            Object.DestroyImmediate(mat);
        }
        else if (t.dimension == TextureDimension.Tex2D)
        {
            string file = $"{id}_{safe}" + (hdr ? ".exr" : ".png");
            ReadBack(t, null, t.width, t.height, hdr, srgb, Path.Combine(_mapsDir, file));
            files.Add(file);
        }
        var t2 = t as Texture2D;
        _texJson.Add("{\"id\":\"" + id + "\",\"name\":\"" + Esc(t.name) + "\",\"asset\":\"" + Esc(AssetDatabase.GetAssetPath(t)) + "\",\"dimension\":\"" + t.dimension +
                     "\",\"width\":" + t.width + ",\"height\":" + t.height + ",\"format\":\"" + t.graphicsFormat + "\",\"srgb\":" + (srgb ? 1 : 0) +
                     ",\"hdr\":" + (hdr ? 1 : 0) + ",\"wrap\":\"" + t.wrapMode + "\",\"filter\":\"" + t.filterMode + "\",\"aniso\":" + t.anisoLevel +
                     ",\"mips\":" + t.mipmapCount + ",\"files\":[" + string.Join(",", files.Select(f => "\"" + Esc(f) + "\"")) + "]}");
        return id;
    }

    static void ReadBack(Texture src, Material mat, int w, int h, bool hdr, bool srgb, string path)
    {
        var rt = RenderTexture.GetTemporary(w, h, 0, hdr ? RenderTextureFormat.ARGBHalf : RenderTextureFormat.ARGB32,
                                            srgb ? RenderTextureReadWrite.sRGB : RenderTextureReadWrite.Linear);
        if (mat) Graphics.Blit(null, rt, mat); else Graphics.Blit(src, rt);
        var prev = RenderTexture.active;
        RenderTexture.active = rt;
        var read = new Texture2D(w, h, hdr ? TextureFormat.RGBAHalf : TextureFormat.RGBA32, false, !srgb);
        read.ReadPixels(new Rect(0, 0, w, h), 0, 0);
        read.Apply();
        RenderTexture.active = prev;
        File.WriteAllBytes(path, hdr ? read.EncodeToEXR(Texture2D.EXRFlags.CompressZIP) : read.EncodeToPNG());
        Object.DestroyImmediate(read);
        RenderTexture.ReleaseTemporary(rt);
    }

    static string Block(MaterialPropertyBlock b)
    {
        var parts = new List<string>();
        foreach (var name in BlockNames)
        {
            int id = Shader.PropertyToID(name);
            if (b.HasMatrix(id)) parts.Add("\"" + name + "\":" + M(b.GetMatrix(id)));
            else if (b.HasVector(id))
            {
                var v = b.GetVector(id);
                parts.Add("\"" + name + "\":" + (name == "unity_RenderingLayer" ? "[" + BitConverter.SingleToInt32Bits(v.x) + "]" : V4(v)));
            }
            else if (b.HasFloat(id)) parts.Add("\"" + name + "\":" + Fl(b.GetFloat(id)));
            else if (b.HasTexture(id)) { var t = b.GetTexture(id); if (t) parts.Add("\"" + name + "\":{\"tex\":\"" + Tex(t) + "\"}"); }
        }
        return "{" + string.Join(",", parts) + "}";
    }

    // the pipeline stand-in's globals (Resources/ag_globals.json, dlc_play.pipeline_globals)
    [Serializable] class G { public string name; public string kind; }
    [Serializable] class Gs { public G[] globals; }

    static string Globals()
    {
        var ta = Resources.Load<TextAsset>("ag_globals");
        if (!ta) return "{}";
        var parts = new List<string>();
        foreach (var g in JsonUtility.FromJson<Gs>(ta.text).globals)
        {
            string v;
            switch (g.kind)
            {
                case "Vector": case "Color": v = V4(Shader.GetGlobalVector(g.name)); break;
                case "Float": v = Fl(Shader.GetGlobalFloat(g.name)); break;
                case "Int": v = Shader.GetGlobalInt(g.name).ToString(); break;
                case "Matrix": v = M(Shader.GetGlobalMatrix(g.name)); break;
                case "Texture": var t = Shader.GetGlobalTexture(g.name); v = t ? "\"" + Esc(t.name) + "\"" : "null"; break;
                default: continue;   // arrays: not needed for the character page (no lights)
            }
            parts.Add("\"" + g.name + "\":" + v);
        }
        return "{" + string.Join(",", parts) + "}";
    }

    static string CamJson(Camera c) =>
        "{\"position\":" + V4(c.transform.position) + ",\"rotation\":" + V4(new Vector4(c.transform.rotation.x, c.transform.rotation.y, c.transform.rotation.z, c.transform.rotation.w)) +
        ",\"fov\":" + Fl(c.fieldOfView) + ",\"near\":" + Fl(c.nearClipPlane) + ",\"far\":" + Fl(c.farClipPlane) +
        ",\"background\":" + C(c.backgroundColor) + ",\"width\":" + c.pixelWidth + ",\"height\":" + c.pixelHeight + "}";

    // ---------------------------------------------------------------- text helpers
    static string PathOf(Transform t, Transform root)
    {
        var parts = new List<string>();
        for (var x = t; x && x != root.parent; x = x.parent) parts.Insert(0, x.name);
        return string.Join("/", parts);
    }

    static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "");
    static string Fl(float f) => float.IsNaN(f) || float.IsInfinity(f) ? "null" : f.ToString("R", Inv);
    static string V4(Vector4 v) => "[" + Fl(v.x) + "," + Fl(v.y) + "," + Fl(v.z) + "," + Fl(v.w) + "]";
    static string C(Color c) => "[" + Fl(c.r) + "," + Fl(c.g) + "," + Fl(c.b) + "," + Fl(c.a) + "]";
    static string M(Matrix4x4 m) => "[" + string.Join(",", Enumerable.Range(0, 16).Select(i => Fl(m[i % 4, i / 4]))) + "]";
}
