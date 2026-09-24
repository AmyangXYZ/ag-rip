// The game's end-of-frame, rebuilt around its own decompiled shaders:
//   Hidden/SimPipeline/PostProcessing/Bloom  - URP-style bloom (prefilter, H/V blur, upsample)
//   Hidden/SimPipeline/Final                 - bloom add, filmic/ACES curve, vignette, LUT
//   Hidden/RenderPipeline/Lut                - the colour-grading LUT, baked every frame
// Parameters come from the scene's SceneSetting (tonemapping, exposure, contrast,
// threshold) and the volume stack (grading). Bloom scatter/knee are not stored in the
// scene - BloomPass: scatter 0.77, clamp 100, knee = threshold * 0.5 (decompiled).
using System.Reflection;
using UnityEngine;

[ExecuteAlways]
[ImageEffectAllowedInSceneView]
[RequireComponent(typeof(Camera))]
public class AGSimPostFX : MonoBehaviour
{
    public bool bloom = true;
    [Range(0, 1)] public float bloomScatter = 0.77f;   // BloomPass: 0.77
    public float bloomClamp = 100f;   // BloomPass: 100
    [Tooltip("Knee as a fraction of the threshold (URP: 0.5).")]
    public float bloomKnee = 0.5f;
    public bool finalPass = true;
    [Tooltip("_ACES_TONEMAP: 0 = the game's exposure/contrast curve, 1 = ACES.")]
    [Range(0, 1)] public float aces = 0f;
    [Tooltip("Flip for the fullscreen triangle; -1 on D3D-style UV conventions.")]
    public float yFlip = 0f;   // 0 = auto

    Material _bloom, _final, _lutBuilder;
    RenderTexture _lut;
    Texture2D _curveIdentity, _curveHalf;
    readonly RenderTexture[] _down = new RenderTexture[16], _up = new RenderTexture[16];

    static MonoBehaviour SceneSetting()
    {
        var p = FindObjectOfType<AGSimPipeline>();
        return p ? p.sceneSetting : null;
    }

    static T Get<T>(object o, string field, T fallback)
    {
        if (o == null) return fallback;
        var f = o.GetType().GetField(field, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        if (f == null) return fallback;
        try { return (T)System.Convert.ChangeType(f.GetValue(o), typeof(T)); } catch { return fallback; }
    }

    static Material Mat(ref Material m, string shader)
    {
        if (m) return m;
        var s = Shader.Find(shader);
        return s ? (m = new Material(s) { hideFlags = HideFlags.HideAndDontSave }) : null;
    }

    static void Draw(Texture src, RenderTexture dst, Material mat, int pass)
    {
        mat.SetTexture("_MainTex", src);
        Graphics.SetRenderTarget(dst);
        mat.SetPass(pass);
        Graphics.DrawProceduralNow(MeshTopology.Triangles, 3);
    }

    static Vector3 CamPos => Camera.main ? Camera.main.transform.position : Vector3.zero;
    static T Vol<T>(string c, string p, T fallback) => AGVolumes.Get(c, p, fallback, CamPos);

    // ReplicaExt.BloomPass.Execute + FinalPass.SetMaterialParams (decompiled):
    //   bloom runs when PostProcessSetting.contrast > 0; _Params = (0.77, 100, threshold, threshold*0.5),
    //   threshold as authored (no gamma conversion); Final: tonemapping = SceneSetting._tonemapping,
    //   exposure / contrast from PostProcessSetting (volume-resolved), ACES when ColorAdjustments.mode == 1
    //   (exposure = 2^postExposure), invert/grayness/darkness -> EXTRA_POSTEFFECT.
    void OnRenderImage(RenderTexture src, RenderTexture dst)
    {
        var ss = SceneSetting();
        var final = finalPass ? Mat(ref _final, "Hidden/SimPipeline/Final") : null;
        if (!final) { Graphics.Blit(src, dst); return; }

        float contrast = Vol("PostProcessSetting", "contrast", 1f);
        float exposure = Vol("PostProcessSetting", "exporsure", 1f);
        Texture bloomTex = Texture2D.blackTexture;
        RenderTexture bloomRT = null;
        if (bloom && contrast > 0f && Mat(ref _bloom, "Hidden/SimPipeline/PostProcessing/Bloom"))
            bloomTex = bloomRT = Bloom(src, Vol("PostProcessSetting", "threshold", 1f));
        Shader.SetGlobalTexture("SimPipelineBloom", bloomTex);

        final.SetFloat("tonemapping", Get<int>(ss, "_tonemapping", 1) != 0 ? 1f : 0f);
        final.SetFloat("contrast", contrast);
        bool acesMode = Vol("ColorAdjustments", "mode", 0) == 1;
        final.SetFloat("_ACES_TONEMAP", acesMode ? 1f : aces);
        final.SetFloat("exposure", acesMode ? Mathf.Pow(2f, Vol("ColorAdjustments", "postExposure", 0f)) : exposure);
        bool invert = Vol("PostProcessSetting", "invert", false);
        float grayness = Vol("PostProcessSetting", "grayness", 0f), darkness = Vol("PostProcessSetting", "darkness", 0f);
        final.SetFloat("_invert", invert ? 1f : 0f);
        final.SetFloat("_Grayness", grayness);
        final.SetFloat("_Darkness", darkness);
        if (grayness > 0f || darkness > 0f || invert) final.EnableKeyword("EXTRA_POSTEFFECT");
        else final.DisableKeyword("EXTRA_POSTEFFECT");
        final.SetFloat("_VignetteEnable", 0f);
        final.SetFloat("_HasGobalDistortionTexture", 0f);
        final.SetFloat("_YFlip", yFlip != 0 ? yFlip : (dst == null && SystemInfo.graphicsUVStartsAtTop ? -1f : 1f));
        var lut = Lut();
        Shader.SetGlobalTexture("_ColorGraddingLut", lut);
        final.SetTexture("_ColorGraddingLut", lut);
        Draw(src, dst, final, 0);

        if (bloomRT) RenderTexture.ReleaseTemporary(bloomRT);
    }

    RenderTexture Bloom(RenderTexture src, float threshold)
    {
        int w = Mathf.Max(1, src.width / 2), h = Mathf.Max(1, src.height / 2);
        int mips = Mathf.Clamp(Mathf.FloorToInt(Mathf.Log(Mathf.Max(w, h), 2f) - 1), 1, 16);
        _bloom.SetVector("_Params", new Vector4(bloomScatter, bloomClamp, threshold, threshold * bloomKnee));
        var fmt = RenderTextureFormat.ARGBHalf;
        for (int i = 0; i < mips; i++)
        {
            _down[i] = RenderTexture.GetTemporary(w, h, 0, fmt);
            _up[i] = RenderTexture.GetTemporary(w, h, 0, fmt);
            _down[i].filterMode = _up[i].filterMode = FilterMode.Bilinear;
            w = Mathf.Max(1, w / 2); h = Mathf.Max(1, h / 2);
        }
        Draw(src, _down[0], _bloom, 0);
        for (int i = 1; i < mips; i++)
        {
            Draw(_down[i - 1], _up[i], _bloom, 1);
            Draw(_up[i], _down[i], _bloom, 2);
        }
        for (int i = mips - 2; i >= 0; i--)
        {
            var low = i == mips - 2 ? _down[i + 1] : _up[i + 1];
            _bloom.SetTexture("_MainTexLowMip", low);
            Draw(_down[i], _up[i], _bloom, 3);
        }
        var result = _up[0];
        if (mips == 1) result = _down[0];
        for (int i = 0; i < mips; i++)
        {
            if (_down[i] != result) RenderTexture.ReleaseTemporary(_down[i]);
            if (_up[i] != result) RenderTexture.ReleaseTemporary(_up[i]);
        }
        return result;
    }

    // ReplicaExt.PostProcessFeature.SetupRenderFeature + Replica.ColorGradingLutPass (decompiled):
    // every frame the game bakes _ColorGraddingLut from the volume stack with
    // Hidden/RenderPipeline/Lut (URP's LDR LUT builder) into a size^2 x size ARGB32 strip,
    // sRGB-encoded (linear colour space); size 32 with PostProcessSetting.lutSize32, else 16.
    // SceneSetting._colorGraddingLut is never read by this pipeline.
    Texture Lut()
    {
        int size = Vol("PostProcessSetting", "lutSize32", false) ? 32 : 16;
        if (!_lut || _lut.height != size)
        {
            if (_lut) _lut.Release();
            _lut = new RenderTexture(size * size, size, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB)
                { filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp, hideFlags = HideFlags.HideAndDontSave };
        }
        var m = Mat(ref _lutBuilder, "Hidden/RenderPipeline/Lut");
        if (!m) return _lut;
        // (h, 0.5/w, 0.5/h, h/(h-1)); the game divides the last term as integers (idiv), so it is 1
        m.SetVector("_LutParams", new Vector4(size, 0.5f / (size * size), 0.5f / size, size / (size - 1)));

        Vector3 lms = ColorBalanceToLMSCoeffs(Vol("WhiteBalance", "temperature", 0f), Vol("WhiteBalance", "tint", 0f));
        m.SetVector("_ColorBalance", new Vector4(lms.x, lms.y, lms.z, 0f));
        Color filter = Vol("ColorAdjustments", "colorFilter", Color.white);
        m.SetVector("_ColorFilter", new Vector4(Mathf.GammaToLinearSpace(filter.r), Mathf.GammaToLinearSpace(filter.g),
                                                Mathf.GammaToLinearSpace(filter.b), filter.a));
        m.SetVector("_HueSatCon", new Vector4(Vol("ColorAdjustments", "hueShift", 0f) / 360f,
                                              Vol("ColorAdjustments", "saturation", 0f) / 100f + 1f,
                                              Vol("ColorAdjustments", "contrast", 0f) / 100f + 1f, 0f));

        var grey = new Color(0.5f, 0.5f, 0.5f, 1f);
        Color splitS = Vol("SplitToning", "shadows", grey), splitH = Vol("SplitToning", "highlights", grey);
        m.SetVector("_SplitShadows", new Vector4(splitS.r, splitS.g, splitS.b, Vol("SplitToning", "balance", 0f) / 100f));
        m.SetVector("_SplitHighlights", new Vector4(splitH.r, splitH.g, splitH.b, 0f));

        m.SetVector("_ChannelMixerRed", Mixer("red", 100f, 0f, 0f));
        m.SetVector("_ChannelMixerGreen", Mixer("green", 0f, 100f, 0f));
        m.SetVector("_ChannelMixerBlue", Mixer("blue", 0f, 0f, 100f));

        var one = new Vector4(1f, 1f, 1f, 0f);
        m.SetVector("_Shadows", ShadowsMidtonesHighlights(Vol("ShadowsMidtonesHighlights", "shadows", one)));
        m.SetVector("_Midtones", ShadowsMidtonesHighlights(Vol("ShadowsMidtonesHighlights", "midtones", one)));
        m.SetVector("_Highlights", ShadowsMidtonesHighlights(Vol("ShadowsMidtonesHighlights", "highlights", one)));
        m.SetVector("_ShaHiLimits", new Vector4(Vol("ShadowsMidtonesHighlights", "shadowsStart", 0f),
                                                Vol("ShadowsMidtonesHighlights", "shadowsEnd", 0.3f),
                                                Vol("ShadowsMidtonesHighlights", "highlightsStart", 0.55f),
                                                Vol("ShadowsMidtonesHighlights", "highlightsEnd", 1f)));

        // PrepareLiftGammaGain: lift x0.15, gamma/gain x0.8, each minus its luminance plus w
        Vector4 lift = Vol("LiftGammaGain", "lift", one), gamma = Vol("LiftGammaGain", "gamma", one),
                gain = Vol("LiftGammaGain", "gain", one);
        Vector3 l = Lin(lift) * 0.15f, g = Lin(gamma) * 0.8f, k = Lin(gain) * 0.8f;
        float lumL = Luminance(l), lumG = Luminance(g), lumK = Luminance(k);
        m.SetVector("_Lift", new Vector4(l.x - lumL + lift.w, l.y - lumL + lift.w, l.z - lumL + lift.w, 0f));
        m.SetVector("_Gamma", new Vector4(1f / Mathf.Max(g.x - lumG + gamma.w + 1f, 0.001f),
                                          1f / Mathf.Max(g.y - lumG + gamma.w + 1f, 0.001f),
                                          1f / Mathf.Max(g.z - lumG + gamma.w + 1f, 0.001f), 0f));
        m.SetVector("_Gain", new Vector4(k.x - lumK + gain.w + 1f, k.y - lumK + gain.w + 1f, k.z - lumK + gain.w + 1f, 0f));

        // ColorCurves: no stage overrides them, so every curve is its default TextureCurve bake
        // (128 texels, texel i = curve(i/128)): master and RGB identity, hue/sat/lum curves 0.5
        if (!_curveIdentity) _curveIdentity = CurveTexture(i => i / 128f);
        if (!_curveHalf) _curveHalf = CurveTexture(i => 0.5f);
        foreach (var c in new[] { "_CurveMaster", "_CurveRed", "_CurveGreen", "_CurveBlue" }) m.SetTexture(c, _curveIdentity);
        foreach (var c in new[] { "_CurveHueVsHue", "_CurveHueVsSat", "_CurveSatVsSat", "_CurveLumVsSat" }) m.SetTexture(c, _curveHalf);

        Draw(null, _lut, m, 0);
        return _lut;
    }

    static Vector4 Mixer(string output, float r, float g, float b) => new Vector4(
        Vol("ChannelMixer", output + "OutRedIn", r) / 100f, Vol("ChannelMixer", output + "OutGreenIn", g) / 100f,
        Vol("ChannelMixer", output + "OutBlueIn", b) / 100f, 0f);

    static Vector3 Lin(Vector4 c) =>
        new Vector3(Mathf.GammaToLinearSpace(c.x), Mathf.GammaToLinearSpace(c.y), Mathf.GammaToLinearSpace(c.z));

    static float Luminance(Vector3 c) => c.x * 0.2126729f + c.y * 0.7151522f + c.z * 0.072175f;

    // PrepareShadowsMidtonesHighlights: linear colour + w (x4 when w >= 0), floored at 0
    static Vector4 ShadowsMidtonesHighlights(Vector4 v)
    {
        float w = v.w * (v.w >= 0f ? 4f : 1f);
        Vector3 c = Lin(v);
        return new Vector4(Mathf.Max(c.x + w, 0f), Mathf.Max(c.y + w, 0f), Mathf.Max(c.z + w, 0f), 0f);
    }

    // SRP core ColorUtils.ColorBalanceToLMSCoeffs: white balance as LMS scale factors
    static Vector3 ColorBalanceToLMSCoeffs(float temperature, float tint)
    {
        float t1 = temperature / 65f, t2 = tint / 65f;
        float x = 0.31271f - t1 * (t1 < 0f ? 0.1f : 0.05f);
        float y = 2.87f * x - 3f * x * x - 0.27509507f + t2 * 0.05f;
        float X = x / y, Z = (1f - x - y) / y;      // CIExyToLMS with Y = 1
        var lms = new Vector3(0.7328f * X + 0.4296f - 0.1624f * Z,
                              -0.7036f * X + 1.6975f + 0.0061f * Z,
                              0.0030f * X + 0.0136f + 0.9834f * Z);
        return new Vector3(0.949237f / lms.x, 1.03542f / lms.y, 1.08728f / lms.z);
    }

    static Texture2D CurveTexture(System.Func<int, float> value)
    {
        var t = new Texture2D(128, 1, TextureFormat.RHalf, false, true)
            { filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp, hideFlags = HideFlags.HideAndDontSave };
        var px = new Color[128];
        for (int i = 0; i < 128; i++) px[i] = new Color(value(i), 0f, 0f, 0f);
        t.SetPixels(px);
        t.Apply(false, true);
        return t;
    }

    void OnDisable()
    {
        if (_lut) { _lut.Release(); _lut = null; }
    }
}
