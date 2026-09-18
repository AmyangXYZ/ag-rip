// The game's end-of-frame, rebuilt around its own decompiled shaders:
//   Hidden/SimPipeline/PostProcessing/Bloom  - URP-style bloom (prefilter, H/V blur, upsample)
//   Hidden/SimPipeline/Final                 - bloom add, filmic/ACES curve, vignette, LUT
// Parameters come from the scene's SceneSetting (tonemapping, exposure, contrast,
// threshold, colour-grading LUT). Bloom scatter/knee are not stored in the scene -
// BloomPass: scatter 0.77, clamp 100, knee = threshold * 0.5 (decompiled).
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

    Material _bloom, _final, _lut3d;
    RenderTexture _lutStrip;
    Texture _lutSource;
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
        final.SetTexture("_ColorGraddingLut", Lut(ss));
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

    // Final samples a 2D strip LUT: size^2 x size, slice = blue.
    Texture Lut(object ss)
    {
        var src = Get<byte>(ss, "_colorGradding", 0) != 0 ? Get<Texture>(ss, "_colorGraddingLut", null) : null;
        if (_lutStrip && _lutSource == src) return _lutStrip;
        _lutSource = src;
        int size = src is Texture3D t3 ? t3.depth : 32;
        if (_lutStrip) _lutStrip.Release();
        _lutStrip = new RenderTexture(size * size, size, 0, RenderTextureFormat.ARGBHalf, RenderTextureReadWrite.Linear)
            { filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp, hideFlags = HideFlags.HideAndDontSave };
        var m = Mat(ref _lut3d, "Hidden/AG/LutStrip");
        m.SetTexture("_Lut3D", src ? src : null);
        m.SetFloat("_Identity", src ? 0f : 1f);
        m.SetFloat("_Size", size);
        Graphics.Blit(null, _lutStrip, m, 0);
        return _lutStrip;
    }

    void OnDisable()
    {
        if (_lutStrip) { _lutStrip.Release(); _lutStrip = null; }
    }
}
