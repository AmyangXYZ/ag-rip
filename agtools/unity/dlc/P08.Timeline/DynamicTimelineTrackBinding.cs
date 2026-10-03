// Ported from the game's P08.Timeline hot-update DLL (AG_cache/re/hotfix/P08Timeline_src/
// DynamicTimelineTrackBinding.cs, P08CharBinding.cs, P08SceneBinding.cs, P08MixBinding.cs),
// as shipped: at Awake a sequence loads the objects its CharBindings name (113701 touch1 /
// touch2: the script book's light, Prop/ch_113701_prop_taicishu_light), parents them under
// a "root" under the sequence, binds their Animators to the tracks its SceneBindings name,
// points cameras at them (MixBindings), and shows them while the director plays.
// Asset.InstantiateWithoutCache is AGGameAssets here (the prefab the path names).
using System;
using System.Collections.Generic;
using Unity.Cinemachine;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;
using Object = UnityEngine.Object;

#pragma warning disable CS0618 // the game's own 2.x Cinemachine components

[Serializable]
public class P08CharBinding
{
    public enum EnumRootMontion
    {
        DEFAULT,
        ROOT_MONTION,
        NON_ROOT_MONTION
    }

    public string key;

    public string path;

    public Vector3 position;

    public Quaternion rotation;

    public EnumRootMontion enumRootMotion;

    public GameObject gua;

    [NonSerialized]
    public GameObject go;

    public GameObject GetInst(GameObject timelineGo)
    {
        GameObject result = null;
        if (!string.IsNullOrEmpty(path))
            return AGGameAssets.InstantiateWithoutCache(path);
        Transform parent = timelineGo.transform.parent;
        if (parent != null)
        {
            if (parent.name == key)
            {
                result = parent.gameObject;
            }
            else
            {
                Transform parent2 = parent.parent;
                if (parent2 != null && parent2.name == key)
                    result = parent2.gameObject;
            }
        }
        return result;
    }
}

[Serializable]
public class P08SceneBinding
{
    public string key;

    public string subPath;

    public TrackAsset track;

    public string componentType;
}

[Serializable]
public class P08MixBinding
{
    public string key;

    public string subPath;

    public GameObject component;

    public string componentType;
}

public class DynamicTimelineTrackBinding : MonoBehaviour
{
    public enum EnumTimelineType
    {
        TL_DEFAULT,
        TL_REAL_TIME,
        TL_UI_WIN,
        TL_MINIGAME,
        TL_MULTIPLE
    }

    public EnumTimelineType _timelineType;

    [NonSerialized] private bool _isInstall;
    [NonSerialized] private bool _dontLoadChar;
    [NonSerialized] private bool _noNeedRoot;
    [NonSerialized] private bool _dontClearCtrl;
    [NonSerialized] private bool _dontHideOnStop;

    public PlayableDirector playableDirector;

    public List<P08CharBinding> CharBindings = new List<P08CharBinding>();

    public List<P08SceneBinding> SceneBindings = new List<P08SceneBinding>();

    public List<P08MixBinding> MixBindings = new List<P08MixBinding>();

    public Dictionary<string, P08CharBinding> dict = new Dictionary<string, P08CharBinding>();

    private void Awake()
    {
        InitConfig();
        if (!_dontLoadChar)
            AutoInit();
    }

    public virtual void AutoInit()
    {
        ManualInit();
    }

    public virtual void ManualInit()
    {
        LoadDict();
        BuildBindings();
    }

    private void OnStop(PlayableDirector director)
    {
        SetCharActive(false);
    }

    private void OnPlay(PlayableDirector director)
    {
        SetCharActive(true);
    }

    private void InitConfig()
    {
        if (_isInstall) return;
        switch (_timelineType)
        {
            case EnumTimelineType.TL_REAL_TIME:
                _dontLoadChar = false; _noNeedRoot = true; _dontClearCtrl = true; _dontHideOnStop = false;
                break;
            case EnumTimelineType.TL_UI_WIN:
                _dontLoadChar = false; _noNeedRoot = false; _dontClearCtrl = false; _dontHideOnStop = true;
                break;
            case EnumTimelineType.TL_MINIGAME:
            case EnumTimelineType.TL_MULTIPLE:
                _dontLoadChar = true; _noNeedRoot = true; _dontClearCtrl = true; _dontHideOnStop = false;
                break;
            default:
                _dontLoadChar = false; _noNeedRoot = false; _dontClearCtrl = false; _dontHideOnStop = false;
                break;
        }
        _isInstall = true;
    }

    protected void LoadDict()
    {
        foreach (P08CharBinding charBinding in CharBindings)
        {
            if (charBinding.go == null)
            {
                GameObject inst = charBinding.GetInst(gameObject);
                if (inst != null && !_dontLoadChar)
                {
                    if (_noNeedRoot)
                    {
                        inst.transform.parent = transform;
                        inst.transform.SetLocalPositionAndRotation(charBinding.position, charBinding.rotation);
                    }
                    else
                    {
                        var root = new GameObject("root");
                        root.transform.parent = transform;
                        inst.transform.parent = root.transform;
                        root.transform.SetLocalPositionAndRotation(charBinding.position, charBinding.rotation);
                        inst.transform.SetLocalPositionAndRotation(Vector3.zero, Quaternion.identity);
                    }
                }
                charBinding.go = inst;
            }
            if (!dict.TryGetValue(charBinding.key, out var value) || value == null || value.go == null)
                dict[charBinding.key] = charBinding;
        }
    }

    protected void BuildBindings()
    {
        if (playableDirector == null) return;
        foreach (P08SceneBinding sceneBinding in SceneBindings)
        {
            if (string.IsNullOrEmpty(sceneBinding.key) || !dict.TryGetValue(sceneBinding.key, out var value) || value == null || sceneBinding.track == null)
                continue;
            GameObject go = value.go;
            if (go == null) continue;
            if (!string.IsNullOrEmpty(sceneBinding.subPath) && sceneBinding.subPath != "$gua")
            {
                Transform t = go.transform.Find(sceneBinding.subPath);
                if (t != null) go = t.gameObject;
            }
            if (sceneBinding.track is AnimationTrack animTrack)
            {
                Animator component = go.GetComponent<Animator>();
                if (component != null && component.runtimeAnimatorController != null)
                    CheckAnimationTrack(animTrack, component);
            }
            if (sceneBinding.subPath == "$gua") continue;
            Object bound = go;
            if (!string.IsNullOrEmpty(sceneBinding.componentType))
            {
                Component c = go.GetComponent(sceneBinding.componentType);
                if (c != null) bound = c;
            }
            playableDirector.SetGenericBinding(sceneBinding.track, bound);
        }
        foreach (P08MixBinding mixBinding in MixBindings)
        {
            if (string.IsNullOrEmpty(mixBinding.key) || !dict.TryGetValue(mixBinding.key, out var value2) || value2 == null || value2.go == null)
                continue;
            GameObject go = value2.go;
            if (!string.IsNullOrEmpty(mixBinding.subPath))
            {
                Transform t = go.transform.Find(mixBinding.subPath);
                if (t != null) go = t.gameObject;
            }
            if (string.IsNullOrEmpty(mixBinding.componentType) || mixBinding.component == null) continue;
            var vcam = mixBinding.component.GetComponent<CinemachineVirtualCamera>();
            if (vcam == null) continue;
            if (mixBinding.componentType == "CameraLookAt" || mixBinding.componentType == "CinemachineVirtualCamera")
                vcam.LookAt = go.transform;
            else if (mixBinding.componentType == "CameraFollow")
                vcam.Follow = go.transform;
            else if (mixBinding.componentType == "CameraBoth")
            {
                vcam.Follow = go.transform;
                vcam.LookAt = go.transform;
            }
        }
        foreach (P08CharBinding charBinding in CharBindings)
        {
            if (charBinding.go == null) continue;
            Animator a = charBinding.go.GetComponent<Animator>();
            if (a == null) continue;
            if (_dontLoadChar)
            {
                if (a.applyRootMotion && charBinding.enumRootMotion == P08CharBinding.EnumRootMontion.NON_ROOT_MONTION)
                    a.applyRootMotion = false;
                else if (!a.applyRootMotion && charBinding.enumRootMotion == P08CharBinding.EnumRootMontion.ROOT_MONTION)
                    a.applyRootMotion = true;
                continue;
            }
            a.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            if (a.applyRootMotion && charBinding.enumRootMotion == P08CharBinding.EnumRootMontion.NON_ROOT_MONTION)
                a.applyRootMotion = false;
            else if (!a.applyRootMotion && charBinding.enumRootMotion != P08CharBinding.EnumRootMontion.NON_ROOT_MONTION)
                a.applyRootMotion = true;
            if (a.runtimeAnimatorController != null && !_dontClearCtrl)
                a.runtimeAnimatorController = null;
        }
        playableDirector.RebuildGraph();
        playableDirector.played += OnPlay;
        if (!_dontHideOnStop)
            playableDirector.stopped += OnStop;
        if (!playableDirector.playOnAwake)
            SetCharActive(false);
    }

    private static bool CheckAnimationTrack(AnimationTrack animTrack, Animator ani)
    {
        return CheckAnimationTrack(animTrack, ani.runtimeAnimatorController.animationClips, ani.gameObject.name);
    }

    private static bool CheckAnimationTrack(AnimationTrack animTrack, AnimationClip[] clips, string goName)
    {
        bool result = true;
        foreach (TimelineClip clip in animTrack.GetClips())
        {
            if (!(clip.asset is AnimationPlayableAsset asset) || asset.clip != null) continue;
            AnimationClip found = null;
            foreach (var c in clips)
                if (c.name == clip.displayName) { found = c; break; }
            if (found == null)
            {
                Debug.LogError("===>>> " + goName + " 找不到动画: " + clip.displayName);
                result = false;
            }
            else
            {
                asset.clip = found;
            }
        }
        return result;
    }

    private void SetCharActive(bool flag)
    {
        if (!_dontLoadChar && Application.isPlaying)
        {
            foreach (var item in dict)
                if (item.Value.go != null) item.Value.go.SetActive(flag);
        }
        if (_timelineType != EnumTimelineType.TL_REAL_TIME && _timelineType != EnumTimelineType.TL_MULTIPLE) return;
        foreach (var item2 in dict)
        {
            P08CharBinding value = item2.Value;
            if (value.go == null) continue;
            Animator a = value.go.GetComponent<Animator>();
            if (a == null) continue;
            if (_dontLoadChar)
            {
                if (a.applyRootMotion && value.enumRootMotion == P08CharBinding.EnumRootMontion.NON_ROOT_MONTION)
                    a.applyRootMotion = false;
                else if (!a.applyRootMotion && value.enumRootMotion == P08CharBinding.EnumRootMontion.ROOT_MONTION)
                    a.applyRootMotion = true;
            }
            else if (a.applyRootMotion && value.enumRootMotion == P08CharBinding.EnumRootMontion.NON_ROOT_MONTION)
                a.applyRootMotion = false;
            else if (!a.applyRootMotion && value.enumRootMotion != P08CharBinding.EnumRootMontion.NON_ROOT_MONTION)
                a.applyRootMotion = true;
        }
    }
}
