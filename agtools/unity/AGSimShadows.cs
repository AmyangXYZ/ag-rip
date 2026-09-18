// Main-light cascaded shadows for the decompiled game shaders, in the built-in renderer.
//
// The game's receivers are URP's code: cascade picked by squared distance to four
// split spheres (_CascadeShadowSplitSpheres0..3 / _CascadeShadowSplitSphereRadii),
// _MainLightWorldToShadow[cascade] into a 2x2 atlas, hardware compare sampling,
// 4-tap soft filter when _MainLightShadowParams.y == 1 (_MainLightShadowOffset0/1),
// strength in _MainLightShadowParams.x. The casters are the game's own SHADOWCASTER
// passes (normal bias sim_ShadowBias.y scaled by 1-N.L, depth bias .x along
// sim_ShadowLightDirection, reversed-Z clamp). Cascade settings come from the
// scene's CascadeShadowSetting volume component.
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;
using UnityEngine.Rendering;

public static class AGSimShadows
{
    public static int resolution = 2048;


    static RenderTexture _atlas;
    static CommandBuffer _cb;
    static readonly Matrix4x4[] _w2s = new Matrix4x4[5];
    static readonly Dictionary<Shader, int> _casterPass = new Dictionary<Shader, int>();
    static readonly ShaderTagId LightMode = new ShaderTagId("LightMode");

    public struct Settings
    {
        public bool enabled; public float maxDistance; public int count; public Vector3 splits;
    }

    static float Param(object comp, string field, float fallback)
    {
        var f = comp.GetType().GetField(field, BindingFlags.Public | BindingFlags.Instance);
        var p = f?.GetValue(comp);
        var v = p?.GetType().GetField("m_Value", BindingFlags.Public | BindingFlags.Instance)?.GetValue(p);
        try { return v == null ? fallback : System.Convert.ToSingle(v); } catch { return fallback; }
    }

    /// The scene's CascadeShadowSetting (Replica volume component); URP-like defaults otherwise.
    public static Settings Read()
    {
        var s = new Settings { enabled = true, maxDistance = 50f, count = 4, splits = new Vector3(0.067f, 0.2f, 0.467f) };
        foreach (var so in Resources.FindObjectsOfTypeAll<ScriptableObject>())
        {
            if (so.GetType().Name != "CascadeShadowSetting") continue;
            s.enabled = Param(so, "castCastShadow", 1f) != 0f;
            s.maxDistance = Param(so, "maxShadowDistance", 50f);
            s.count = Mathf.Clamp((int)Param(so, "cascadeShadowSplitCount", 4f), 1, 4);
            s.splits = new Vector3(Param(so, "cascadeShadowSplit0", 0.067f), Param(so, "cascadeShadowSplit1", 0.2f),
                                   Param(so, "cascadeShadowSplit2", 0.467f));
            break;
        }
        return s;
    }

    static int CasterPass(Material m)
    {
        if (!m || !m.shader) return -1;
        if (_casterPass.TryGetValue(m.shader, out int p)) return p;
        p = -1;
        for (int i = 0; i < m.shader.passCount; i++)
            if (string.Equals(m.shader.FindPassTagValue(i, LightMode).name, "SHADOWCASTER", System.StringComparison.OrdinalIgnoreCase)) { p = i; break; }
        _casterPass[m.shader] = p;
        return p;
    }

    static Renderer[] _casterSrc;
    static readonly List<Renderer> _casters = new List<Renderer>();

    /// Renderers that cast, as the game's pipeline would see them: one LOD level per
    /// LODGroup (LOD0 - every level casting at once over-darkens), and never the
    /// enclosing giants (sky domes, backdrop shells) - x305's 333 m "sky_01" uses the
    /// Standard shader with casting on and would otherwise shadow the whole stage.
    static List<Renderer> Casters(Renderer[] renderers, float maxDistance)
    {
        if (_casterSrc == renderers) return _casters;
        _casterSrc = renderers;
        _casters.Clear();
        var skipLod = new HashSet<Renderer>();
        foreach (var g in Object.FindObjectsOfType<LODGroup>())
        {
            var lods = g.GetLODs();
            for (int i = 1; i < lods.Length; i++)
                foreach (var lr in lods[i].renderers) if (lr) skipLod.Add(lr);
        }
        float giant = Mathf.Max(100f, maxDistance * 4f);
        foreach (var rnd in renderers)
        {
            if (!rnd || !rnd.enabled || rnd.shadowCastingMode == ShadowCastingMode.Off || !rnd.gameObject.activeInHierarchy) continue;
            if (skipLod.Contains(rnd) || rnd.bounds.size.magnitude > giant) continue;
            _casters.Add(rnd);
        }
        return _casters;
    }

    /// Render the atlas for `cam` and publish the receiver globals. Returns false
    /// (and the caller should disable MAIN_LIGHT_SHADOWS) when there is nothing to do.
    public static bool Render(Camera cam, Light light, Renderer[] renderers, Settings s, bool soft)
    {
        if (!light || !s.enabled || light.shadows == LightShadows.None) return false;
        if (!_atlas || _atlas.width != resolution)
        {
            if (_atlas) _atlas.Release();
            _atlas = new RenderTexture(resolution, resolution, 24, RenderTextureFormat.Shadowmap)
                { filterMode = FilterMode.Bilinear, hideFlags = HideFlags.HideAndDontSave, name = "AGMainLightShadowmap" };
        }
        _cb ??= new CommandBuffer { name = "AG main light shadows" };
        _cb.Clear();
        _cb.SetRenderTarget(_atlas);
        // Depth is reversed-Z on D3D (Unity converts the projection below) and the
        // Shadowmap sampler compares greater-equal to match - measured, and exactly what
        // URP's receiver math expects. ClearRenderTarget takes depth on the conventional
        // scale (1 = far) and converts it, so the default is the right "far" clear.
        _cb.ClearRenderTarget(true, true, Color.black);

        int tiles = s.count > 1 ? 2 : 1, tile = resolution / tiles;
        float[] fr = { 0, s.splits.x, s.splits.y, s.splits.z, 1 };
        if (s.count < 4) fr[s.count] = 1;
        Vector3 L = -light.transform.forward;                  // towards the light
        var rot = Quaternion.LookRotation(light.transform.forward, Mathf.Abs(Vector3.Dot(light.transform.forward, Vector3.up)) > 0.99f ? Vector3.forward : Vector3.up);
        var spheres = new Vector4[4];
        float farClip = Mathf.Min(s.maxDistance, cam.farClipPlane);

        for (int c = 0; c < 4; c++)
        {
            if (c >= s.count) { spheres[c] = new Vector4(0, 0, 0, -1); continue; }
            float n = Mathf.Max(cam.nearClipPlane, fr[c] * farClip), f = fr[c + 1] * farClip;
            // bounding sphere of the camera-frustum slice
            var corners = new List<Vector3>();
            foreach (float d in new[] { n, f })
                for (int x = 0; x < 2; x++) for (int y = 0; y < 2; y++)
                    corners.Add(cam.ViewportToWorldPoint(new Vector3(x, y, d)));
            Vector3 center = Vector3.zero;
            foreach (var p in corners) center += p;
            center /= corners.Count;
            float r = 0;
            foreach (var p in corners) r = Mathf.Max(r, (p - center).magnitude);
            r = Mathf.Ceil(r * 16f) / 16f;
            // snap to shadow texels so the map doesn't swim
            float texel = 2f * r / tile;
            var ls = Quaternion.Inverse(rot) * center;
            ls.x = Mathf.Floor(ls.x / texel) * texel; ls.y = Mathf.Floor(ls.y / texel) * texel;
            center = rot * ls;
            spheres[c] = new Vector4(center.x, center.y, center.z, r * r);

            float back = r + 200f;                              // casters up-light of the slice
            var view = Matrix4x4.TRS(center - light.transform.forward * back, rot, new Vector3(1, 1, -1)).inverse;
            var proj = Matrix4x4.Ortho(-r, r, -r, r, 0.1f, back + r);
            var tileRect = new Rect((c % tiles) * tile, (c / tiles) * tile, tile, tile);

            _cb.SetViewport(tileRect);
            // Unity converts this projection for the GPU itself (reversed-Z on D3D).
            _cb.SetViewProjectionMatrices(view, proj);
            float depthBias = light.shadowBias * texel, normalBias = -light.shadowNormalBias * texel;
            if (soft) { depthBias *= 2.5f; normalBias *= 2.5f; }
            _cb.SetGlobalVector("sim_ShadowBias", new Vector4(depthBias, normalBias, 0, 0));
            _cb.SetGlobalVector("sim_ShadowLightDirection", L);
            foreach (var rnd in Casters(renderers, s.maxDistance))
            {
                if ((rnd.bounds.center - center).magnitude - rnd.bounds.extents.magnitude > r + back) continue;
                var mats = rnd.sharedMaterials;
                for (int sm = 0; sm < mats.Length; sm++)
                {
                    int pass = CasterPass(mats[sm]);
                    if (pass >= 0) _cb.DrawRenderer(rnd, mats[sm], sm, pass);
                }
            }

            // world -> atlas texel, URP's GetShadowTransform + tile offset
            // URP: raw projection, Z row negated on reversed-Z platforms, then scale/bias
            var pz = proj;
            if (SystemInfo.usesReversedZBuffer) { pz.m20 = -pz.m20; pz.m21 = -pz.m21; pz.m22 = -pz.m22; pz.m23 = -pz.m23; }
            var m = pz * view;
            var tex = Matrix4x4.identity;
            tex.m00 = 0.5f; tex.m11 = 0.5f; tex.m22 = 0.5f; tex.m03 = 0.5f; tex.m13 = 0.5f; tex.m23 = 0.5f;

            m = tex * m;
            var slice = Matrix4x4.identity;
            float inv = 1f / tiles;
            slice.m00 = inv; slice.m11 = inv;
            slice.m03 = (c % tiles) * inv; slice.m13 = (c / tiles) * inv;
            _w2s[c] = slice * m;
        }
        // restore the camera for the frame that follows
        _cb.SetViewProjectionMatrices(cam.worldToCameraMatrix, cam.projectionMatrix);
        Graphics.ExecuteCommandBuffer(_cb);

        for (int c = s.count; c < 5; c++) _w2s[c] = Matrix4x4.zero;
        Shader.SetGlobalTexture("_MainLightShadowmapTexture", _atlas);
        Shader.SetGlobalMatrixArray("_MainLightWorldToShadow", _w2s);
        Shader.SetGlobalVector("_CascadeShadowSplitSpheres0", spheres[0]);
        Shader.SetGlobalVector("_CascadeShadowSplitSpheres1", spheres[1]);
        Shader.SetGlobalVector("_CascadeShadowSplitSpheres2", spheres[2]);
        Shader.SetGlobalVector("_CascadeShadowSplitSpheres3", spheres[3]);
        Shader.SetGlobalVector("_CascadeShadowSplitSphereRadii", new Vector4(spheres[0].w, spheres[1].w, spheres[2].w, spheres[3].w));
        float h = 0.5f / resolution;
        Shader.SetGlobalVector("_MainLightShadowOffset0", new Vector4(-h, -h, h, -h));
        Shader.SetGlobalVector("_MainLightShadowOffset1", new Vector4(-h, h, h, h));
        Shader.SetGlobalVector("_MainLightShadowmapSize", new Vector4(1f / resolution, 1f / resolution, resolution, resolution));
        Shader.SetGlobalVector("_MainLightShadowParams", new Vector4(light.shadowStrength, soft ? 1f : 0f, 0, 0));
        return true;
    }
}
