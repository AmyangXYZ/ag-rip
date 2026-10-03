// Character add-on to the pipeline stand-in (AGSimPipeline): the parts of the game's render
// pipeline that only characters use, ported from the decompiled GameAssembly.dll:
//
//   LightingFeatures.SetupTowardMatrix    sim_TowardMatrix (camera-facing frame the character
//                                         light _LocalLightDir is expressed in)
//   LightingFeatures.SetupShadows         character shadow map (SimMainLight._castCharacterShadow
//     + DrawShadowPass.Execute            with SceneSetting._supportDirectionalLight):
//     + LightingFeatures.GetWorldToShadow  sim_CharacterShadowmap / World2Shadow / ShadowOffset0-3 /
//                                         ShadowLightDirection, keyword SIM_DIRECTIONAL_SHADOW
//   LightingFeatures.SetupRenderFeature   SIM_DIRECTIONAL_SHADOW on/off
//   CharacterGlobalParams.Execute         _OutlineMaxOffsetMultiplier
//   CharacterFeature.Create/AddRenderPasses  after the opaques (event 300): DrawObjectsPass
//                                         "Char Hair Shadow" (LightMode CharHairShadow), "Char Face
//                                         Shadow" (CharFaceShadow), "Char Hair TransE" (CharHairTransE),
//                                         opaque queue range, all layers; then (event 301)
//   OverrideEffectPass.Execute /          the eye "see-through-hair" passes: for every registered
//     OverriderEffectSystem.OverriderEffectRendering  eye list, overridePass1, then 2, then 3,
//                                         cmd.DrawRenderer(renderer, sharedMaterials[sub], sub, pass)
//   CharacterSceneEnvironment.Update      scene character ambient (EnvironmentEffectUtil) when the
//                                         scene has one
//   CharacterPointLightFeature.SetupRenderFeature -> CharacterPointLightSystem.SetUp
//     (+ CharacterPointLightController.OnEnable/Update/OnDisable, CharacterPointLightSystem.Regist)
//                                         the CharacterLighting cbuffer of the PBR character
//                                         shaders (Uber/Eye/Fur/StockingCast/VAT): up to 4 lamps
//                                         registered by CharacterPointLightControllers
//
// The built-in renderer never draws passes whose LightMode it does not know (CharHairShadow,
// CharFaceShadow, Override1-3, PreDepth, Reflection), so those are drawn here from a command
// buffer at CameraEvent.AfterForwardOpaque, before AGSimPipeline's opaque grab. The face writes
// stencil 200 in its ForwardBase pass, the hair's CharHairShadow pass (shifted along the
// character light) increments it to 201 where the hair's shadow falls, and the face's
// CharFaceShadow pass paints the shadowed face there and decrements it back. Eyes write 255;
// the Override passes redraw them where hair covers them (ZTest GEqual / Equal, stencil == 255).
//
// Static: hooks Camera.onPreCull on load (editor and player); no scene object needed.
using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

public static class AGSimCharacter
{
    /// CameraExtension.shadowRotationImmediately for cameras without a CameraExtension: the DLC
    /// poster sets it on the main camera (Lua PosterGirlDlcActor.InitCameraParams).
    public static bool shadowRotationImmediately = true;
    /// SetupTowardMatrix without a CameraExtension: 60 degrees/s.
    public const float DefaultShadowRotationSpeed = 60f;
    public static bool enabled = true;
    /// Draw the CharHairShadow / CharFaceShadow / CharHairTransE passes from the after-opaque
    /// command buffer. The built-in renderer skips passes with a LightMode it does not know; if a
    /// Frame Debugger capture shows it drawing them itself, turn this off to avoid drawing twice.
    public static bool drawCustomLightModePasses = true;

    // ---------------------------------------------------------------- bootstrap
#if UNITY_EDITOR
    [UnityEditor.InitializeOnLoadMethod]
#endif
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
    static void Boot() => Ensure();

    static bool _hooked;
    public static void Ensure()
    {
        if (_hooked) return;
        _hooked = true;
        Camera.onPreCull += OnPreCullCamera;
    }

    /// Rescan the scene now (batch captures call this after loading).
    public static void Refresh() { Ensure(); Scan(); }

    // ---------------------------------------------------------------- scene data
    static Renderer[] _renderers = Array.Empty<Renderer>();
    static float _nextScan;
    static MonoBehaviour _sceneSetting, _simMainLight, _sceneEnvironment;

    static MonoBehaviour FindByTypeName(string name)
    {
        foreach (var mb in Object.FindObjectsByType<MonoBehaviour>())
            if (mb && mb.isActiveAndEnabled && mb.GetType().Name == name) return mb;
        return null;
    }

    static T Get<T>(object o, string field, T fallback = default)
    {
        if (o == null) return fallback;
        var f = o.GetType().GetField(field, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        if (f == null) return fallback;
        var v = f.GetValue(o);
        if (v is T t) return t;
        try { return (T)Convert.ChangeType(v, typeof(T)); } catch { return fallback; }
    }

    static void Scan()
    {
        _renderers = Object.FindObjectsByType<Renderer>();
        _sceneSetting = FindByTypeName("SceneSetting");
        _simMainLight = FindByTypeName("SimMainLight");
        _sceneEnvironment = FindByTypeName("CharacterSceneEnvironment");
        _pointLightControllers.Clear();
        foreach (var mb in Object.FindObjectsByType<MonoBehaviour>(FindObjectsInactive.Include))
            if (mb && mb.GetType().Name == "CharacterPointLightController") _pointLightControllers.Add(mb);
        _nextScan = Time.realtimeSinceStartup + 2f;
    }

    // ---------------------------------------------------------------- per camera
    static void OnPreCullCamera(Camera cam)
    {
        if (!enabled || !cam) return;
        if (Time.realtimeSinceStartup > _nextScan || _renderers.Length == 0) Scan();
        SetupTowardMatrix(cam);
        SetupCharacterGlobalParams(cam);
        bool dirShadow = SetupCharacterShadow(cam);
        SetKeyword("SIM_DIRECTIONAL_SHADOW", dirShadow);
        SetupSceneEnvironment(cam);
        SetupCharacterPointLights();
        BuildCharacterPasses(cam);
    }

    static void SetKeyword(string k, bool on) { if (on) Shader.EnableKeyword(k); else Shader.DisableKeyword(k); }

    // LightingFeatures.SetupTowardMatrix @0x29b03a0: toward = normalize(-forward.x, 0, -forward.z)
    // (Vector3.zero if degenerate), turned from the camera's last value (default (0,1,0)) by at most
    // deltaTime * speed degrees (CameraExtension.shadowRotationSpeed, 60 without one), or taken as is
    // with CameraExtension.shadowRotationImmediately. Columns: (up x d, up, d, (0,0,0,1)).
    static readonly Dictionary<Camera, Vector3> _lastToward = new Dictionary<Camera, Vector3>();

    static void SetupTowardMatrix(Camera cam)
    {
        Vector3 f = cam.transform.forward;
        float x = -f.x, z = -f.z;
        float len = (float)Math.Sqrt(x * x + 0.0 + z * z);
        Vector3 target = len > 1e-5f ? new Vector3(x / len, 0f / len, z / len) : Vector3.zero;
        Vector3 last = _lastToward.TryGetValue(cam, out var l) ? l : new Vector3(0f, 1f, 0f);
        bool immediate = shadowRotationImmediately;
        float speed = DefaultShadowRotationSpeed;
        foreach (var mb in cam.GetComponents<MonoBehaviour>())
            if (mb && mb.GetType().Name == "CameraExtension")
            {
                immediate = Get(mb, "shadowRotationImmediately", false);
                speed = Get(mb, "shadowRotationSpeed", speed);
            }
        if (immediate || !Application.isPlaying) last = target;   // edit mode: no frame time to turn with
        Vector3 d = Vector3.RotateTowards(last, target, Time.deltaTime * speed * 3.1415927f / 180f, 0f);
        _lastToward[cam] = d;
        var m = new Matrix4x4(new Vector4(d.z - d.y * 0f, d.x * 0f - d.z * 0f, d.y * 0f - d.x, 0f),
                              new Vector4(0f, 1f, 0f, 0f),
                              new Vector4(d.x, d.y, d.z, 0f),
                              new Vector4(0f, 0f, 0f, 1f));
        Shader.SetGlobalMatrix("sim_TowardMatrix", m);
    }

    // CharacterGlobalParams.Execute @0x298d370: max(pixelWidth / 768 - 1, 0); a scene-view camera
    // uses Camera.main's pixelWidth / 768 (no -1), or 2 without a main camera.
    static void SetupCharacterGlobalParams(Camera cam)
    {
        float v = Mathf.Max(cam.pixelWidth / 768f - 1f, 0f);
        if (cam.cameraType == CameraType.SceneView)
        {
            var main = Camera.main;
            v = main ? main.pixelWidth / 768f : 2f;
        }
        Shader.SetGlobalFloat("_OutlineMaxOffsetMultiplier", v);
    }

    // ---------------------------------------------------------------- character shadow
    // LightingFeatures.SetupShadows @0x29aec80, character branch: SimMainLight.currentDirctionalLight
    // among the visible lights, Light.shadows != None, SimMainLight._castCharacterShadow and
    // RenderSettings.supportDirectionalLight (<- SceneSetting._supportDirectionalLight). 2048^2 16-bit
    // map; view = light.worldToLocalMatrix with row 2 negated, proj = Ortho(+-width/2, +-height/2,
    // near, far); DrawShadowPass: sim_ShadowBias = (depth, normal, 0, 0), SetGlobalDepthBias(0, 0),
    // DrawShadows (SHADOWCASTER passes; SIM_DIRECTIONAL_SHADOW variant, along
    // sim_CharacterShadowLightDirection).
    const int CharShadowRes = 2048;
    static RenderTexture _charShadow;
    static CommandBuffer _shadowCb;
    static readonly Dictionary<Shader, Dictionary<string, int>> _passByLightMode = new Dictionary<Shader, Dictionary<string, int>>();
    static readonly ShaderTagId LightModeTag = new ShaderTagId("LightMode");

    static int PassIndex(Shader s, string lightMode)
    {
        if (!s) return -1;
        if (!_passByLightMode.TryGetValue(s, out var map))
        {
            map = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < s.passCount; i++)
            {
                string n = s.FindPassTagValue(i, LightModeTag).name;
                if (!string.IsNullOrEmpty(n) && !map.ContainsKey(n)) map[n] = i;
            }
            _passByLightMode[s] = map;
        }
        return map.TryGetValue(lightMode, out int p) ? p : -1;
    }

    static bool SetupCharacterShadow(Camera cam)
    {
        var sml = _simMainLight;
        if (!sml || !_sceneSetting) return false;
        var light = sml.GetComponent<Light>();
        if (!light || !light.isActiveAndEnabled || light.type != LightType.Directional) return false;
        if (light.shadows == LightShadows.None) return false;
        if (Get<int>(sml, "_castCharacterShadow", 0) == 0) return false;
        if (Get<int>(_sceneSetting, "_supportDirectionalLight", 0) == 0) return false;

        float halfW = Get(sml, "_characterShadowWidth", 0f) * 0.5f, halfH = Get(sml, "_characterShadowHeight", 0f) * 0.5f;
        float near = Get(sml, "_characterShadowNear", 0f), far = Get(sml, "_characterShadowFar", 0f);
        Matrix4x4 view = light.transform.worldToLocalMatrix;
        view.SetRow(2, -view.GetRow(2));
        Matrix4x4 proj = Matrix4x4.Ortho(-halfW, halfW, -halfH, halfH, near, far);
        float texel = Mathf.Max(2f / proj.m11, 2f / proj.m00);
        float depthBias = light.shadowBias * 2f / Mathf.Min(proj.m00, proj.m11) * texel * 0.00048828125f;
        float normalBias = -light.shadowNormalBias * texel * 0.00048828125f * 3.65f;

        if (!_charShadow)
            _charShadow = new RenderTexture(CharShadowRes, CharShadowRes, 16, RenderTextureFormat.Shadowmap)
                { filterMode = FilterMode.Bilinear, hideFlags = HideFlags.HideAndDontSave, name = "sim_CharacterShadowmap" };
        _shadowCb ??= new CommandBuffer { name = "AG character shadow" };
        _shadowCb.Clear();
        Vector3 L = -light.transform.forward;
        _shadowCb.SetGlobalVector("sim_CharacterShadowLightDirection", new Vector4(L.x, L.y, L.z, 0f));
        _shadowCb.SetRenderTarget(_charShadow);
        _shadowCb.ClearRenderTarget(true, false, Color.black);
        _shadowCb.SetViewProjectionMatrices(view, proj);
        _shadowCb.SetGlobalVector("sim_ShadowBias", new Vector4(depthBias, normalBias, 0f, 0f));
        _shadowCb.SetGlobalDepthBias(0f, 0f);
        var vp = proj * view;
        foreach (var r in _renderers)
        {
            if (!r || !r.enabled || !r.gameObject.activeInHierarchy || r.shadowCastingMode == ShadowCastingMode.Off) continue;
            if (!InClipBox(vp, r.bounds)) continue;
            var mats = r.sharedMaterials;
            for (int sm = 0; sm < mats.Length; sm++)
            {
                if (!mats[sm] || !mats[sm].GetShaderPassEnabled("ShadowCaster")) continue;
                int pass = PassIndex(mats[sm].shader, "SHADOWCASTER");
                if (pass >= 0) _shadowCb.DrawRenderer(r, mats[sm], sm, pass);
            }
        }
        _shadowCb.SetGlobalDepthBias(0f, 0f);
        _shadowCb.SetViewProjectionMatrices(cam.worldToCameraMatrix, cam.projectionMatrix);
        Shader.EnableKeyword("SIM_DIRECTIONAL_SHADOW");   // the casters' variant, as on the game's cmd
        Graphics.ExecuteCommandBuffer(_shadowCb);

        // GetWorldToShadow @0x29adef0: reversed-Z flips row 2 of proj, then [0.5 scale, 0.5 bias] * proj * view
        Matrix4x4 pz = proj;
        if (SystemInfo.usesReversedZBuffer) pz.SetRow(2, -pz.GetRow(2));
        var sb = Matrix4x4.identity;
        sb.m00 = sb.m11 = sb.m22 = 0.5f; sb.m03 = sb.m13 = sb.m23 = 0.5f;
        Shader.SetGlobalTexture("sim_CharacterShadowmap", _charShadow);
        Shader.SetGlobalMatrix("sim_CharacterWorld2Shadow", sb * (pz * view));
        float h = 0.5f / CharShadowRes;
        Shader.SetGlobalVector("sim_CharacterShadowOffset0", new Vector4(-h, -h, 0f, 0f));
        Shader.SetGlobalVector("sim_CharacterShadowOffset1", new Vector4(-h, h, 0f, 0f));
        Shader.SetGlobalVector("sim_CharacterShadowOffset2", new Vector4(h, h, 0f, 0f));
        Shader.SetGlobalVector("sim_CharacterShadowOffset3", new Vector4(h, -h, 0f, 0f));
        Shader.SetGlobalVector("sim_CharacterShadowLightDirection", new Vector4(L.x, L.y, L.z, 0f));
        // The game also sets _MainLightShadowParams = (light.shadowStrength, 0, 0, 0) here; left to
        // AGSimShadows, which owns that global for the cascades (see README, open questions).
        return true;
    }

    static bool InClipBox(Matrix4x4 vp, Bounds b)
    {
        Vector3 mn = new Vector3(float.MaxValue, float.MaxValue, float.MaxValue), mx = -mn;
        for (int i = 0; i < 8; i++)
        {
            var c = new Vector3((i & 1) != 0 ? b.max.x : b.min.x, (i & 2) != 0 ? b.max.y : b.min.y, (i & 4) != 0 ? b.max.z : b.min.z);
            var p = vp.MultiplyPoint(c);
            mn = Vector3.Min(mn, p); mx = Vector3.Max(mx, p);
        }
        return mx.x >= -1f && mn.x <= 1f && mx.y >= -1f && mn.y <= 1f && mx.z >= -1f && mn.z <= 1f;
    }

    // ---------------------------------------------------------------- CharacterSceneEnvironment
    // CharacterSceneEnvironment.OnEnable/Update/OnDisable: with one in the scene, every character's
    // EnvironmentEffect takes its SH from the resolved CharacterEnvironmentSetting sky/equator/ground
    // (the component overrides those defaults; volume profiles may override again) and its reflectionMap.
    static void SetupSceneEnvironment(Camera cam)
    {
        var env = _sceneEnvironment;
        CharacterEffect.EnvironmentEffectUtil.useSceneEnvironment = env != null;
        if (!env) return;
        Vector3 p = cam.transform.position;
        Color sky = AGVolumes.Get("CharacterEnvironmentSetting", "skyColor", Get(env, "skyColor", new Color(0.8f, 0.8f, 0.8f, 1f)), p);
        Color eq = AGVolumes.Get("CharacterEnvironmentSetting", "equatorColor", Get(env, "equatorColor", new Color(0.5f, 0.5f, 0.5f, 1f)), p);
        Color gr = AGVolumes.Get("CharacterEnvironmentSetting", "groundColor", Get(env, "groundColor", new Color(0.3f, 0.3f, 0.3f, 1f)), p);
        var refl = AGVolumes.Get<Texture>("CharacterEnvironmentSetting", "reflectionMap", Get<Texture>(env, "reflectionMap", null), p);
        CharacterEffect.EnvironmentEffectUtil.SHCoefficients(
            CharacterEffect.EnvironmentEffectUtil.GetAmbientProbe(sky, eq, gr), CharacterEffect.EnvironmentEffectUtil.sceneSH);
        CharacterEffect.EnvironmentEffectUtil.sceneReflectionMap = refl as Cubemap;
    }

    // ---------------------------------------------------------------- character point lights
    // CharacterPointLightController.OnEnable registers (light, (diffuseIntensity, specularIntensity,
    // rimIntensity, flattedIntensity)) with CharacterPointLightSystem under an increasing index
    // (Dictionary<int, CharacterPointLightData>: enumerated in registration order) and sets the
    // light's ReplicaAdditionalLightData.m_Dummy; Update pushes changed intensities; OnDisable
    // removes it. CharacterPointLightFeature.SetupRenderFeature runs CharacterPointLightSystem.SetUp
    // @0x392ae60 on the camera's command buffer:
    //   CharacterLightCount = min(count, 4) (the shader reads it with asint: integer bits)
    //   for the first 4 registered lights whose Light.type != Directional (Light.enabled is not tested):
    //     CharacterLightPosition[i]    = (transform.position, 0)
    //     CharacterLightColor[i]       = (pow(r * intensity, 2.2), pow(g * I, 2.2), pow(b * I, 2.2), color.a)
    //     CharacterLightParameters[i]  = (diffuse, specular, rim, flatted)
    //     CharacterLightSpotDir[i]     = (0, 0, 1, 1 / m_ShapeRadius); spot: xyz = localToWorld column 2
    //     CharacterLightAttenuation[i] = (1 / max(range^4, 1e-4), -range^4 / (0.64 range^4 - range^4), 0, 1);
    //                                    spot: z, w = LightInfo.GetSpotAngleAttenuation(spotAngle, innerSpotAngle)
    // The shader (SimPipeline/Character/PBR/Uber ForwardBase) then takes, per lamp at squared distance
    // d2: min(1 / d2, SpotDir.w) * max(1 - (d2 * Att.x)^2, 0)^2 * spot^2 * Color, so the window
    // reaches range^2 and the inverse square is clamped at d2 = m_ShapeRadius.
    static readonly List<MonoBehaviour> _pointLightControllers = new List<MonoBehaviour>();
    static readonly List<MonoBehaviour> _pointLightRegistry = new List<MonoBehaviour>();
    static readonly Vector4[] _cplPos = new Vector4[4], _cplCol = new Vector4[4], _cplAtt = new Vector4[4],
                              _cplPar = new Vector4[4], _cplDir = new Vector4[4];

    static void SetupCharacterPointLights()
    {
        // the system's registry: drop the disabled, append the newly enabled (OnEnable order)
        _pointLightRegistry.RemoveAll(c => !c || !c.isActiveAndEnabled);
        foreach (var c in _pointLightControllers)
            if (c && c.isActiveAndEnabled && !_pointLightRegistry.Contains(c)) _pointLightRegistry.Add(c);
        int count = Mathf.Min(_pointLightRegistry.Count, 4);
        Shader.SetGlobalFloat("CharacterLightCount", BitConverter.Int32BitsToSingle(count));
        for (int i = 0; i < 4; i++)
        {
            _cplPos[i] = _cplCol[i] = _cplAtt[i] = _cplPar[i] = _cplDir[i] = Vector4.zero;
            if (count == 0) _cplCol[i].w = 1f;
        }
        if (count > 0)
        {
            int n = 0;
            foreach (var c in _pointLightRegistry)
            {
                if (n >= 4) break;
                var light = c.GetComponent<Light>();     // Awake: _curLight = GetComponent<Light>()
                if (!light || light.type == LightType.Directional) continue;
                float shape = 0.01f;                      // ReplicaAdditionalLightData [Min(0.01)]
                foreach (var mb in light.GetComponents<MonoBehaviour>())
                    if (mb && mb.GetType().Name == "ReplicaAdditionalLightData") shape = Get(mb, "m_ShapeRadius", shape);
                float range = light.range, intensity = light.intensity;
                Color lc = light.color;
                Vector3 p = light.transform.position;
                _cplPos[n] = new Vector4(p.x, p.y, p.z, 0f);
                _cplCol[n] = new Vector4(Mathf.Pow(lc.r * intensity, 2.2f), Mathf.Pow(lc.g * intensity, 2.2f),
                                         Mathf.Pow(lc.b * intensity, 2.2f), lc.a);
                _cplPar[n] = new Vector4(Get(c, "diffuseIntensity", 1f), Get(c, "specularIntensity", 1f),
                                         Get(c, "rimIntensity", 1f), Get(c, "flattedIntensity", 0f));
                _cplDir[n] = new Vector4(0f, 0f, 1f, 1f / shape);
                float r4 = range * range * range * range;
                _cplAtt[n] = new Vector4(1f / Mathf.Max(r4, 1e-4f), -r4 / (r4 * 0.64f - r4), 0f, 1f);
                if (light.type == LightType.Spot)
                {
                    // GetSpotAngleAttenuation(spotAngle, innerSpotAngle as a Nullable with a value)
                    float cosOuter = Mathf.Cos(light.spotAngle * 0.017453292f * 0.5f);
                    float cosInner = Mathf.Cos(light.innerSpotAngle * 0.017453292f * 0.5f);
                    float inv = 1f / Mathf.Max(cosInner - cosOuter, 0.001f);
                    _cplAtt[n].z = inv; _cplAtt[n].w = -cosOuter * inv;
                    Vector3 f = light.transform.localToWorldMatrix.GetColumn(2);
                    _cplDir[n] = new Vector4(f.x, f.y, f.z, _cplDir[n].w);
                }
                n++;
            }
        }
        Shader.SetGlobalVectorArray("CharacterLightPosition", _cplPos);
        Shader.SetGlobalVectorArray("CharacterLightColor", _cplCol);
        Shader.SetGlobalVectorArray("CharacterLightAttenuation", _cplAtt);
        Shader.SetGlobalVectorArray("CharacterLightParameters", _cplPar);
        Shader.SetGlobalVectorArray("CharacterLightSpotDir", _cplDir);
    }

    // ---------------------------------------------------------------- after-opaque character passes
    const string PassesName = "AG character passes (hair/face shadow, eye override)";
    static readonly Dictionary<Camera, CommandBuffer> _passes = new Dictionary<Camera, CommandBuffer>();
    static readonly string[] AfterOpaqueLightModes = { "CharHairShadow", "CharFaceShadow", "CharHairTransE" };
    struct Draw { public Renderer r; public Material m; public int sub, pass, queue; public float dist; }
    static readonly List<Draw> _draws = new List<Draw>();
    /// The after-opaque draws of the last frame, in order, for a recorder (AGDlcRecord).
    public static readonly List<(Renderer renderer, Material material, int submesh, int pass)> LastPassDraws =
        new List<(Renderer, Material, int, int)>();

    static CommandBuffer PassesBuffer(Camera cam)
    {
        if (_passes.TryGetValue(cam, out var cb) && cb != null) return cb;
        foreach (var b in cam.GetCommandBuffers(CameraEvent.AfterForwardOpaque))
            if (b.name == PassesName) { _passes[cam] = b; return b; }
        cb = new CommandBuffer { name = PassesName };
        cam.AddCommandBuffer(CameraEvent.AfterForwardOpaque, cb);
        _passes[cam] = cb;
        return cb;
    }

    static void BuildCharacterPasses(Camera cam)
    {
        var cb = PassesBuffer(cam);
        cb.Clear();
        LastPassDraws.Clear();
        Vector3 camPos = cam.transform.position;
        // DrawObjectsPass(..., opaque, RenderQueueRange.opaque, all layers): CommonOpaque sorting
        foreach (var lightMode in drawCustomLightModePasses ? AfterOpaqueLightModes : Array.Empty<string>())
        {
            _draws.Clear();
            foreach (var r in _renderers)
            {
                if (!r || !r.enabled || !r.gameObject.activeInHierarchy) continue;
                if ((cam.cullingMask & (1 << r.gameObject.layer)) == 0) continue;
                var mats = r.sharedMaterials;
                for (int sm = 0; sm < mats.Length; sm++)
                {
                    var m = mats[sm];
                    if (!m || m.renderQueue > (int)RenderQueue.GeometryLast || !m.GetShaderPassEnabled(lightMode)) continue;
                    int pass = PassIndex(m.shader, lightMode);
                    if (pass < 0) continue;
                    _draws.Add(new Draw { r = r, m = m, sub = sm, pass = pass, queue = m.renderQueue,
                                          dist = (r.bounds.center - camPos).sqrMagnitude });
                }
            }
            _draws.Sort((a, b) => a.queue != b.queue ? a.queue.CompareTo(b.queue) : a.dist.CompareTo(b.dist));
            foreach (var d in _draws)
            {
                cb.DrawRenderer(d.r, d.m, d.sub, d.pass);
                LastPassDraws.Add((d.r, d.m, d.sub, d.pass));
            }
        }
        // OverrideEffectPass (event 301): every registered eye list, pass group 1, 2, 3
        for (int g = 0; g < 3; g++)
            foreach (var ce in CharacterEffect.Active)
            {
                var list = ce ? ce.EyeRenderData : null;
                if (list == null) continue;
                foreach (var e in list)
                {
                    if (e == null || !e.renderer || !e.renderer.enabled || e.submeshIndcies == null) continue;
                    var passes = g == 0 ? e.overridePass1 : g == 1 ? e.overridePass2 : e.overridePass3;
                    if (passes == null) continue;
                    var mats = e.renderer.sharedMaterials;
                    for (int i = 0; i < e.submeshIndcies.Count && i < passes.Count; i++)
                    {
                        int sub = e.submeshIndcies[i];
                        if (sub < 0 || sub >= mats.Length || !mats[sub]) continue;
                        cb.DrawRenderer(e.renderer, mats[sub], sub, passes[i]);
                        LastPassDraws.Add((e.renderer, mats[sub], sub, passes[i]));
                    }
                }
            }
    }
}
