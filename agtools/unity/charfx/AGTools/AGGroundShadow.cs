// Character GROUND SHADOW: the game's GroundShadowSystem (UnityEngine.Pipelines.SimPipeline.Character),
// ported from the decompiled GameAssembly.dll (AG_cache/re/decomp_all, decomp_charfx), method by method:
//
//   CharacterEffect.updateShadow @0x28f9c90   register / unregister a character (its _rendererList)
//                                             while it is active and _shadowEnabled; push _shadowHeight
//   GroundShadowSystem.Regist @0x2959350      per character: ShadowData (Projection, sub-mesh counts)
//     / UnRegist @0x295c710                   + ShadowRenderer (128x128 R8 temporary texture)
//     / UpdateShadowGroundHeight @0x295ca40
//   SceneSetting.Update @0x2982680            SceneSetting -> RenderSettings (ground shadow block)
//   RenderSettings.get_groundShadowDir @0x2946df0  (sin/cos of inclination, azimuth)
//   CharacterFeature.Create @0x298c8b0        materials: CharacterProjection, ShadowBlur,
//     + GroundShadowSystem.InitMaterials       ProjectionShadow, DecalShadow (see Shaders below)
//   CharacterFeature.SetupRenderFeature @0x298d1e0 -> GroundShadowSystem.UpdateSettings @0x295c980
//   CharacterFeature.AddRenderPasses @0x298c730  both passes only when RenderSettings.groundShadow,
//                                             never for Preview / Reflection cameras
//   GroundShadowPrePass.Execute @0x299d2a0    (event 250, BeforeRenderingOpaques)
//     -> GroundShadowSystem.RenderIntoQuad @0x2959880 (+ TransBounds @0x295a9e0)
//   GroundShadowPass.Execute @0x299cee0       (event 402, after CopyColor at 401)
//     -> QuadIntoTarget @0x2958e80 (decal off) / NormalReconstruction.SetupProperties @0x39425f0
//        + CubeToTarget @0x2957ef0 (decal on)
//   GroundShadowSystem.get_shadowQuad @0x295d260 / get_shadowCube @0x295cef0 (meshes; index arrays
//     read from global-metadata.dat: quad {0,1,2,0,2,3}, cube 36 indices)
//   GroundShadowSystem.DrawBounds @0x2958510  debug lines (no caller in the game)
//
// How it renders: each character's renderers are drawn with Hidden/SimPipeline/Projection into its
// own 128x128 R8 texture (cleared to white, BlendOp Min) through sim_CharProjMatrix, an oblique
// projection along the ground-shadow direction onto the ground plane, fitted to the character's
// bounds; blurred (CharacterShadowBlur pass 0 then 1); then laid on the ground as a quad (the
// texture's footprint) with Hidden/SimPipeline/CharacterShadow, Blend DstColor Zero (multiply).
// Decal mode instead draws a sheared cube with Hidden/SimPipeline/DecalShadow, which rebuilds the
// world position from _CameraDepthTexture and clips by the reconstructed normal, so the shadow
// follows uneven ground.
//
// Built-in renderer mapping:
//   GroundShadowPrePass (250, before the opaques) -> Graphics.ExecuteCommandBuffer in
//     Camera.onPreCull (as AGSimCharacter's character shadow map): it only fills the per-character
//     textures, so all it needs is to run before the camera's composite.
//   GroundShadowPass (402: after the skybox and after CopyColorPass at 401, before the
//     transparents) -> CameraEvent.BeforeForwardAlpha, kept after AGSimPipeline's opaque grab
//     ("AG grab opaque + depth", same event) so _CameraOpaqueTexture holds no ground shadow, as in
//     the game. AfterForwardOpaque would be before the skybox and before the grab; AfterSkybox
//     does not run without a skybox clear.
//
// Stand-in additions (the game's pipeline sets these itself, the built-in renderer does not):
// unity_MatrixInvVP and _ScaledScreenParams for DecalShadow, and _CameraDepthTexture through
// Camera.depthTextureMode. Static: hooks Camera.onPreCull on load (editor and player).
//
// Shaders (Shader.Find; the game's materials hold nothing the property block does not set):
//   Hidden/SimPipeline/Projection           CharacterFeature.GroundShadowProj  (CharacterProjection)
//   Hidden/SimPipeline/CharacterShadowBlur  CharacterFeature.GroundShadowBlur  (ShadowBlur)
//   Hidden/SimPipeline/CharacterShadow      CharacterFeature.GroundShadow      (ProjectionShadow)
//   Hidden/SimPipeline/DecalShadow          CharacterFeature.DecalShadow       (DecalShadow)
using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

public static class AGGroundShadow
{
    public static bool enabled = true;

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

    /// Rescan the scene and re-register the characters now (batch captures call this after
    /// loading, next to AGSimPipeline.Refresh / AGSimCharacter.Refresh).
    public static void Refresh() { Ensure(); Scan(); ReadSettings(); SyncCharacters(); }

    // ---------------------------------------------------------------- scene data
    static MonoBehaviour _sceneSetting;
    static float _nextScan;

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
        _sceneSetting = FindByTypeName("SceneSetting");
        _nextScan = Time.realtimeSinceStartup + 2f;
    }

    // ---------------------------------------------------------------- RenderSettings (ground shadow)
    // SceneSetting.Update @0x2982680 copies the SceneSetting fields into the pipeline's static
    // RenderSettings: groundShadow (0xFE), decalShadow (0x104), normalClipThreshold (0x108),
    // decalCubeHeight (0x10C), visualizeCube (0x110), groundHeight (0x114) = _groundHeight
    // (+ transform.position.y when _innerScene), inclination / azimuth (0x11C / 0x120) in radians
    // (degrees * 3.1415927 / 180), fade (0x124), colour (0x134), shadowScatter (0x144),
    // scatter colour (0x148), scatter range (0x15C).
    static bool _rsGroundShadow, _rsDecalShadow, _rsScatter;
    static float _rsNormalClipThreshold, _rsDecalCubeHeight, _rsGroundHeight, _rsFade, _rsScatterRange;
    static Vector3 _rsDir;
    static Color _rsColor, _rsScatterColor;

    static void ReadSettings()
    {
        var ss = _sceneSetting;
        _rsGroundShadow = ss && Get<int>(ss, "_groundShadowEnable", 0) != 0;
        if (!ss) return;
        _rsDecalShadow = Get<int>(ss, "_decalShadow", 0) != 0;
        _rsNormalClipThreshold = Get(ss, "_normalClipThreshold", 0f);
        _rsDecalCubeHeight = Get(ss, "_decalCubeHeight", 0f);
        float gh = Get(ss, "_groundHeight", 0f);
        if (Get<int>(ss, "_innerScene", 0) != 0) gh += ss.transform.position.y;
        _rsGroundHeight = gh;
        float azimuth = Get(ss, "_groundShadowAzimuth", 0f) * 3.1415927f / 180f;
        float inclination = Get(ss, "_groundShadowInclination", 0f) * 3.1415927f / 180f;
        _rsFade = Get(ss, "_groundShadowFade", 0f);
        _rsColor = Get(ss, "_groundShadowColor", Color.black);
        _rsScatter = Get<int>(ss, "_shadowScatter", 0) != 0;
        _rsScatterColor = Get(ss, "_shadowScatterColor", Color.clear);
        _rsScatterRange = Get(ss, "_shadowScatterRange", 0f);
        // RenderSettings.get_groundShadowDir @0x2946df0: (cos az sin inc, cos inc, sin az sin inc)
        float si = Mathf.Sin(inclination);
        _rsDir = new Vector3(Mathf.Cos(azimuth) * si, Mathf.Cos(inclination), Mathf.Sin(azimuth) * si);
    }


    // ---------------------------------------------------------------- GroundShadowSystem state
    // ShadowData (type 0x10, rendererList 0x18, subMeshCounts 0x20, heightOffset 0x28, trans 0x30)
    // ShadowRenderer (quadTrans 0x10, texture 0x50, skip 0x58)
    class ShadowData
    {
        public int type;                                    // ShadowType: Spot 0, Projection 1
        public List<CharacterEffect.RendererInfo> rendererList;
        public readonly List<int> subMeshCounts = new List<int>();
        public float heightOffset;
        public Transform trans;
    }

    class ShadowRenderer
    {
        public Matrix4x4 quadTrans;
        public RenderTexture texture;
        public bool skip;
    }

    static readonly Dictionary<int, ShadowData> _shadowDataMap = new Dictionary<int, ShadowData>();
    static readonly Dictionary<int, ShadowRenderer> _shadowRendererMap = new Dictionary<int, ShadowRenderer>();
    static int _index;
    static RenderTexture _blurIntermedia;
    static Material _projectionMaterial, _shadowMaterial, _decalShadowMat, _blurMaterial;
    static Mesh _shadowQuad, _shadowCube;
    static bool _decalMode;
    static float _groundHeight, _decalCubeHeight;
    static Vector3 _shadowDir;
    static readonly MaterialPropertyBlock _block = new MaterialPropertyBlock();

    // CharacterEffect +0x68 (_shadowId) for each registered character
    static readonly Dictionary<CharacterEffect, int> _shadowIds = new Dictionary<CharacterEffect, int>();

    static Material Mat(ref Material m, string shader)
    {
        if (m) return m;
        var s = Shader.Find(shader);
        if (!s) { WarnOnce("AGGroundShadow: shader not found: " + shader); return null; }
        return m = new Material(s) { hideFlags = HideFlags.HideAndDontSave, name = shader };
    }

    static readonly HashSet<string> _warned = new HashSet<string>();
    static void WarnOnce(string msg) { if (_warned.Add(msg)) Debug.LogWarning(msg); }

    // CharacterFeature.Create -> InitMaterials(GroundShadowProj, GroundShadowBlur, GroundShadow, DecalShadow)
    static Material projectionMaterial => Mat(ref _projectionMaterial, "Hidden/SimPipeline/Projection");
    static Material blurMaterial => Mat(ref _blurMaterial, "Hidden/SimPipeline/CharacterShadowBlur");
    static Material shadowMaterial => Mat(ref _shadowMaterial, "Hidden/SimPipeline/CharacterShadow");
    static Material decalShadowMat => Mat(ref _decalShadowMat, "Hidden/SimPipeline/DecalShadow");

    // get_blurIntermedia @0x295cbb0: new RenderTexture(128, 128, 0, (RenderTextureFormat)16 = R8)
    static RenderTexture blurIntermedia =>
        _blurIntermedia ? _blurIntermedia
                        : _blurIntermedia = new RenderTexture(128, 128, 0, RenderTextureFormat.R8)
                              { hideFlags = HideFlags.HideAndDontSave, name = "GroundShadow blurIntermedia" };

    // get_shadowQuad @0x295d260: (-1,0,-1) (-1,0,1) (1,0,1) (1,0,-1), uv (0,0) (0,1) (1,1) (1,0)
    static Mesh shadowQuad
    {
        get
        {
            if (_shadowQuad) return _shadowQuad;
            _shadowQuad = new Mesh { hideFlags = HideFlags.HideAndDontSave, name = "GroundShadow quad" };
            _shadowQuad.SetVertices(new List<Vector3> { new Vector3(-1f, 0f, -1f), new Vector3(-1f, 0f, 1f),
                                                        new Vector3(1f, 0f, 1f), new Vector3(1f, 0f, -1f) });
            _shadowQuad.SetUVs(0, new List<Vector2> { new Vector2(0f, 0f), new Vector2(0f, 1f),
                                                      new Vector2(1f, 1f), new Vector2(1f, 0f) });
            _shadowQuad.SetIndices(new[] { 0, 1, 2, 0, 2, 3 }, MeshTopology.Triangles, 0, false);
            return _shadowQuad;
        }
    }

    // get_shadowCube @0x295cef0: the 8 corners of [-1,1]^3, 36 indices (metadata 0xF04578)
    static Mesh shadowCube
    {
        get
        {
            if (_shadowCube) return _shadowCube;
            _shadowCube = new Mesh { hideFlags = HideFlags.HideAndDontSave, name = "GroundShadow cube" };
            _shadowCube.SetVertices(new[] {
                new Vector3(-1f, -1f, -1f), new Vector3(1f, -1f, -1f), new Vector3(1f, -1f, 1f), new Vector3(-1f, -1f, 1f),
                new Vector3(-1f, 1f, -1f), new Vector3(1f, 1f, -1f), new Vector3(1f, 1f, 1f), new Vector3(-1f, 1f, 1f) });
            _shadowCube.SetIndices(new[] { 0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 3, 6, 2, 3, 7, 6,
                                           0, 1, 5, 0, 5, 4, 0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5 },
                                   MeshTopology.Triangles, 0, false);
            return _shadowCube;
        }
    }

    // UpdateSettings @0x295c980: store the settings, DEPTH_MODE on the projection material in decal mode
    static void UpdateSettings(bool decalMode, float groundHeight, Vector3 shadowDir, float decalCubeHeight)
    {
        _shadowDir = shadowDir;
        _decalCubeHeight = decalCubeHeight;
        _groundHeight = groundHeight;
        _decalMode = decalMode;
        var m = projectionMaterial;
        if (!m) return;
        if (decalMode) m.EnableKeyword("DEPTH_MODE"); else m.DisableKeyword("DEPTH_MODE");
    }

    // IsValid @0x2958e10
    static bool IsValid(int id) => id >= 0 && _shadowDataMap.ContainsKey(id);

    // Regist @0x2959350
    static int Regist(List<CharacterEffect.RendererInfo> renderList, Transform transform)
    {
        var data = new ShadowData { heightOffset = 0f, rendererList = renderList, type = 1, trans = transform };
        _shadowDataMap[_index] = data;
        var sr = new ShadowRenderer { texture = RenderTexture.GetTemporary(128, 128, 0, RenderTextureFormat.R8) };
        _shadowRendererMap[_index] = sr;
        data.subMeshCounts.Clear();
        for (int i = 0; renderList != null && i < renderList.Count; i++)
        {
            var info = renderList[i];
            if (info == null) break;                          // the game stops on a null entry
            var smr = info.renderer as SkinnedMeshRenderer;
            data.subMeshCounts.Add(1);
            if (!info.renderer)
            {
                Debug.LogError(info.rendererName + " Miss Character Shadow Renderer");
                continue;
            }
            if (info.updateBounds != 0 && smr) smr.updateWhenOffscreen = true;
            if (smr && smr.sharedMesh) { data.subMeshCounts[i] = smr.sharedMesh.subMeshCount; continue; }
            var mf = info.renderer.GetComponent<MeshFilter>();
            if (mf && mf.sharedMesh) data.subMeshCounts[i] = mf.sharedMesh.subMeshCount;
        }
        return _index++;
    }

    // UnRegist @0x295c710
    static void UnRegist(int id)
    {
        if (!_shadowDataMap.TryGetValue(id, out var data)) return;
        if (data.rendererList != null)
            foreach (var info in data.rendererList)
                if (info != null && info.updateBounds != 0 && info.renderer is SkinnedMeshRenderer smr && smr)
                    smr.updateWhenOffscreen = false;
        _shadowDataMap.Remove(id);
        if (_shadowRendererMap.TryGetValue(id, out var sr))
        {
            if (sr.texture) { RenderTexture.ReleaseTemporary(sr.texture); sr.texture = null; }
            _shadowRendererMap.Remove(id);
        }
    }

    // UpdateShadowGroundHeight @0x295ca40
    static void UpdateShadowGroundHeight(int id, float height)
    {
        if (_shadowDataMap.TryGetValue(id, out var data)) data.heightOffset = height;
    }

    // CharacterEffect.updateShadow @0x28f9c90, for every character: isActiveAndEnabled && _shadowEnabled
    // (+0x78) -> Regist(_rendererList (+0x70), transform) unless IsValid(_shadowId), then
    // UpdateShadowGroundHeight(_shadowId, _shadowHeight (+0x7C)); otherwise UnRegist and _shadowId = -1.
    // The game calls it on enable/disable and when the shadow fields change; here every frame.
    static readonly List<CharacterEffect> _drop = new List<CharacterEffect>();

    static void SyncCharacters()
    {
        _drop.Clear();
        foreach (var kv in _shadowIds)
            if (!kv.Key || !kv.Key.isActiveAndEnabled || kv.Key._shadowEnabled == 0) _drop.Add(kv.Key);
        foreach (var ce in _drop)
        {
            UnRegist(_shadowIds[ce]);
            _shadowIds.Remove(ce);
        }
        foreach (var ce in CharacterEffect.Active)
        {
            if (!ce || !ce.isActiveAndEnabled || ce._shadowEnabled == 0) continue;
            if (!_shadowIds.TryGetValue(ce, out int id) || !IsValid(id))
                _shadowIds[ce] = id = Regist(ce._rendererList, ce.transform);
            UpdateShadowGroundHeight(id, ce._shadowHeight);
        }
    }

    // ---------------------------------------------------------------- per camera
    static void OnPreCullCamera(Camera cam)
    {
        if (!cam) return;
        // AddRenderPasses: nothing for Preview (4) and Reflection (16) cameras
        if (cam.cameraType == CameraType.Preview || cam.cameraType == CameraType.Reflection) return;
        if (Time.realtimeSinceStartup > _nextScan) Scan();
        ReadSettings();
        SyncCharacters();
        var cb = CompositeBuffer(cam);
        cb.Clear();
        if (!enabled || !_rsGroundShadow) return;
        // CharacterFeature.SetupRenderFeature @0x298d1e0
        UpdateSettings(_rsDecalShadow, _rsGroundHeight, _rsDir, _rsDecalCubeHeight);
        if (!projectionMaterial || !blurMaterial) return;

        // GroundShadowPrePass.Execute (event 250)
        _preCb ??= new CommandBuffer { name = "AG ground shadow (GroundShadowPrePass)" };
        _preCb.Clear();
        RenderIntoQuad(_preCb);
        Graphics.ExecuteCommandBuffer(_preCb);

        // GroundShadowPass.Execute (event 402): camera matrices, then the quads or the decal cubes
        cb.SetViewProjectionMatrices(cam.worldToCameraMatrix, cam.projectionMatrix);
        if (!_decalMode)
        {
            if (shadowMaterial) QuadIntoTarget(cb);
        }
        else if (decalShadowMat)
        {
            cam.depthTextureMode |= DepthTextureMode.Depth;   // _CameraDepthTexture (the game's CopyDepthPass)
            SetupNormalReconstruction(cb, cam);
            // the game's pipeline provides these to every shader; the built-in renderer does not
            int w = cam.pixelWidth, h = cam.pixelHeight;
            cb.SetGlobalVector("_ScaledScreenParams", new Vector4(w, h, 1f + 1f / w, 1f + 1f / h));
            // intermediate (flipped) target, as the game's camera colour target
            cb.SetGlobalMatrix("unity_MatrixInvVP",
                (GL.GetGPUProjectionMatrix(cam.projectionMatrix, true) * cam.worldToCameraMatrix).inverse);
            CubeToTarget(cb);
        }
    }

    static CommandBuffer _preCb;
    const string PassName = "AG ground shadow (GroundShadowPass)";
    const string GrabName = "AG grab opaque + depth";   // AGSimPipeline.EnsureGrab (CopyColorPass, event 401)
    static readonly Dictionary<Camera, CommandBuffer> _passes = new Dictionary<Camera, CommandBuffer>();

    static CommandBuffer CompositeBuffer(Camera cam)
    {
        if (!_passes.TryGetValue(cam, out var cb) || cb == null)
        {
            foreach (var b in cam.GetCommandBuffers(CameraEvent.BeforeForwardAlpha))
                if (b.name == PassName) { cb = b; break; }
            if (cb == null)
            {
                cb = new CommandBuffer { name = PassName };
                cam.AddCommandBuffer(CameraEvent.BeforeForwardAlpha, cb);
            }
            _passes[cam] = cb;
        }
        // keep it after the opaque grab: 402 runs after 401
        int mine = -1, grab = -1;
        var list = cam.GetCommandBuffers(CameraEvent.BeforeForwardAlpha);
        for (int i = 0; i < list.Length; i++)
        {
            if (list[i].name == PassName) mine = i;
            else if (list[i].name == GrabName) grab = i;
        }
        if (mine < 0 || grab > mine)
        {
            if (mine >= 0) cam.RemoveCommandBuffer(CameraEvent.BeforeForwardAlpha, cb);
            cam.AddCommandBuffer(CameraEvent.BeforeForwardAlpha, cb);
        }
        return cb;
    }

    // ---------------------------------------------------------------- RenderIntoQuad @0x2959880
    static void RenderIntoQuad(CommandBuffer cmd)
    {
        foreach (var kv in _shadowDataMap)
        {
            var data = kv.Value;
            if (!_shadowRendererMap.TryGetValue(kv.Key, out var sr)) continue;
            sr.skip = true;
            if (data.type != 1) continue;
            var list = data.rendererList;
            if (list == null || list.Count == 0 || list[0] == null) continue;   // the game throws here
            if (!list[0].renderer)
            {
                Debug.LogWarning(list[0].rendererName + "Missing Skinned Renderer");
                continue;
            }
            // the character's bounds: the first renderer's centre, every enabled renderer encapsulated
            var b = new Bounds(list[0].renderer.bounds.center, Vector3.zero);
            for (int i = 0; i < list.Count; i++)
            {
                var r = list[i]?.renderer;
                if (!r)
                {
                    Debug.LogWarning(list[i]?.rendererName + "Missing Skinned Renderer");
                    continue;
                }
                if (r.enabled) b.Encapsulate(r.bounds);
            }
            Vector3 size = b.size;
            if ((size - Vector3.zero).sqrMagnitude < 9.9999994e-11f) continue;
            sr.skip = false;
            if (!sr.texture) continue;
            sr.texture.DiscardContents();
            cmd.SetRenderTarget(sr.texture);
            cmd.ClearRenderTarget(false, true, new Color(1f, 1f, 1f, 1f));

            Vector3 dir = _shadowDir;
            float fade = _rsFade;                               // RenderSettings.groundShadowFade
            // oblique projection along dir onto y = 0: column 1 = (-dir.x / dir.y, 1, -dir.z / dir.y, 0)
            Matrix4x4 m = Matrix4x4.identity;
            m.m01 = -dir.x / dir.y;
            m.m21 = -dir.z / dir.y;
            // height clamp: min(size.y, 1 / min(fade, 0.01)) (minss in the binary, not max)
            float h = size.y;
            float lim = Mathf.Min(fade, 0.01f);
            if (1f / lim <= h) h = 1f / lim;
            Vector3 bmin = b.min, bmax = b.max;
            b.SetMinMax(bmin, new Vector3(bmax.x, bmin.y + h, bmax.z));

            Bounds tb = TransBounds(b, ref m);
            float g = data.heightOffset + _groundHeight;
            float ix = 1f / tb.extents.x;
            m.m00 *= ix; m.m01 *= ix; m.m02 *= ix;
            m.m13 = -g;
            m.m03 = -tb.center.x * ix;
            float iz = 1f / tb.extents.z;
            m.m20 *= iz; m.m21 *= iz; m.m22 *= iz;
            m.m23 = -tb.center.z * iz;

            // the quad (or decal cube) on the ground under the footprint, sheared along dir
            float H = _decalMode ? _decalCubeHeight : tb.extents.y;
            var q = Matrix4x4.identity;
            q.m00 = tb.extents.x;
            q.m01 = dir.x / dir.y * H; q.m11 = H; q.m21 = dir.z / dir.y * H;
            q.m22 = tb.extents.z;
            q.m03 = dir.x * g / dir.y + tb.center.x; q.m13 = g; q.m23 = dir.z * g / dir.y + tb.center.z;
            sr.quadTrans = q;

            cmd.SetGlobalMatrix("sim_CharProjMatrix", m);
            cmd.SetGlobalFloat("sim_CharProjFade", fade);
            cmd.SetGlobalFloat("_InvDecalHeight", 1f / _decalCubeHeight);
            var proj = projectionMaterial;
            for (int i = 0; i < list.Count; i++)
            {
                var r = list[i]?.renderer;
                if (!r || !r.enabled || !r.gameObject.activeInHierarchy) continue;
                int n = i < data.subMeshCounts.Count ? data.subMeshCounts[i] : 1;
                for (int s = 0; s < n; s++) cmd.DrawRenderer(r, proj, s);   // shaderPass -1: every pass
            }
        }
        // (UnRegist(-1): no-op)
        if (_decalMode) return;
        // blur: CharacterShadowBlur pass 0 (horizontal) into blurIntermedia, pass 1 (vertical) back
        var blur = blurMaterial;
        foreach (var sr in _shadowRendererMap.Values)
        {
            if (sr.skip || !sr.texture) continue;
            blurIntermedia.DiscardContents();
            cmd.Blit(sr.texture, blurIntermedia, blur, 0);
            sr.texture.DiscardContents();
            cmd.Blit(blurIntermedia, sr.texture, blur, 1);
        }
    }

    // TransBounds @0x295a9e0: the axis-aligned bounds of the 8 corners through trans (MultiplyPoint3x4),
    // in the game's corner order and with Bounds.min / Bounds.max setters as it uses them.
    static Bounds TransBounds(Bounds bounds, ref Matrix4x4 trans)
    {
        Vector3 mn = bounds.min, mx = bounds.max;
        var r = bounds;
        Vector3 p = trans.MultiplyPoint3x4(mn);
        r.max = p;
        r.min = p;
        Vector3[] corners = {
            new Vector3(mx.x, mn.y, mn.z), new Vector3(mn.x, mx.y, mn.z), new Vector3(mn.x, mn.y, mx.z),
            new Vector3(mx.x, mx.y, mx.z), new Vector3(mn.x, mx.y, mx.z), new Vector3(mx.x, mn.y, mx.z),
            new Vector3(mx.x, mx.y, mn.z) };
        foreach (var c in corners)
        {
            p = trans.MultiplyPoint3x4(c);
            r.min = Vector3.Min(r.min, p);
            r.max = Vector3.Max(r.max, p);
        }
        return r;
    }

    // ---------------------------------------------------------------- QuadIntoTarget @0x2958e80
    // per unskipped character: _MainTex = its texture, _Color = groundShadowColor, _EnableShadowScatter,
    // _ShadowScatterColor, _ShadowScatterRange; DrawMesh(shadowQuad, quadTrans, shadowMaterial, 0, 0)
    static void QuadIntoTarget(CommandBuffer cmd)
    {
        foreach (var sr in _shadowRendererMap.Values)
        {
            if (sr.skip || !sr.texture) continue;
            _block.SetTexture("_MainTex", sr.texture);
            _block.SetColor("_Color", _rsColor);
            _block.SetFloat("_EnableShadowScatter", _rsScatter ? 1f : 0f);
            _block.SetColor("_ShadowScatterColor", _rsScatterColor);
            _block.SetFloat("_ShadowScatterRange", _rsScatterRange);
            cmd.DrawMesh(shadowQuad, sr.quadTrans, shadowMaterial, 0, 0, _block);
        }
    }

    // ---------------------------------------------------------------- CubeToTarget @0x2957ef0
    // as QuadIntoTarget, plus _ShadowHeight = heightOffset + groundHeight, _ShadowDir = (dir, 0),
    // _NormalClipThreshold; DrawMesh(shadowCube, quadTrans, decalShadowMat, 0, 0)
    static void CubeToTarget(CommandBuffer cmd)
    {
        foreach (var kv in _shadowRendererMap)
        {
            var sr = kv.Value;
            if (sr.skip || !sr.texture) continue;
            if (!_shadowDataMap.TryGetValue(kv.Key, out var data)) continue;
            _block.SetTexture("_MainTex", sr.texture);
            _block.SetColor("_Color", _rsColor);
            _block.SetFloat("_EnableShadowScatter", _rsScatter ? 1f : 0f);
            _block.SetColor("_ShadowScatterColor", _rsScatterColor);
            _block.SetFloat("_ShadowScatterRange", _rsScatterRange);
            _block.SetFloat("_ShadowHeight", data.heightOffset + _groundHeight);
            _block.SetVector("_ShadowDir", new Vector4(_shadowDir.x, _shadowDir.y, _shadowDir.z, 0f));
            _block.SetFloat("_NormalClipThreshold", _rsNormalClipThreshold);
            cmd.DrawMesh(shadowCube, sr.quadTrans, decalShadowMat, 0, 0, _block);
        }
    }

    // NormalReconstruction.SetupProperties @0x39425f0: _NormalReconstructionMatrix =
    // inverse(projection * view with its translation column set to (0, 0, 0, 1)) (CPU projection)
    static void SetupNormalReconstruction(CommandBuffer cmd, Camera cam)
    {
        var view = cam.worldToCameraMatrix;
        view.SetColumn(3, new Vector4(0f, 0f, 0f, 1f));
        cmd.SetGlobalMatrix("_NormalReconstructionMatrix", (cam.projectionMatrix * view).inverse);
    }

    // ---------------------------------------------------------------- DrawBounds @0x2958510
    /// Debug.DrawLine of a box's 12 edges in the game's colours (no caller in the game).
    public static void DrawBounds(Bounds b, float delay)
    {
        Vector3 c = b.center, e = b.extents;
        var p0 = new Vector3(c.x - e.x, c.y - e.y, c.z - e.z); var p1 = new Vector3(c.x + e.x, c.y - e.y, c.z - e.z);
        var p2 = new Vector3(c.x + e.x, c.y - e.y, c.z + e.z); var p3 = new Vector3(c.x - e.x, c.y - e.y, c.z + e.z);
        var q0 = new Vector3(c.x - e.x, c.y + e.y, c.z - e.z); var q1 = new Vector3(c.x + e.x, c.y + e.y, c.z - e.z);
        var q2 = new Vector3(c.x + e.x, c.y + e.y, c.z + e.z); var q3 = new Vector3(c.x - e.x, c.y + e.y, c.z + e.z);
        var yellow = new Color(1f, 0.92156863f, 0.015686275f, 1f);
        Debug.DrawLine(p0, p1, Color.blue, delay); Debug.DrawLine(p1, p2, Color.red, delay);
        Debug.DrawLine(p2, p3, yellow, delay); Debug.DrawLine(p3, p0, Color.magenta, delay);
        Debug.DrawLine(q0, q1, Color.blue, delay); Debug.DrawLine(q1, q2, Color.red, delay);
        Debug.DrawLine(q2, q3, yellow, delay); Debug.DrawLine(q3, q0, Color.magenta, delay);
        Debug.DrawLine(p0, q0, Color.white, delay); Debug.DrawLine(p1, q1, new Color(0.5f, 0.5f, 0.5f, 1f), delay);
        Debug.DrawLine(p2, q2, Color.green, delay); Debug.DrawLine(p3, q3, Color.cyan, delay);
    }
}
