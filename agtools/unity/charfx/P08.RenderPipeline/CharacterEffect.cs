// CharacterEffect (assembly P08.RenderPipeline, AOT code in GameAssembly.dll) - the per-character
// driver of the character shaders' per-renderer inputs. Replaces the field-only stub
// (Assets/Scripts/P08.RenderPipeline/CharacterEffect.cs), same class name and serialized layout.
//
// Ported from the Ghidra decompile of the game's methods (AG_cache/re/decomp_charfx and
// AG_cache/re/decomp_all), method by method:
//   CharacterEffect.Awake / OnEnable / OnDisable / LateUpdate / UpdateMaterials / UseUILightPosition
//   CharacterEffect.ShaderIds, CharacterEffectShaderIds (.cctor)       property names
//   EffectBase.Update / EffectContext.Reset / get_keywordDirty          effect plumbing
//   DitherEffect.Update, InterferenceEffect.Update, EnvironmentEffect.Update,
//   EnvironmentEffectUtil.GetAmbientProbe / SHCoefficients, EyeEffect.updateEyeEffect
//
// Everything is written into ONE MaterialPropertyBlock shared by all of the character's renderers
// (as the game does), re-applied with Renderer.SetPropertyBlock whenever a value changed.
//
// Not ported (inactive in the home/DLC default look; battle/FX only): CharacterEffectOverrider
// data, fur (updateFur), ground shadow (updateShadow -> GroundShadowSystem), selected outline,
// ghost, slice, image/SeperateRT and OutlineRT effects. Their serialized data is kept.
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

[ExecuteAlways]
public class CharacterEffect : MonoBehaviour
{
    // ------------------------------------------------------------------ serialized layout (stub)
    [Serializable]
    public class RendererInfo
    {
        [SerializeField] public Renderer renderer;
        [SerializeField] public byte updateBounds;
        [SerializeField] public string rendererName;
    }

    [Serializable]
    public class EyeEffect
    {
        [SerializeField] public byte _enabled;
        [SerializeField] public List<OverriderEffectRendererData> _eyeRendererList;
        [NonSerialized] public int _eyeEffectId = -1;
    }

    [Serializable]
    public class OverriderEffectRendererData
    {
        [SerializeField] public string name;
        [SerializeField] public Renderer renderer;
        [SerializeField] public List<int> submeshIndcies;
        [SerializeField] public List<int> overridePass1;
        [SerializeField] public List<int> overridePass2;
        [SerializeField] public List<int> overridePass3;
    }

    [Serializable]
    public class ImageEffect
    {
        [SerializeField] public byte _overrideSeperateRTSize;
        [SerializeField] public int _overrideSeperateRTWidth;
        [SerializeField] public int _overrideSeperateRTHeight;
        [SerializeField] public Color _color;
        [SerializeField] public byte _allowAlphaFade;
        [SerializeField] public byte _InteractWithScene;
        [SerializeField] public float _fps;
        [SerializeField] public float _dispersionScale;
        [SerializeField] public int _blurScale;
        [SerializeField] public float _noisyScale;
        [SerializeField] public float _interferenceScale;
        [SerializeField] public float _interferenceStrengthRandom;
        [SerializeField] public float _interferenceStrength;
        [SerializeField] public float _interferenceSpeed;
        [SerializeField] public float _silhouetteScale;
        [SerializeField] public float _silhouetteMainBodyTransform;
        [SerializeField] public Color _silhouetteLineColor;
        [SerializeField] public Color _silhouetteMainBodyColor;
        [SerializeField] public byte _antiShake;
    }

    [Serializable]
    public class GhostEffect
    {
        [SerializeField] public SkinnedMeshRenderer _renderer;
        [SerializeField] public int maxGhostCount;
        [SerializeField] public Material _material;
        [SerializeField] public byte _enabled;
        [SerializeField] public byte auto;
        [SerializeField] public float _interval;
    }

    [Serializable]
    public class InterferenceEffect
    {
        [SerializeField] public float _noise;
        [SerializeField] public Color _geometryOutlineColor;
        [SerializeField] public float _simTimeScale;
        [NonSerialized] public bool _enabled;          // 0x30, not serialized in the game
        [NonSerialized] public bool _lastActive;       // 0x31
        [NonSerialized] public bool _dirty = true;     // EffectBase._dirty (ctor: true)
    }

    [Serializable]
    public class DitherEffect
    {
        [SerializeField] public float _alpha;
        [NonSerialized] public bool _dirty = true;
    }

    [Serializable]
    public class SliceEffect
    {
        [SerializeField] public Transform _center;
        [SerializeField] public float _offset;
        [SerializeField] public float _range;
        [SerializeField] public float _density;
        [SerializeField] public float _speed;
        [SerializeField] public Color _color;
        [SerializeField] public Color _color2;
        [SerializeField] public float _sliceBoundOffset;
        [SerializeField] public float _upperBoundDensity;
        [SerializeField] public float _horizontalDensityRange;
    }

    [Serializable]
    public class EnvironmentEffect
    {
        [SerializeField] public Color _skyColor;
        [SerializeField] public Color _equatorColor;
        [SerializeField] public Color _groundColor;
        [SerializeField] public Cubemap _reflectionMap;
        [NonSerialized] public bool _dirty = true;
        [NonSerialized] public SH lastShParams = new SH();
        [NonSerialized] public SH curShParams = new SH();
        [NonSerialized] public Texture lastReflectionMap;   // never assigned by the game's Update
    }

    [Serializable]
    public class OutlineRTEffect
    {
        [SerializeField] public byte _enabled;
        [SerializeField] public float _bias;
        [SerializeField] public float _conservativeBias;
        [SerializeField] public int _MSAASamples;
    }

    [SerializeField] public byte _furEnabled;
    [SerializeField] public List<Renderer> _furRendererList;
    [SerializeField] public List<RendererInfo> _rendererList;
    [SerializeField] public byte _shadowEnabled;
    [SerializeField] public byte _onlyPC;
    [SerializeField] public float _shadowHeight;
    [SerializeField] public Color _emissionColor;
    [SerializeField] public Color _fillOuter;
    [SerializeField] public Color _fillInner;
    [SerializeField] public Color _fillColor;
    [SerializeField] public float _fillRatio;
    [SerializeField] public Texture2D _dissolveTexture;
    [SerializeField] public Color _dissolveColor;
    [SerializeField] public float _dissolveFactor;
    [SerializeField] public float _localLightInclination;
    [SerializeField] public float _localLightAzimuth;
    [SerializeField] public float _localLightIntensity;
    [SerializeField] public Color _localLightColor;
    [SerializeField] public byte _faceReceiveShadow;
    [SerializeField] public byte _disableFaceSDFShadow;
    [SerializeField] public Transform _face;
    [SerializeField] public Color _rimlightColor;
    [SerializeField] public float _rimlightThreshold;
    [SerializeField] public float _rimlightFade;
    [SerializeField] public float _rimlightRange;
    [SerializeField] public float _rimlightInclination;
    [SerializeField] public float _rimlightAzimuth1;
    [SerializeField] public float _rimlightAzimuth2;
    [SerializeField] public float _UILightInclination;
    [SerializeField] public float _UILightAzimuth;
    [SerializeField] public float _rotateSpeed;
    [SerializeField] public float _lightDirectionChangeSideValue;
    [SerializeField] public float _lightDirectionTransitionFactor;
    [SerializeField] public GameObject _directionalLight;
    [SerializeField] public Quaternion OriginalRotation;
    [SerializeField] public float relativeAngle;
    [SerializeField] public int _characterLayer;
    [SerializeField] public EyeEffect _eyeEffect;
    [SerializeField] public ImageEffect _imageEffect;
    [SerializeField] public byte _skipDof;
    [SerializeField] public GhostEffect _ghostEffect;
    [SerializeField] public InterferenceEffect _interferenceEffect;
    [SerializeField] public DitherEffect _ditherEffect;
    [SerializeField] public SliceEffect _sliceEffect;
    [SerializeField] public EnvironmentEffect _environmentEffect;
    [SerializeField] public OutlineRTEffect _outlineRTEffect;
    [SerializeField] public byte _selectedOutline;
    [SerializeField] public byte _customSelectedOutline;
    [SerializeField] public float _selectedOutlineThickness;
    [SerializeField] public Color _selectedOutlineColor;
    [SerializeField] public float _selectedOutlineMax;
    [SerializeField] public float _selectedOutlineMin;

    // ------------------------------------------------------------------ shader ids
    // CharacterEffect.ShaderIds..cctor @0x2905dd0
    static readonly int FillOuter = Shader.PropertyToID("_FillOuter");
    static readonly int FillInner = Shader.PropertyToID("_FillInner");
    static readonly int FillClamp = Shader.PropertyToID("_FillClamp");
    static readonly int FillSoft = Shader.PropertyToID("_FillSoft");
    static readonly int DissolveTexture = Shader.PropertyToID("_DissolveTexture");
    static readonly int DissolveColor = Shader.PropertyToID("_DissolveColor");
    static readonly int DissolveFactor = Shader.PropertyToID("_DissolveFactor");
    static readonly int ZOffsetUnit = Shader.PropertyToID("_ZOffset_Unit");
    static readonly int ExtralEmissionColor = Shader.PropertyToID("_ExtralEmissionColor");
    static readonly int LocalCharacterHeight = Shader.PropertyToID("_LocalCharacterHeight");
    static readonly int RimlightColor = Shader.PropertyToID("_RimlightColor");
    static readonly int RimlightThreshold = Shader.PropertyToID("_RimlightThreshold");
    static readonly int RimlightFade = Shader.PropertyToID("_RimlightFade");
    static readonly int RimlightDir1 = Shader.PropertyToID("_RimlightDir1");
    static readonly int RimlightDir2 = Shader.PropertyToID("_RimlightDir2");
    static readonly int RimlightRange = Shader.PropertyToID("_RimlightRange");
    // SimPipeline.Character.CharacterEffectShaderIds..cctor @0x2953e00
    static readonly int LocalLightDir = Shader.PropertyToID("_LocalLightDir");
    static readonly int LocalLightColor = Shader.PropertyToID("_LocalLightColor");
    static readonly int UseFaceReceiveShadow = Shader.PropertyToID("_UseFaceReceiveShadow");
    static readonly int DisableFaceSDFShadow = Shader.PropertyToID("_DisableFaceSDFShadow");
    static readonly int World2Face = Shader.PropertyToID("_World2Face");
    // DitherEffect / InterferenceEffect / EnvironmentEffect ..cctor
    static readonly int DitherAlpha = Shader.PropertyToID("_DitherAlpha");
    static readonly int Noise = Shader.PropertyToID("_Noise");
    static readonly int GeometryOutlineColor = Shader.PropertyToID("_GeometryOutlineColor");
    static readonly int SimTimeScale = Shader.PropertyToID("_SimTimeScale");
    static readonly int[] UnitySH = {
        Shader.PropertyToID("unity_SHAr"), Shader.PropertyToID("unity_SHAg"), Shader.PropertyToID("unity_SHAb"),
        Shader.PropertyToID("unity_SHBr"), Shader.PropertyToID("unity_SHBg"), Shader.PropertyToID("unity_SHBb"),
        Shader.PropertyToID("unity_SHC") };
    static readonly int[] ReplicaSH = {
        Shader.PropertyToID("_Replica_SHAr"), Shader.PropertyToID("_Replica_SHAg"), Shader.PropertyToID("_Replica_SHAb"),
        Shader.PropertyToID("_Replica_SHBr"), Shader.PropertyToID("_Replica_SHBg"), Shader.PropertyToID("_Replica_SHBb"),
        Shader.PropertyToID("_Replica_SHC") };
    static readonly int SimEnvCube = Shader.PropertyToID("sim_EnvCube");
    // not the game's: the built-in renderer leaves unity_RenderingLayer 0, AGSimPipeline fills it
    // per renderer for the Forward+ light mask test; SetPropertyBlock below would erase it.
    static readonly int UnityRenderingLayer = Shader.PropertyToID("unity_RenderingLayer");
    const string KwDissolve = "SIM_DISSOLVE", KwDithering = "SIM_DITHERING", KwInterference = "SIM_INTERFERENCE";

    // ------------------------------------------------------------------ runtime state
    /// CharacterEffectSystem._characters: every enabled CharacterEffect (read by AGSimCharacter).
    public static readonly List<CharacterEffect> Active = new List<CharacterEffect>();

    bool _isEnable = true;                         // 0x20 (ctor: true)
    List<Renderer> _renderers;                     // 0x28
    MaterialPropertyBlock _propBlock;              // 0x40
#pragma warning disable 0414, 0649
    bool _shareMaterial;                           // 0x38 (never set by the game's home/DLC code)
    bool _furDirty;                                // 0x50 (fur not ported)
#pragma warning restore 0414, 0649
    bool _hightDirty;                              // 0x7A
    bool _emissionDirty, _fillDirty, _dissolveDirty, _lightDirDirty, _rimlightDirty;
    bool _fillFlat;                                // 0xD0 (not serialized)
    float _fillSoft;                               // 0xD4 (not serialized)
    float _prevDissolveFactor;                     // 0xFC
    Vector3 localLightDir;                         // 0x120
    Matrix4x4 _lastWorld2Face = Matrix4x4.zero;    // 0x138 (ctor: Matrix4x4.zero)
    bool _UIlightDirDirty;                         // 0x1A4
    Quaternion lastRotation;                       // 0x1C8
    bool IsBattleModel;                            // 0x1D8
    bool firstCall = true;                         // 0x1D9 (ctor: true)
    bool UseUILightPosDirty;                       // 0x1F0
    bool _CharacterLayerDirty;                     // 0x1F1

    // EffectContext
    int _activeEffects;
    bool _requireBlockUpdate;
    readonly List<string> _enableKeywords = new List<string>(), _disableKeywords = new List<string>();

    public List<Renderer> allRenderers => _renderers;
    public bool ActiveAndEnabled => isActiveAndEnabled;

    // ------------------------------------------------------------------ properties (setters mark dirty)
    public Color emissionColor { get => _emissionColor; set { _emissionColor = value; _emissionDirty = true; } }
    public Color fillOuter { get => _fillOuter; set { _fillOuter = value; _fillDirty = true; } }
    public Color fillInner { get => _fillInner; set { _fillInner = value; _fillDirty = true; } }
    public Color fillColor { get => _fillColor; set { _fillColor = value; _fillDirty = true; } }
    public bool fillFlat { get => _fillFlat; set { _fillFlat = value; _fillDirty = true; } }
    public float fillSoft { get => _fillSoft; set { _fillSoft = value; _fillDirty = true; } }
    public float fillRatio { get => _fillRatio; set { _fillRatio = value; _fillDirty = true; } }
    public float dissolveFactor { get => _dissolveFactor; set { _dissolveFactor = value; _dissolveDirty = true; } }
    public float lightInclination { get => _localLightInclination; set { _localLightInclination = value; _lightDirDirty = true; } }
    public float lightAzimuth { get => _localLightAzimuth; set { _localLightAzimuth = value; _lightDirDirty = true; } }
    public float localLightIntensity { get => _localLightIntensity; set { _localLightIntensity = value; _lightDirDirty = true; } }
    public Color localLightColor { get => _localLightColor; set { _localLightColor = value; _lightDirDirty = true; } }
    public bool faceReceiveShadow { get => _faceReceiveShadow != 0; set { _faceReceiveShadow = (byte)(value ? 1 : 0); _lightDirDirty = true; } }
    public bool disableFaceSDFShadow { get => _disableFaceSDFShadow != 0; set { _disableFaceSDFShadow = (byte)(value ? 1 : 0); _lightDirDirty = true; } }
    public Transform face { get => _face; set => _face = value; }
    public Color rimLightColor { get => _rimlightColor; set { _rimlightColor = value; _rimlightDirty = true; } }
    public float rimLightThreshold { get => _rimlightThreshold; set { _rimlightThreshold = value; _rimlightDirty = true; } }
    public float rimLightFade { get => _rimlightFade; set { _rimlightFade = value; _rimlightDirty = true; } }
    public float rimLightRange { get => _rimlightRange; set { _rimlightRange = value; _rimlightDirty = true; } }
    public float rimLightInclination { get => _rimlightInclination; set { _rimlightInclination = value; _rimlightDirty = true; } }
    public float rimLightAzimuth1 { get => _rimlightAzimuth1; set { _rimlightAzimuth1 = value; _rimlightDirty = true; } }
    public float rimLightAzimuth2 { get => _rimlightAzimuth2; set { _rimlightAzimuth2 = value; _rimlightDirty = true; } }
    public float UIlightInclination { get => _UILightInclination; set { _UILightInclination = value; _UIlightDirDirty = true; } }
    public float UIlightAzimuth { get => _UILightAzimuth; set { _UILightAzimuth = value; _UIlightDirDirty = true; } }
    public float shadowHeight { get => _shadowHeight; set { _shadowHeight = value; _hightDirty = true; } }
    public int characterLayer { get => _characterLayer; set { _characterLayer = value; _CharacterLayerDirty = true; } }
    public float ditherAlpha
    {
        get => _ditherEffect != null ? _ditherEffect._alpha : 1f;
        set { if (_ditherEffect != null) { _ditherEffect._alpha = value; _ditherEffect._dirty = true; } }
    }

    // ------------------------------------------------------------------ lifecycle
    // CharacterEffect$$Awake @0x28f42c0
    void Awake()
    {
        UpdateMaterials();
        // GhostEffect.Init(transform) - ghosts not ported.
        // lastRotation = Quaternion.Internal_FromEulerRad((0, 0x406a927f, 0)): (0, 3.6651914 rad, 0)
        lastRotation = Quaternion.Euler(0f, BitConverter.Int32BitsToSingle(0x406a927f) * Mathf.Rad2Deg, 0f);
        IsBattleModel = false;
    }

    // CharacterEffect$$OnEnable @0x28f6860
    void OnEnable()
    {
        if (!Active.Contains(this)) Active.Add(this);     // CharacterEffectSystem.Enable
        _dissolveFactor = 0f;                             // param_1[0xf].monitor (0xF8) = 0
        // updateShadow / updateSelectOutline: ground shadow and selection outline not ported.
        // InitCharacterLayerMask: every non-particle character renderer -> rendering layer 0x40000001
        if (_renderers != null)
            foreach (var r in _renderers)
                if (r && !(r is ParticleSystemRenderer)) r.renderingLayerMask = 0x40000001u;
        UpdateEyeEffect(isActiveAndEnabled);
        if (_outlineRTEffect != null) { /* OutlineRTEffect.SetDirty / UpdateOutlineRT - not ported */ }
    }

    // CharacterEffect$$OnDisable @0x28f6790
    void OnDisable()
    {
        UpdateEyeEffect(isActiveAndEnabled);
        Active.Remove(this);                              // CharacterEffectSystem._characters.Remove
    }

    // EyeEffect$$updateEyeEffect @0x29675d0: register the eye renderers with OverriderEffectSystem
    // (drawn by AGSimCharacter's override pass) while the effect is enabled and the character active.
    void UpdateEyeEffect(bool ceActive)
    {
        if (_eyeEffect == null) return;
        bool on = ceActive && _eyeEffect._enabled != 0;
        _eyeEffect._eyeEffectId = on ? 0 : -1;
    }

    /// OverriderEffectSystem data of this character (null when not registered).
    public List<OverriderEffectRendererData> EyeRenderData =>
        _eyeEffect != null && _eyeEffect._eyeEffectId >= 0 ? _eyeEffect._eyeRendererList : null;

    // ------------------------------------------------------------------ UpdateMaterials @0x28f6af0
    static bool IsCharacterShader(Shader s) => s && s.name != null && s.name.Contains("SimPipeline");

    void UpdateMaterials()
    {
        if (_propBlock == null) _propBlock = new MaterialPropertyBlock();
        if (_renderers == null) _renderers = new List<Renderer>();
        else if (_renderers.Count > 0) return;
        foreach (var r in GetComponentsInChildren<Renderer>(true))
        {
            if (!r || r is ParticleSystemRenderer) continue;
            bool character = false;
            var mats = r.sharedMaterials;
            for (int i = 0; i < mats.Length && !character; i++)
            {
                if (!mats[i]) { Debug.LogError($"{gameObject.name}: {r.name} sharedMaterial is null!"); continue; }
                character = IsCharacterShader(mats[i].shader);
            }
            if (!character) continue;
            ApplyBlock(r);
            if (Application.isPlaying)
            {
                r.motionVectorGenerationMode = MotionVectorGenerationMode.Object;
                if (r is SkinnedMeshRenderer smr) smr.skinnedMotionVectors = true;
            }
            _renderers.Add(r);
            // GetComponent<CharacterEffectOverrider>() - no overriders in the exported prefabs.
        }
        _CharacterLayerDirty = false;
        _UIlightDirDirty = false;
        _emissionDirty = _fillDirty = _lightDirDirty = _dissolveDirty = true;   // +0x88 +0x9c +0x100 +0xdc
        // +0x264 selected outline dirty: not ported
        if (_ditherEffect != null) _ditherEffect._dirty = true;
        if (_environmentEffect != null) _environmentEffect._dirty = true;
    }

    void ApplyBlock(Renderer r)
    {
        _propBlock.SetVector(UnityRenderingLayer, new Vector4(BitConverter.Int32BitsToSingle((int)r.renderingLayerMask), 0, 0, 0));
        r.SetPropertyBlock(_propBlock);
    }

    // ------------------------------------------------------------------ LateUpdate @0x28f4a90
    void LateUpdate()
    {
        if (!_isEnable) return;
        UpdateMaterials();
        // EffectContext.Reset: activeEffects = 0x110, keyword lists cleared, requireBlockUpdate = false
        _activeEffects = 0x110;
        _enableKeywords.Clear(); _disableKeywords.Clear();
        _requireBlockUpdate = false;
        bool force = false;                           // set only by a CharacterEffectOverrider
        bool changed = false;
        var mpb = _propBlock;

        if (!_face) _face = transform;
        Matrix4x4 w2f = _face.worldToLocalMatrix;
        if (w2f != _lastWorld2Face)
        {
            _lastWorld2Face = w2f;
            mpb.SetMatrix(World2Face, w2f);
            changed = true;
        }
        if (force || _emissionDirty)
        {
            mpb.SetColor(ExtralEmissionColor, _emissionColor);
            if (_furEnabled != 0) _furDirty = true;
            _emissionDirty = false;
            changed = true;
        }
        if (force || _fillDirty)
        {
            Color inner = _fillInner, outer = _fillOuter;
            if (_fillFlat) { _fillColor.a = 1f; inner = outer = _fillColor; }
            inner.a *= _fillRatio;
            outer.a *= _fillRatio;
            mpb.SetColor(FillOuter, outer);
            mpb.SetColor(FillInner, inner);
            mpb.SetFloat(FillClamp, _fillFlat ? 1f : 0f);
            mpb.SetFloat(FillSoft, _fillSoft);
            _fillDirty = false;
            changed = true;
        }
        if (_dissolveDirty || _prevDissolveFactor != _dissolveFactor || force)
        {
            _prevDissolveFactor = _dissolveFactor;
            if (_dissolveTexture) mpb.SetTexture(DissolveTexture, _dissolveTexture);
            else _dissolveFactor = 0f;
            mpb.SetFloat(DissolveFactor, _dissolveFactor);
            mpb.SetColor(DissolveColor, _dissolveColor);
            (_dissolveFactor > 0f ? _enableKeywords : _disableKeywords).Add(KwDissolve);
            _dissolveDirty = false;
            changed = true;
        }
        if (_dissolveFactor > 0f) _activeEffects |= 2;
        if (_lightDirDirty)
        {
            // inclination around the horizon, azimuth negated; float math as in the game (pi = 3.1415927)
            float incl = _localLightInclination * 3.1415927f / 180f;
            float az = -_localLightAzimuth * 3.1415927f / 180f;
            localLightDir.y = Mathf.Sin(incl);
            float c = Mathf.Cos(incl);
            localLightDir.x = Mathf.Sin(az) * -c;
            localLightDir.z = Mathf.Cos(az) * c;
            mpb.SetColor(LocalLightColor, _localLightColor * _localLightIntensity);
            mpb.SetVector(LocalLightDir, new Vector4(localLightDir.x, localLightDir.y, localLightDir.z, 0f));
            mpb.SetFloat(UseFaceReceiveShadow, _faceReceiveShadow != 0 ? 1f : 0f);
            mpb.SetFloat(DisableFaceSDFShadow, _disableFaceSDFShadow != 0 ? 1f : 0f);
            _lightDirDirty = false;
            changed = true;
        }
        if (_rimlightDirty)
        {
            mpb.SetColor(RimlightColor, _rimlightColor);
            mpb.SetFloat(RimlightThreshold, _rimlightThreshold);
            mpb.SetFloat(RimlightFade, _rimlightFade);
            mpb.SetFloat(RimlightRange, _rimlightRange);
            float z = _rimlightInclination;
            float s = Mathf.Sqrt(1f - z * z);             // NaN for |z| > 1, as sqrtf in the game
            float a1 = _rimlightAzimuth1 * 3.1415927f, a2 = _rimlightAzimuth2 * 3.1415927f;
            mpb.SetVector(RimlightDir1, new Vector4(Mathf.Cos(a1) * s, Mathf.Sin(a1) * s, z, 0f));
            mpb.SetVector(RimlightDir2, new Vector4(Mathf.Cos(a2) * s, Mathf.Sin(a2) * s, z, 0f));
            _rimlightDirty = false;
            changed = true;
        }
        // _shadowDirty -> updateShadow (ground shadow): not ported
        if (_hightDirty)                                  // never cleared here, as in the game
            mpb.SetFloat(LocalCharacterHeight, _shadowHeight);
        // selected outline (+0x264/+0x265/+0x266): not ported

        if (!IsBattleModel)
        {
            UseUILightPosition();
            UseUILightPosDirty = true;
        }
        else if (UseUILightPosDirty)
        {
            if (_directionalLight)
            {
                _directionalLight.transform.rotation = OriginalRotation;
                firstCall = true;
            }
            UseUILightPosDirty = false;
        }
        if (_CharacterLayerDirty)
        {
            mpb.SetFloat(ZOffsetUnit, _characterLayer);
            _CharacterLayerDirty = false;
            changed = true;
        }

        // effects, in the game's order: interference, dither, slice, ghost, eye, outlineRT, environment, image
        UpdateInterference();
        UpdateDither();
        if (_eyeEffect != null) UpdateEyeEffect(isActiveAndEnabled);   // EyeEffect.Update
        UpdateEnvironment();

        bool apply = changed || _requireBlockUpdate;
        bool keywordDirty = _disableKeywords.Count > 0 || _enableKeywords.Count > 0;
        if (keywordDirty)
        {
            foreach (var r in _renderers) if (r) ModifyKeyword(r, _disableKeywords, _enableKeywords);
            apply = true;
        }
        if (apply)
            foreach (var r in _renderers) if (r) ApplyBlock(r);
        // updateFur: not ported
    }

    // CharacterEffect$$modifyKeyword @0x28f88d0: disable, then enable, on the renderer's materials
    // (Renderer.materials, or sharedMaterials when _shareMaterial). Only touches materials whose
    // keyword state actually changes, so the default look (all effect keywords off) changes nothing.
    static bool _warnedKeyword;
    void ModifyKeyword(Renderer r, List<string> disable, List<string> enable)
    {
        bool needed = false;
        foreach (var m in r.sharedMaterials)
        {
            if (!m) continue;
            foreach (var k in disable) needed |= m.IsKeywordEnabled(k);
            foreach (var k in enable) needed |= !m.IsKeywordEnabled(k);
        }
        if (!needed) return;
        if (!Application.isPlaying && !_shareMaterial)
        {
            if (!_warnedKeyword) Debug.LogWarning("CharacterEffect: material keyword changes need Play mode (Renderer.materials).");
            _warnedKeyword = true;
            return;
        }
        foreach (var m in _shareMaterial ? r.sharedMaterials : r.materials)
        {
            if (!m) continue;
            foreach (var k in disable) m.DisableKeyword(k);
            foreach (var k in enable) m.EnableKeyword(k);
        }
    }

    // ------------------------------------------------------------------ effects
    // InterferenceEffect$$Update @0x2969...: on an active change or when dirty, write the three
    // values and the SIM_INTERFERENCE keyword state. get_active: the non-serialized _enabled flag.
    void UpdateInterference()
    {
        var e = _interferenceEffect;
        if (e == null) return;
        bool active = e._enabled;
        if (e._lastActive != active || e._dirty)
        {
            e._dirty = false;
            (active ? _enableKeywords : _disableKeywords).Add(KwInterference);
            _propBlock.SetFloat(Noise, e._noise);
            _propBlock.SetColor(GeometryOutlineColor, e._geometryOutlineColor);
            _propBlock.SetFloat(SimTimeScale, e._simTimeScale);
            _requireBlockUpdate = true;
            e._lastActive = active;
        }
    }

    // DitherEffect$$Update @0x29574c0; get_active: alpha < 1
    void UpdateDither()
    {
        var e = _ditherEffect;
        if (e == null || !e._dirty) return;
        _propBlock.SetFloat(DitherAlpha, e._alpha);
        _requireBlockUpdate = true;
        bool active = e._alpha <= 1f && e._alpha != 1f;
        (active ? _enableKeywords : _disableKeywords).Add(KwDithering);
        e._dirty = false;
    }

    // EnvironmentEffect$$Update @0x2966800: per-renderer ambient SH (unity_SH* and _Replica_SH*)
    // from the effect's own trilight colours, or from the scene's CharacterSceneEnvironment when
    // one is active (EnvironmentEffectUtil.useSceneEnvironment), and an optional sim_EnvCube.
    void UpdateEnvironment()
    {
        var e = _environmentEffect;
        if (e == null) return;
        SH sh;
        if (!EnvironmentEffectUtil.useSceneEnvironment)
        {
            EnvironmentEffectUtil.SHCoefficients(EnvironmentEffectUtil.GetAmbientProbe(e._skyColor, e._equatorColor, e._groundColor), e.curShParams);
            sh = e.curShParams;
        }
        else sh = EnvironmentEffectUtil.sceneSH;
        Texture refl = EnvironmentEffectUtil.useSceneEnvironment ? EnvironmentEffectUtil.sceneReflectionMap : e._reflectionMap;
        bool changed = !e.lastShParams.Same(sh) || refl != e.lastReflectionMap;
        e._dirty = e._dirty || changed;
        if (e._dirty)
        {
            var v = sh.Values;
            for (int i = 0; i < 7; i++) _propBlock.SetVector(UnitySH[i], v[i]);
            for (int i = 0; i < 7; i++) _propBlock.SetVector(ReplicaSH[i], v[i]);
            if (refl) _propBlock.SetTexture(SimEnvCube, refl);
            _requireBlockUpdate = true;
        }
        e.lastShParams.CopyFrom(sh);
        e._dirty = false;
    }

    // ------------------------------------------------------------------ UseUILightPosition @0x28f72b0
    // Home/UI models turn "their" directional light (a Light under the model's grandparent, or
    // _directionalLight) with the model's yaw. Without such a light the model is treated as a
    // battle model and this is skipped from then on.
    static Vector3 EulerDeg(Quaternion q) => q.eulerAngles;   // Internal_ToEulerRad * Rad2Deg + MakePositive

    static float Wrap180(float a)
    {
        if (a > 180f) a -= 360f;
        if (a < -180f) a += 360f;
        return a;
    }

    void UseUILightPosition()
    {
        if (!_directionalLight)
        {
            Transform p = transform.parent, gp = p ? p.parent : null;
            Light l = gp ? gp.GetComponentInChildren<Light>() : null;
            _directionalLight = l ? l.gameObject : null;
            if (!_directionalLight) { IsBattleModel = true; return; }
        }
        Transform parent = transform.parent;
        if (!parent) return;
        Transform lightT = _directionalLight.transform;
        if (firstCall)
        {
            OriginalRotation = lightT.rotation;
            relativeAngle = Wrap180(EulerDeg(lightT.rotation).y - EulerDeg(parent.rotation).y);
            firstCall = false;
            _UILightAzimuth = EulerDeg(OriginalRotation).y;
            _UILightInclination = EulerDeg(OriginalRotation).x;
            _UIlightDirDirty = false;
        }
        if (_UIlightDirDirty)
        {
            // the game passes OriginalRotation.z (a quaternion component) as the euler z, in degrees
            OriginalRotation = Quaternion.Euler(_UILightInclination, _UILightAzimuth, OriginalRotation.z);
            relativeAngle = Wrap180(EulerDeg(OriginalRotation).y - EulerDeg(parent.rotation).y);
            _UIlightDirDirty = false;
        }
        Vector3 fwd = transform.forward;
        float parentYaw = EulerDeg(parent.rotation).y;
        Transform grand = parent.parent;
        if (!grand) return;
        float grandYaw = EulerDeg(grand.rotation).y;
        var cam = Camera.main;
        if (!cam) Debug.Log("cannot find Camera current one");
        else
        {
            Vector3 cf = cam.transform.forward;
            float a = Mathf.Acos(Mathf.Abs(Vector3.Dot(cf, fwd)));
            float target = parentYaw < 180f ? 0f : 360f;
            float t = Mathf.Pow(1f - (a / 3.1415927f + a / 3.1415927f), _lightDirectionTransitionFactor);
            if (t < 0f) t = 0f; else if (t > 1f) t = 1f;
            parentYaw = (1f - t) * parentYaw + t * target;
        }
        _rotateSpeed = Mathf.Clamp01(_rotateSpeed);
        float yaw = parentYaw + relativeAngle + grandYaw;
        while (yaw > 180f || yaw < -180f) yaw += yaw > 180f ? -360f : 360f;

        float last = EulerDeg(lastRotation).y;
        if (last * yaw < 0f) last = last >= 0f ? last - 360f : last + 360f;
        float step = (yaw - last) * _rotateSpeed;
        float v = last + step;
        if (Mathf.Abs(v - yaw) <= Mathf.Abs(step)) v = yaw;
        v = Wrap180(v);
        Vector3 o = EulerDeg(OriginalRotation);
        lastRotation = Quaternion.Euler(o.x, v, o.z);
        lightT.rotation = lastRotation;
    }

    // ------------------------------------------------------------------ misc API used by other game code
    public void ForceUpdate() => LateUpdate();

    public void DisableRenders() { if (_renderers != null) foreach (var r in _renderers) if (r) r.enabled = false; }
    public void EnableRenders() { if (_renderers != null) foreach (var r in _renderers) if (r) r.enabled = true; }

    // ------------------------------------------------------------------ SH (SimPipeline.Character.SH)
    public class SH
    {
        public Vector4 SHAr, SHAg, SHAb, SHBr, SHBg, SHBb, SHC;
        public Vector4[] Values => new[] { SHAr, SHAg, SHAb, SHBr, SHBg, SHBb, SHC };
        static bool Eq(Vector4 a, Vector4 b) => (a - b).sqrMagnitude < 9.9999994e-11f;
        public bool Same(SH o) => Eq(SHAr, o.SHAr) && Eq(SHAg, o.SHAg) && Eq(SHAb, o.SHAb) &&
                                  Eq(SHBr, o.SHBr) && Eq(SHBg, o.SHBg) && Eq(SHBb, o.SHBb) && Eq(SHC, o.SHC);
        public void CopyFrom(SH o) { SHAr = o.SHAr; SHAg = o.SHAg; SHAb = o.SHAb; SHBr = o.SHBr; SHBg = o.SHBg; SHBb = o.SHBb; SHC = o.SHC; }
    }

    /// SimPipeline.Character.EnvironmentEffectUtil (static state set by CharacterSceneEnvironment).
    public static class EnvironmentEffectUtil
    {
        public static bool useSceneEnvironment;          // +0x10 (CharacterSceneEnvironment.OnEnable/OnDisable)
        public static readonly SH sceneSH = new SH();     // +0x00
        public static Texture sceneReflectionMap;        // +0x08

        // GetAmbientProbe: Unity's ambient probe of a trilight with these colours (no colour conversion)
        public static SphericalHarmonicsL2 GetAmbientProbe(Color sky, Color equator, Color ground)
        {
            var mode = RenderSettings.ambientMode;
            RenderSettings.ambientMode = AmbientMode.Trilight;
            Color s0 = RenderSettings.ambientSkyColor, e0 = RenderSettings.ambientEquatorColor, g0 = RenderSettings.ambientGroundColor;
            RenderSettings.ambientSkyColor = sky;
            RenderSettings.ambientEquatorColor = equator;
            RenderSettings.ambientGroundColor = ground;
            var probe = RenderSettings.ambientProbe;
            RenderSettings.ambientSkyColor = s0;
            RenderSettings.ambientEquatorColor = e0;
            RenderSettings.ambientGroundColor = g0;
            RenderSettings.ambientMode = mode;
            return probe;
        }

        // SHCoefficients: SHA = (c3, c1, c2, c0 - c6), SHB = (c4, c5, 3 c6, c7), SHC = (c8 r, g, b, 1)
        public static void SHCoefficients(SphericalHarmonicsL2 sh, SH o)
        {
            o.SHAr = new Vector4(sh[0, 3], sh[0, 1], sh[0, 2], sh[0, 0] - sh[0, 6]);
            o.SHAg = new Vector4(sh[1, 3], sh[1, 1], sh[1, 2], sh[1, 0] - sh[1, 6]);
            o.SHAb = new Vector4(sh[2, 3], sh[2, 1], sh[2, 2], sh[2, 0] - sh[2, 6]);
            o.SHBr = new Vector4(sh[0, 4], sh[0, 5], sh[0, 6] * 3f, sh[0, 7]);
            o.SHBg = new Vector4(sh[1, 4], sh[1, 5], sh[1, 6] * 3f, sh[1, 7]);
            o.SHBb = new Vector4(sh[2, 4], sh[2, 5], sh[2, 6] * 3f, sh[2, 7]);
            o.SHC = new Vector4(sh[0, 8], sh[1, 8], sh[2, 8], 1f);
        }
    }
}
