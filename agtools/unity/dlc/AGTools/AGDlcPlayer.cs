// Plays a DLC skin's home-screen sequences back to back, bound the way the game binds
// them (Lua manager/heroraisetrack/herotimeline/herouitimeline.lua PlayAction and
// P08Main HeroUITimelineBrain.BindPlayableDirector, AG_cache/re/hotfix):
//   the model prefab Char/<modelId> (<skin>ui_custom) stands at HeroPosAndRotCfg (zero for
//   DLC skins); the UI main camera carries a CinemachineBrain; each sequence prefab
//   (UITimeLine/Charactor/<skin>/<seq>) goes under the model root at identity, the
//   model root's HeroUITimelineBrain (added as the Lua side adds it) binds the tracks -
//   by binding type: itself for the brain's own tracks (ConstraintNodeTrack), the
//   component under what a "@path" / "#path" / "&path" name points at for the others -
//   the model's Animator controller is cleared, and the director is rebuilt, evaluated
//   at 0 and played.
// The game plays a sequence's sound through CRI ADX2; here the sequence's extracted
// audio (voice + scene, one WAV) starts with it.
// Time runs at a fixed 30 fps (Time.captureFramerate) so every run is the same frame
// for frame - the ManualAnimator's cross-fades advance by Time.deltaTime.
using System;
using System.Linq;
using System.Collections;
using System.Collections.Generic;
using Unity.Cinemachine;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;
using Object = UnityEngine.Object;

public class AGDlcPlayer : MonoBehaviour
{
    [Serializable]
    public class Sequence
    {
        public string name;
        public GameObject prefab;
        public AudioClip audio;
    }

    // a prefab a game script loads by its bundle path (DynamicTimelineTrackBinding's
    // CharBindings), for AGGameAssets
    [Serializable]
    public class GameAsset
    {
        public string path;
        public GameObject prefab;
    }

    public GameObject modelPrefab;
    public string modelId;
    public List<Sequence> sequences = new List<Sequence>();
    public List<GameAsset> gameAssets = new List<GameAsset>();
    public int frameRate = 30;
    public bool loopAll = true;

    public static event Action<string, PlayableDirector> SequenceStarted;
    public static event Action<string> SequenceEnded;
    public static event Action AllEnded;

    GameObject _model;
    HeroUITimelineBrain _brain;
    AudioSource _audio;

    void Awake()
    {
        Time.captureFramerate = frameRate;
        foreach (var a in gameAssets)
            AGGameAssets.Register(a.path, a.prefab);
        EnsureCamera();
        var holder = new GameObject("Char");
        _model = Instantiate(modelPrefab, holder.transform);
        _model.name = modelId;
        _model.transform.localPosition = Vector3.zero;
        _model.transform.localRotation = Quaternion.identity;
        // herouitimeline.lua PlayAction: GetComponent, else AddComponent
        _brain = _model.GetComponent<HeroUITimelineBrain>();
        if (_brain == null) _brain = _model.AddComponent<HeroUITimelineBrain>();
        // PosterGirlDlcActor.LoadModel -> UpdateCameraParams: the home camera group for the
        // centre view (ViewDirect.center = 0), then SetSelfCamera(0)
        var cm = _model.transform.Find("camera")?.GetComponent<CharacterCameraManager>();
        if (cm != null)
        {
            cm.SetCameraParams(0);
            cm.SetActiveCamera(0, false);
        }
        _audio = gameObject.AddComponent<AudioSource>();
        _audio.playOnAwake = false;
        _audio.spatialBlend = 0f;
    }

    static void EnsureCamera()
    {
        Camera cam = Camera.main;
        if (cam == null)
        {
            var go = new GameObject("Main Camera") { tag = "MainCamera" };
            cam = go.AddComponent<Camera>();
        }
        cam.allowHDR = true;
        cam.depthTextureMode = DepthTextureMode.Depth;
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = Color.black;
        cam.nearClipPlane = 0.05f;
        cam.farClipPlane = 2000f;
        if (!cam.GetComponent<AGSimPostFX>()) cam.gameObject.AddComponent<AGSimPostFX>();
        if (!cam.GetComponent<CinemachineBrain>()) cam.gameObject.AddComponent<CinemachineBrain>();
        if (!cam.GetComponent<AudioListener>()) cam.gameObject.AddComponent<AudioListener>();
    }

    IEnumerator Start()
    {
        yield return null;          // the home camera goes live first, as in the game
        // -agSeq a,b: only those sequences (a recording of one take)
        var only = (AGDlcCapture.Arg("-agSeq") ?? "").Split(',').Where(x => x.Length > 0).ToList();
        do
        {
            foreach (var seq in sequences)
                if (only.Count == 0 || only.Contains(seq.name))
                    yield return Play(seq);
            AllEnded?.Invoke();
        } while (loopAll);
    }

    IEnumerator Play(Sequence seq)
    {
        var tl = Instantiate(seq.prefab, _model.transform);
        tl.name = seq.prefab.name;
        tl.transform.localPosition = Vector3.zero;
        tl.transform.localRotation = Quaternion.identity;
        tl.transform.localScale = Vector3.one;
        tl.SetActive(true);
        var pd = tl.GetComponent<PlayableDirector>();
        _brain.BindPlayableDirector(pd);
        pd.extrapolationMode = DirectorWrapMode.None;
        var saved = new List<(Animator, RuntimeAnimatorController)>();
        foreach (var a in _model.GetComponentsInChildren<Animator>(true))
        {
            saved.Add((a, a.runtimeAnimatorController));
            a.runtimeAnimatorController = null;
        }
        pd.RebuildGraph();
        // the pipeline stand-in rescans every 2 s: the sequence's renderers (and what its
        // scripts spawned) get their rendering layer, object lights and shadow casting now
        AGSimPipeline.Ensure().Refresh();
        pd.time = 0;
        pd.Evaluate();
        pd.Play();
        pd.Evaluate();
        if (seq.audio)
        {
            _audio.clip = seq.audio;
            _audio.Play();
        }
        SequenceStarted?.Invoke(seq.name, pd);
        double length = pd.duration;
        while (pd != null && pd.state == PlayState.Playing && pd.time < length)
            yield return null;
        _audio.Stop();
        SequenceEnded?.Invoke(seq.name);
        foreach (var (a, c) in saved)
            if (a) a.runtimeAnimatorController = c;
        Destroy(tl);
        yield return null;
    }
}
