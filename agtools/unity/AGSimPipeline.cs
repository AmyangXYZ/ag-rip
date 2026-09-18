// Stand-in for the game's render pipeline (UnityEngine.Rendering.Replica / ReplicaExt),
// feeding the decompiled game shaders the globals the game sets. Since 2026-09-18 this
// follows the game's own code, reverse-engineered from GameAssembly.dll (IL2CPP; see
// README "Reverse engineering" and AG_cache/re/spec), method by method:
//
//   LightingFeatures.SetupLightings       SIM_MAIN_LIGHT, SimMainLightDir/Color/ColorNoInt
//   LightingFeatures.SetupAdditionalLight SimAdditionalLight* (16), SIM_ADDITIONAL_LIGHT
//   ForwardFeature.SetupRenderFeature     SimSceneTint, _ReceiveSceneShadow, sim_FogRotation
//   ForwardFeature.SetupCubeReflection    sim_EnvCube (+Scale, box projection)
//   ForwardFeature.SetupEnvironmentLighting  _Replica_SH* (Unity ambientProbe of the trilight)
//   ForwardFeature.UpdateRenderSettings   fog, dynamic fog, probe lighting, _AcceptLightProbe,
//                                         sim_Time, shadow scatter, _SubtractiveShadowColor
//
// Values come from the volume stack (AGVolumes: SceneSetting defaults, profile overrides),
// set with the same Unity calls as the game (SetGlobalColor vs SetGlobalVector) so Unity
// applies the same colour conversions. Forward+ (PLUS_LIGHTING) with one all-lights bin.
// Not reproduced: SSAO,
// regional/character shadows, volumetrics, per-renderer dithering.
using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;
using UnityEngine.Rendering;

[ExecuteAlways]
[DefaultExecutionOrder(-1000)]
public class AGSimPipeline : MonoBehaviour
{
    [Header("Game settings (GameAssembly / globalgamemanagers)")]
    [Tooltip("GraphicsSettings.lightsUseLinearIntensity - false in the game: finalColor = (colour * intensity).linear.")]
    public bool lightsUseLinearIntensity = false;
    [Header("Debug")]
    [Tooltip("Multiplier on the environment's bakeReflectionScale.")]
    public float envCubeScale = 1f;
    [Tooltip("Forward+ (PLUS_LIGHTING, PlusLightingFeature): every light through the light list, as the game renders stages.")]
    public bool plusLighting = true;
    [Tooltip("Legacy per-object additional lights (SIM_ADDITIONAL_LIGHT): the game only enables it when SceneSetting.dynamicLightingEnabled is set at runtime.")]
    public bool additionalLights = false;
    [Tooltip("Cascaded main-light shadows (MAIN_LIGHT_SHADOWS), settings from the scene's CascadeShadowSetting.")]
    public bool mainLightShadows = true;
    public bool softShadows = true;
    [Tooltip("Sun only - no ambient, reflection or fog - to judge direct light and shadows.")]
    public bool debugDirectOnly = false;

    [Header("Found in the scene (read-only)")]
    public MonoBehaviour sceneSetting;
    public Light mainLight;
    public Texture envCube;
    public int additionalLightCount;

    const int MaxLights = 16;
    readonly Vector4[] _pos = new Vector4[MaxLights], _col = new Vector4[MaxLights],
                       _att = new Vector4[MaxLights], _dir = new Vector4[MaxLights];
    readonly List<Light> _lights = new List<Light>();
    Renderer[] _renderers = Array.Empty<Renderer>();
    MaterialPropertyBlock _mpb;
    float _nextScan;

    // ---------------------------------------------------------------- bootstrap
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Boot() => Ensure();

    public static AGSimPipeline Ensure()
    {
        var existing = FindObjectOfType<AGSimPipeline>();
        if (!existing)
        {
            var go = new GameObject("AGSimPipeline (generated)") { hideFlags = HideFlags.DontSave };
            existing = go.AddComponent<AGSimPipeline>();
        }
        AGStageCamera.Ensure();
        return existing;
    }

    void OnEnable()
    {
        _mpb = new MaterialPropertyBlock();
        Scan();
        Camera.onPreCull += OnPreCullCamera;
    }

    void OnDisable()
    {
        Camera.onPreCull -= OnPreCullCamera;
    }

    /// <summary>Rescan the scene and push every global now (batch mode has no Update).</summary>
    public void Refresh()
    {
        if (_mpb == null) _mpb = new MaterialPropertyBlock();
        Scan();
        SetGlobals();
        if (additionalLights) AssignObjectLights();
        AssignRenderingLayers();
    }

    void Update()
    {
        if (Time.realtimeSinceStartup > _nextScan) { Scan(); _nextScan = Time.realtimeSinceStartup + 2f; AssignRenderingLayers(); }
        SetGlobals();
        if (additionalLights) AssignObjectLights();
    }

    // Forward+ rejects a light unless unity_RenderingLayer & light mask != 0. SRP fills
    // unity_RenderingLayer per draw from Renderer.renderingLayerMask; the built-in renderer
    // leaves it 0, so it is set here (raw bits, as the shader reads them).
    void AssignRenderingLayers()
    {
        if (_mpb == null) _mpb = new MaterialPropertyBlock();
        foreach (var r in _renderers)
        {
            if (!r) continue;
            r.GetPropertyBlock(_mpb);
            _mpb.SetVector("unity_RenderingLayer", new Vector4(IntBits((int)r.renderingLayerMask), 0, 0, 0));
            r.SetPropertyBlock(_mpb);
        }
    }

    void OnPreCullCamera(Camera cam)
    {
        bool shadows = mainLightShadows && mainLight &&
                       AGSimShadows.Render(cam, mainLight, _renderers, AGSimShadows.Read(), softShadows);
        SetKeyword("MAIN_LIGHT_SHADOWS", shadows);
        if (!shadows) Shader.SetGlobalVector("_MainLightShadowParams", Vector4.zero);
        int w = cam.pixelWidth, h = cam.pixelHeight;
        Shader.SetGlobalVector("_ScaledScreenParams", new Vector4(w, h, 1f + 1f / w, 1f + 1f / h));
        // UpdateRenderSettings: sim_Time = (t - floor(t), floor(t), 0, 0), t = Time.time
        float t = Application.isPlaying ? Time.time : (float)(Time.realtimeSinceStartupAsDouble % 100000.0);
        Shader.SetGlobalVector("sim_Time", new Vector4(t - Mathf.Floor(t), Mathf.Floor(t), 0f, 0f));
        Matrix4x4 vp = GL.GetGPUProjectionMatrix(cam.projectionMatrix, true) * cam.worldToCameraMatrix;
        Shader.SetGlobalMatrix("_NonJitteredViewProjMatrix", vp);
        EnsureGrab(cam);
    }

    // ReplicaRenderer.CreateGrabRenderTextures: after the opaques the game copies colour to
    // _CameraOpaqueTexture (+ _OpaqueTexture) and depth (R32, raw device depth) to
    // _CameraDepthTexture (+ _DepthIntermediate) and enables HAS_DEPTH_BUFFER. Water
    // (CartoonWater*, Ripplet) reads them for depth fade, foam, intersection and refraction.
    // Built-in equivalent: the camera depth texture (drawn from the SHADOWCASTER passes, so
    // their shadow bias is zeroed first) copied to R32 by Hidden/AG/CopyDepth, and a colour
    // copy, both after the skybox.
    const string GrabName = "AG grab opaque + depth";
    static readonly int OpaqueId = Shader.PropertyToID("_CameraOpaqueTexture");
    static readonly int DepthCopyId = Shader.PropertyToID("_AGDepthCopy");
    static Material _copyDepth;

    static void EnsureGrab(Camera cam)
    {
        cam.depthTextureMode |= DepthTextureMode.Depth;
        foreach (var b in cam.GetCommandBuffers(CameraEvent.AfterSkybox))
            if (b.name == GrabName) return;
        if (!_copyDepth)
        {
            var sh = Shader.Find("Hidden/AG/CopyDepth");
            if (!sh) return;
            _copyDepth = new Material(sh) { hideFlags = HideFlags.HideAndDontSave };
        }
        var pre = new CommandBuffer { name = GrabName };
        pre.SetGlobalVector("sim_ShadowBias", Vector4.zero);
        cam.AddCommandBuffer(CameraEvent.BeforeDepthTexture, pre);
        var cb = new CommandBuffer { name = GrabName };
        cb.GetTemporaryRT(OpaqueId, -1, -1, 0, FilterMode.Bilinear, RenderTextureFormat.DefaultHDR);
        cb.Blit(BuiltinRenderTextureType.CurrentActive, OpaqueId);
        cb.SetGlobalTexture("_OpaqueTexture", OpaqueId);
        cb.GetTemporaryRT(DepthCopyId, -1, -1, 0, FilterMode.Point, RenderTextureFormat.RFloat);
        cb.Blit(BuiltinRenderTextureType.None, DepthCopyId, _copyDepth);
        cb.SetGlobalTexture("_DepthIntermediate", DepthCopyId);
        cb.EnableShaderKeyword("HAS_DEPTH_BUFFER");
        cam.AddCommandBuffer(CameraEvent.AfterSkybox, cb);
    }

    // ---------------------------------------------------------------- scene data
    static MonoBehaviour FindByTypeName(string name)
    {
        foreach (var mb in FindObjectsOfType<MonoBehaviour>())
            if (mb && mb.GetType().Name == name) return mb;
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

    void Scan()
    {
        sceneSetting = FindByTypeName("SceneSetting");
        // Unity's mainLightIndex: RenderSettings.sun if set, else the brightest directional light
        mainLight = RenderSettings.sun && RenderSettings.sun.isActiveAndEnabled ? RenderSettings.sun : null;
        if (!mainLight)
        {
            var simMain = FindByTypeName("SimMainLight");
            mainLight = simMain ? simMain.GetComponent<Light>() : null;
        }
        if (!mainLight)
            foreach (var l in FindObjectsOfType<Light>())
                if (l.type == LightType.Directional && l.isActiveAndEnabled && (!mainLight || l.intensity > mainLight.intensity))
                    mainLight = l;
        _lights.Clear();
        foreach (var l in FindObjectsOfType<Light>())
            if (l != mainLight && l.isActiveAndEnabled && l.type != LightType.Directional)
                _lights.Add(l);
        _renderers = FindObjectsOfType<Renderer>();
    }

    static Cubemap _black;
    static Cubemap BlackCube()
    {
        if (_black) return _black;
        _black = new Cubemap(1, TextureFormat.RGBA32, false) { hideFlags = HideFlags.HideAndDontSave };
        foreach (CubemapFace f in Enum.GetValues(typeof(CubemapFace)))
            if (f != CubemapFace.Unknown) _black.SetPixel(f, 0, 0, Color.black);
        _black.Apply();
        return _black;
    }

    // GraphicsSettings.lightsUseLinearIntensity is false in the game: Unity's
    // VisibleLight.finalColor = (colour * intensity) converted gamma -> linear.
    Vector4 LightColor(Light l) =>
        lightsUseLinearIntensity ? (Vector4)(l.color.linear * l.intensity) : (Vector4)((l.color * l.intensity).linear);

    Vector3 CamPos => Camera.main ? Camera.main.transform.position : Vector3.zero;
    T Vol<T>(string component, string param, T fallback) => AGVolumes.Get(component, param, fallback, CamPos);

    // ---------------------------------------------------------------- globals
    void SetGlobals()
    {
        var ss = sceneSetting;

        // ---- LightingFeatures.SetupLightings
        SetKeyword("SIM_MAIN_LIGHT", mainLight);
        if (mainLight)
        {
            Vector3 d = -mainLight.transform.forward;
            Shader.SetGlobalVector("SimMainLightDir", new Vector4(d.x, d.y, d.z, 0f));
            Shader.SetGlobalColor("SimMainLightColor", (Color)LightColor(mainLight));
            Shader.SetGlobalColor("SimMainLightColorNoInt", mainLight.color);
        }
        else
        {
            Shader.SetGlobalVector("SimMainLightDir", Vector4.zero);
            Shader.SetGlobalColor("SimMainLightColor", Color.clear);
            Shader.SetGlobalColor("SimMainLightColorNoInt", Color.clear);
        }

        // ---- ForwardFeature.SetupRenderFeature: tint (near-black -> white), scene shadow, fog rotation
        Color tint = Vol("EnvironmentSetting", "tint", Color.white);
        if (tint.r * tint.r + tint.g * tint.g + tint.b * tint.b + tint.a * tint.a < 1e-5f) tint = Color.white;
        Shader.SetGlobalColor("SimSceneTint", tint);
        Shader.SetGlobalFloat("_ReceiveSceneShadow", Get<int>(ss, "_receiveShadow", 0) != 0 ? 1f : 0f);
        bool fogRot = Vol("EnvironmentSetting", "fogRotation", false);
        Shader.SetGlobalFloat("sim_FogRotation", fogRot ? 1f : 0f);
        if (fogRot) Shader.SetGlobalMatrix("sim_FogInverseRootRotationMatrix", Matrix4x4.identity);

        // ---- SetupCubeReflection: only when the environment has a bake reflection cube
        var cube = Vol<Texture>("EnvironmentSetting", "bakeReflectionTex", null);
        envCube = cube;
        if (cube)
        {
            Shader.SetGlobalTexture("sim_EnvCube", cube);
            Shader.SetGlobalFloat("sim_EnvCubeScale", debugDirectOnly ? 0f :
                Vol("EnvironmentSetting", "bakeReflectionScale", 1f) * envCubeScale);
            // box projection from the probe under SceneSetting (SceneSetting.SetupReflectionProject)
            var probe = ss ? ss.GetComponentInChildren<ReflectionProbe>(true) : null;
            if (probe)
            {
                Bounds b = probe.bounds;
                Vector3 mn = b.min, mx = b.max, c = b.center - probe.center;
                Shader.SetGlobalVector("sim_EnvCube_BoxMin", new Vector4(mn.x, mn.y, mn.z, 0f));
                Shader.SetGlobalVector("sim_EnvCube_BoxMax", new Vector4(mx.x, mx.y, mx.z, 0f));
                Shader.SetGlobalVector("sim_EnvCube_ProbePosition",
                    new Vector4(c.x, c.y, c.z, 1f / ((mx - mn).magnitude * 0.5f)));
            }
        }
        else Shader.SetGlobalTexture("sim_EnvCube", BlackCube());

        // ---- SetupEnvironmentLighting: SH = Unity's own ambient probe of the trilight colours.
        // Default: the scene's RenderSettings ambient; the environment's colours when both
        // sceneLightProbe and setLightProbe. Colours are made linear first (ConvertSRGBToActiveColorSpace).
        Color sky = RenderSettings.ambientSkyColor.linear, eq = RenderSettings.ambientEquatorColor.linear,
              gr = RenderSettings.ambientGroundColor.linear;
        if (Vol("EnvironmentSetting", "sceneLightProbe", false) && Vol("EnvironmentSetting", "setLightProbe", false))
        {
            sky = Vol("EnvironmentSetting", "skyColor", Color.black).linear;
            eq = Vol("EnvironmentSetting", "equatorColor", Color.black).linear;
            gr = Vol("EnvironmentSetting", "groundColor", Color.black).linear;
        }
        if (debugDirectOnly) sky = eq = gr = Color.black;
        SetAmbientSH(sky, eq, gr);

        // ---- UpdateRenderSettings: fog
        Shader.EnableKeyword("sim_FOG_LINEAR");
        if (!Vol("EnvironmentSetting", "fogEnable", false) || debugDirectOnly)
        {
            Shader.SetGlobalColor("sim_FogColor", Color.clear);
            Shader.SetGlobalColor("sim_FogColor2", Color.clear);
            Shader.SetGlobalColor("sim_FogDirectionalColor", Color.clear);
        }
        else
        {
            float end = Vol("EnvironmentSetting", "fogEnd", 1f), start = Vol("EnvironmentSetting", "fogStart", 0f);
            Shader.SetGlobalColor("sim_FogColor", Vol("EnvironmentSetting", "fogColor", Color.clear));
            Shader.SetGlobalColor("sim_FogColor2", Vol("EnvironmentSetting", "fogColor2", Color.clear));
            Shader.SetGlobalVector("sim_FogParams", new Vector4(-1f / (end - start), end / (end - start),
                Vol("EnvironmentSetting", "fogHeight", 0f), Vol("EnvironmentSetting", "fogHeightGradient", 0f)));
            if (!Vol("EnvironmentSetting", "fogDirectionalScattering", false) || !RenderSettings.sun ||
                RenderSettings.sun.type != LightType.Directional)
                Shader.SetGlobalColor("sim_FogDirectionalColor", Color.clear);
            else
            {
                Vector3 f = RenderSettings.sun.transform.forward.normalized;
                Shader.SetGlobalVector("sim_FogDirectionalDir", f);
                Color dc = Vol("EnvironmentSetting", "fogDirectionalColor", Color.clear);
                dc.a = Vol("EnvironmentSetting", "fogDirectionalIntensity", 0f);
                Shader.SetGlobalColor("sim_FogDirectionalColor", dc);
                Shader.SetGlobalFloat("sim_FogDirectionalFalloff", Vol("EnvironmentSetting", "fogDirectionalFalloff", 0f));
            }
        }
        // dynamic fog (pipeline RenderSettings, fed by SceneSetting): linear mode
        Shader.DisableKeyword("sim_DYN_FOG_LINEAR");
        Shader.DisableKeyword("sim_DYN_FOG_EXP");
        Shader.DisableKeyword("sim_DYN_FOG_EXP_SQ");
        Color dyn = Get(ss, "_dynamicFogColor", Color.clear);
        if (ss && Get(ss, "_dynamicFogMode", 0) == 1 && dyn.a > 0f && !debugDirectOnly)
        {
            float de = Get(ss, "_dynamicFogEnd", 1f), ds = Get(ss, "_dynamicFogStart", 0f);
            Shader.EnableKeyword("sim_DYN_FOG_LINEAR");
            Shader.SetGlobalColor("sim_DynFogColor", dyn);
            Shader.SetGlobalVector("sim_DynFogParams", new Vector4(-1f / (de - ds), de / (de - ds),
                Get(ss, "_dynamicFogHeight", 0f), Get(ss, "_dynamicFogHeightGradient", 0f)));
        }

        // ---- UpdateRenderSettings: probe lighting, vertex ambient, shadow scatter, shadow colour
        bool accept = Vol("CharacterEnvironmentSetting", "acceptLightProbe", false) ||
                      (Vol("EnvironmentSetting", "setLightProbe", false) && Vol("EnvironmentSetting", "sceneLightProbe", false));
        Shader.SetGlobalColor("_ProbeLightingBase", Vol("CharacterEnvironmentSetting", "probeLightingBase", Color.white));
        Shader.SetGlobalFloat("_ProbeLightingScale", Vol("CharacterEnvironmentSetting", "probeLightingScale", 0f));
        Shader.SetGlobalFloat("_AcceptLightProbe", accept ? 1f : 0f);
        Shader.SetGlobalFloat("sim_VertexAmbientScale", Get(ss, "_vertexAmbientScale", 1f));
        Shader.SetGlobalFloat("_EnableShadowScatter", Get<int>(ss, "_shadowScatter", 0) != 0 ? 1f : 0f);
        Shader.SetGlobalColor("_ShadowScatterColor", Get(ss, "_shadowScatterColor", Color.clear));
        Shader.SetGlobalFloat("_ShadowScatterRange", Get(ss, "_shadowScatterRange", 0f));
        Color shadowCol = Vol("EnvironmentSetting", "realtimeShadowColor", Color.gray);
        Shader.SetGlobalVector("_SubtractiveShadowColor", shadowCol.linear);   // ConvertSRGBToActiveColorSpace

        // screen-space AO not reproduced: neutral inputs
        Shader.SetGlobalTexture("_ScreenSpaceOcclusionTexture", Texture2D.whiteTexture);
        Shader.SetGlobalVector("_AmbientOcclusionParam", Vector4.zero);
        Shader.SetGlobalMatrix("identity_matrix", Matrix4x4.identity);

        // ---- LightingFeatures.SetupAdditionalLight (legacy path, runtime-toggled in the game)
        FillLightTable();
        SetKeyword("SIM_ADDITIONAL_LIGHT", additionalLights && additionalLightCount > 0);
        // ---- PlusLightingFeature / AreaLightFeature: Forward+
        SetupPlusLighting();
    }

    // ---------------------------------------------------------------- Forward+
    // PlusLightingFeature.SetupLightConstants + LightInfo(VisibleLight): per-light arrays
    // (255); SetupMainLightConstants: _MainLightPosition/_MainLightColor; bins and tiles are
    // bound as constant buffers (_PlusLighting_ZBinBuffer / _TileBuffer). The game bins
    // lights per screen tile and depth slice; binning only culls, so one tile and one depth
    // bin holding every light shade identically (the shader still applies each range).
    // AreaLightFeature: _LtcData = 64x64 RGBAHalf LUT (LtcData.s_LtcMatrixData_BRDF_GGX).
    const int PlusMax = 255;
    readonly Vector4[] _pPos = new Vector4[PlusMax], _pCol = new Vector4[PlusMax], _pAtt = new Vector4[PlusMax],
                       _pDir = new Vector4[PlusMax], _pExtra = new Vector4[PlusMax];
    readonly Matrix4x4[] _pW2L = new Matrix4x4[PlusMax];
    readonly float[] _pType = new float[PlusMax], _pMask = new float[PlusMax], _pCookieBits = new float[8];
    readonly uint[] _zbinData = new uint[4096], _tileData = new uint[16384];
    GraphicsBuffer _zbins, _tiles;
    static Texture2D _ltc;
    public int plusLightCount;

    static Texture2D Ltc()
    {
        if (_ltc) return _ltc;
        var bytes = Resources.Load<TextAsset>("ag_ltc");
        if (!bytes) return null;
        _ltc = new Texture2D(64, 64, TextureFormat.RGBAHalf, false, true)
            { name = "LTC_LUT", wrapMode = TextureWrapMode.Clamp, filterMode = FilterMode.Bilinear, hideFlags = HideFlags.HideAndDontSave };
        _ltc.LoadRawTextureData(bytes.bytes);
        _ltc.Apply(false, true);
        return _ltc;
    }

    void SetupPlusLighting()
    {
        SetKeyword("PLUS_LIGHTING", plusLighting);
        if (!plusLighting) return;
        int n = Mathf.Min(_lights.Count, PlusMax);
        plusLightCount = n;
        for (int i = 0; i < PlusMax; i++)
        {
            if (i >= n)
            {
                _pPos[i] = _pCol[i] = _pDir[i] = _pExtra[i] = Vector4.zero;
                _pAtt[i] = new Vector4(0, 1, 0, 1); _pW2L[i] = Matrix4x4.identity; _pType[i] = 0; _pMask[i] = 0;
                continue;
            }
            var l = _lights[i];
            int ext = 0; Vector2 area = Vector2.zero; float shape = 0f, dimmer = 0f; bool affectVol = false, dummy = false;
            foreach (var mb in l.GetComponents<MonoBehaviour>())
                if (mb && mb.GetType().Name == "ReplicaAdditionalLightData")
                {
                    ext = Get(mb, "m_ExtensionType", 0);
                    area = Get(mb, "m_AreaSize", Vector2.zero);
                    shape = Get(mb, "m_ShapeRadius", 0f);
                    affectVol = Get<int>(mb, "m_AffectVolumetric", 0) != 0;
                    dimmer = Get(mb, "m_VolumetricDimmer", 0f);
                    dummy = Get<int>(mb, "m_Dummy", 0) != 0;
                }
            int type = ext != 0 ? ext : (int)l.type;        // Spot 0, Directional 1, Point 2, Rect 3, ...
            Matrix4x4 l2w = l.transform.localToWorldMatrix;
            _pType[i] = type;
            _pCol[i] = LightColor(l);
            _pAtt[i] = new Vector4(0, 1, 0, 1);
            _pDir[i] = new Vector4(0, 0, 1, 1f / shape);
            _pW2L[i] = l2w.inverse;
            if (type == 1)
            {
                Vector3 f = l2w.GetColumn(2);
                _pPos[i] = new Vector4(-f.x, -f.y, -f.z, 0f);
            }
            else
            {
                Vector3 p = l2w.GetColumn(3);
                _pPos[i] = new Vector4(p.x, p.y, p.z, 1f);
                float r2 = l.range * l.range;
                _pAtt[i].x = 1f / Mathf.Max(r2, 1e-4f);
                _pAtt[i].y = -r2 / (r2 * 0.64f - r2);
                if (type == 0)
                {
                    float cosOuter = Mathf.Cos(Mathf.Deg2Rad * l.spotAngle * 0.5f);
                    float cosInner = Mathf.Cos(Mathf.Deg2Rad * l.innerSpotAngle * 0.5f);
                    float inv = 1f / Mathf.Max(cosInner - cosOuter, 0.001f);
                    _pAtt[i].z = inv; _pAtt[i].w = -cosOuter * inv;
                    Vector3 f = l2w.GetColumn(2);
                    _pDir[i] = new Vector4(f.x, f.y, f.z, 1f / shape);
                    var proj = Matrix4x4.Perspective(l.spotAngle, 1f, 0.001f, l.range);
                    proj.SetColumn(2, proj.GetColumn(2) * -1f);
                    _pW2L[i] = proj * l2w.inverse;
                }
            }
            _pExtra[i] = new Vector4(affectVol ? dimmer : 0f, dummy ? 1f : 0f,
                                     type == 3 ? area.x * 0.5f : 0f, type == 3 ? area.y * 0.5f : 0f);
            _pMask[i] = IntBits((int)l.renderingLayerMask);   // raw bits: the shader ands them
        }
        Shader.SetGlobalVectorArray("_AdditionalLightsPosition", _pPos);
        Shader.SetGlobalVectorArray("_AdditionalLightsColor", _pCol);
        Shader.SetGlobalVectorArray("_AdditionalLightsAttenuation", _pAtt);
        Shader.SetGlobalVectorArray("_AdditionalLightsSpotDir", _pDir);
        Shader.SetGlobalVectorArray("_AdditionalLightsExtra", _pExtra);
        Shader.SetGlobalFloatArray("_AdditionalLightsLayerMasks", _pMask);
        Shader.SetGlobalMatrixArray("_AdditionalLightsWorldToLights", _pW2L);
        Shader.SetGlobalFloatArray("_AdditionalLightsLightTypes", _pType);
        Shader.SetGlobalFloatArray("_AdditionalLightsCookieEnableBits", _pCookieBits);

        // one screen tile, one depth bin: header (min index | max index << 16), then masks
        int words = Mathf.Max(1, (n + 31) / 32);
        Array.Clear(_zbinData, 0, _zbinData.Length);
        Array.Clear(_tileData, 0, _tileData.Length);
        _zbinData[0] = n > 0 ? (uint)(n - 1) << 16 : 0xFFFFu;
        for (int w = 0; w < words; w++)
        {
            int bits = Mathf.Clamp(n - w * 32, 0, 32);
            uint m = bits >= 32 ? 0xFFFFFFFFu : (1u << bits) - 1u;
            _zbinData[1 + w] = m;
            _tileData[w] = m;
        }
        if (_zbins == null || !_zbins.IsValid()) _zbins = new GraphicsBuffer(GraphicsBuffer.Target.Constant, 1024, 16);
        if (_tiles == null || !_tiles.IsValid()) _tiles = new GraphicsBuffer(GraphicsBuffer.Target.Constant, 4096, 16);
        _zbins.SetData(_zbinData);
        _tiles.SetData(_tileData);
        Shader.SetGlobalConstantBuffer("_PlusLighting_ZBinBuffer", _zbins, 0, 1024 * 16);
        Shader.SetGlobalConstantBuffer("_PlusLighting_TileBuffer", _tiles, 0, 4096 * 16);
        // Params0 = (tile scale x, y, tile count x, words per tile); Params1 = (far z, bin count, 0, 0)
        Shader.SetGlobalVector("_PlusLightingParams0", new Vector4(0f, 0f, 1f, words));
        Shader.SetGlobalVector("_PlusLightingParams1", new Vector4(1000f, 0f, 0f, 0f));

        // SetupMainLightConstants (vectors: no colour conversion)
        if (mainLight)
        {
            Vector3 f = mainLight.transform.forward;
            Shader.SetGlobalVector("_MainLightPosition", new Vector4(f.x, f.y, f.z, 0f));
            Shader.SetGlobalVector("_MainLightColor", LightColor(mainLight));
        }
        var ltc = Ltc();
        if (ltc) Shader.SetGlobalTexture("_LtcData", ltc);
    }

    void OnDestroy()
    {
        _zbins?.Release(); _tiles?.Release();
        _zbins = _tiles = null;
    }

    // ---------------------------------------------------------------- SH
    // EnvironmentEffectUtil.GetAmbientProbe + SHCoefficients: RenderSettings to trilight with
    // these colours, read Unity's RenderSettings.ambientProbe, restore, pack the raw
    // coefficients: SHA = (c3, c1, c2, c0 - c6), SHB = (c4, c5, 3*c6, c7), SHC = (c8, 1).
    void SetAmbientSH(Color sky, Color equator, Color ground)
    {
        var mode = RenderSettings.ambientMode;
        Color s0 = RenderSettings.ambientSkyColor, e0 = RenderSettings.ambientEquatorColor, g0 = RenderSettings.ambientGroundColor;
        RenderSettings.ambientMode = AmbientMode.Trilight;
        RenderSettings.ambientSkyColor = sky;
        RenderSettings.ambientEquatorColor = equator;
        RenderSettings.ambientGroundColor = ground;
        SphericalHarmonicsL2 sh = RenderSettings.ambientProbe;
        RenderSettings.ambientSkyColor = s0;
        RenderSettings.ambientEquatorColor = e0;
        RenderSettings.ambientGroundColor = g0;
        RenderSettings.ambientMode = mode;
        string[] a = { "_Replica_SHAr", "_Replica_SHAg", "_Replica_SHAb" }, b = { "_Replica_SHBr", "_Replica_SHBg", "_Replica_SHBb" };
        for (int c = 0; c < 3; c++)
        {
            Shader.SetGlobalVector(a[c], new Vector4(sh[c, 3], sh[c, 1], sh[c, 2], sh[c, 0] - sh[c, 6]));
            Shader.SetGlobalVector(b[c], new Vector4(sh[c, 4], sh[c, 5], sh[c, 6] * 3f, sh[c, 7]));
        }
        Shader.SetGlobalVector("_Replica_SHC", new Vector4(sh[0, 8], sh[1, 8], sh[2, 8], 1f));
    }

    // ---------------------------------------------------------------- lights
    // LightingFeatures.SetupAdditionalLight: first 16 non-directional visible lights;
    // pos = (p, 1); colour = (finalColor.rgb, light.color.a);
    // att = LightInfo.GetPunctualLightDistanceAttenuation(range) (x = 1/max(r^2, 1e-4),
    //       y = -r^2 / (0.64 r^2 - r^2)) + GetSpotAngleAttenuation (z, w; point: 0, 1);
    // spotDir = (localToWorld column 2 = forward, w = 1 / m_ShapeRadius).
    void FillLightTable()
    {
        var cam = CamPos;
        _lights.RemoveAll(l => !l);
        _lights.Sort((a, b) => (a.transform.position - cam).sqrMagnitude.CompareTo((b.transform.position - cam).sqrMagnitude));
        additionalLightCount = Mathf.Min(_lights.Count, MaxLights);
        for (int i = 0; i < MaxLights; i++)
        {
            if (i >= additionalLightCount) { _pos[i] = _col[i] = _dir[i] = Vector4.zero; _att[i] = new Vector4(0, 1, 0, 1); continue; }
            var l = _lights[i];
            Vector3 p = l.transform.position;
            _pos[i] = new Vector4(p.x, p.y, p.z, 1f);
            Vector4 lc = LightColor(l); lc.w = l.color.a;
            _col[i] = lc;
            float r2 = l.range * l.range;
            float z = 0f, w = 1f;
            if (l.type == LightType.Spot)
            {
                float cosOuter = Mathf.Cos(Mathf.Deg2Rad * l.spotAngle * 0.5f);
                float cosInner = Mathf.Cos(Mathf.Deg2Rad * l.innerSpotAngle * 0.5f);
                z = 1f / Mathf.Max(cosInner - cosOuter, 0.001f);
                w = -cosOuter * z;
            }
            _att[i] = new Vector4(1f / Mathf.Max(r2, 1e-4f), -r2 / (r2 * 0.64f - r2), z, w);
            float shape = 0f;
            foreach (var mb in l.GetComponents<MonoBehaviour>())
                if (mb && mb.GetType().Name == "ReplicaAdditionalLightData") shape = Get(mb, "m_ShapeRadius", 0f);
            Vector3 f = l.transform.forward;
            _dir[i] = new Vector4(f.x, f.y, f.z, 1f / shape);
        }
        Shader.SetGlobalVectorArray("SimAdditionalLightPosition", _pos);
        Shader.SetGlobalVectorArray("SimAdditionalLightColor", _col);
        Shader.SetGlobalVectorArray("SimAdditionalLightAttenuation", _att);
        Shader.SetGlobalVectorArray("SimAdditionalLightSpotDir", _dir);
    }

    static void SetKeyword(string k, bool on)
    {
        if (on) Shader.EnableKeyword(k); else Shader.DisableKeyword(k);
    }

    static float IntBits(int i) => BitConverter.Int32BitsToSingle(i);

    // Unity's per-object light lists (native, not in the game's code): up to 4 lights per
    // renderer, strongest at its bounds centre
    readonly List<(float, int)> _scores = new List<(float, int)>();
    readonly Vector4[] _idx = new Vector4[2];

    void AssignObjectLights()
    {
        foreach (var r in _renderers)
        {
            if (!r) continue;
            var b = r.bounds;
            _scores.Clear();
            for (int i = 0; i < additionalLightCount; i++)
            {
                var l = _lights[i];
                float d = Mathf.Sqrt(b.SqrDistance(l.transform.position));
                if (d > l.range) continue;
                _scores.Add((l.intensity / (1f + d * d), i));
            }
            _scores.Sort((a, c) => c.Item1.CompareTo(a.Item1));
            int n = Mathf.Min(4, _scores.Count);
            // the shader reads these with asint(): integer bits, not float values
            _idx[0] = new Vector4(IntBits(n > 0 ? _scores[0].Item2 : 0), IntBits(n > 1 ? _scores[1].Item2 : 0),
                                  IntBits(n > 2 ? _scores[2].Item2 : 0), IntBits(n > 3 ? _scores[3].Item2 : 0));
            _idx[1] = Vector4.zero;
            r.GetPropertyBlock(_mpb);
            _mpb.SetVector("unity_LightData", new Vector4(0, n, 0, 0));
            _mpb.SetVectorArray("unity_LightIndices", _idx);
            r.SetPropertyBlock(_mpb);
        }
    }
}
