// Plays a DLC skin's home-screen sequences back to back, bound the way the game binds
// them (Lua manager/heroraisetrack/herotimeline/herouitimeline.lua PlayAction and
// P08Main HeroUITimelineBrain.BindPlayableDirector, AG_cache/re/hotfix):
//   the model prefab Char/<modelId> (<skin>ui_custom) stands at HeroPosAndRotCfg (zero for
//   DLC skins); the UI main camera carries a CinemachineBrain; each sequence prefab
//   (UITimeLine/Charactor/<skin>/<seq>) goes under the model root at identity, every
//   output track named "@path" / "#path" / "&path" is bound to what that path names
//   (model root, then the sequence, then the scene), the model's Animator controller is
//   cleared, and the director is rebuilt, evaluated at 0 and played.
// The game plays a sequence's sound through CRI ADX2; here the sequence's extracted
// audio (voice + scene, one WAV) starts with it.
// Time runs at a fixed 30 fps (Time.captureFramerate) so every run is the same frame
// for frame - the ManualAnimator's cross-fades advance by Time.deltaTime.
using System;
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

    public GameObject modelPrefab;
    public string modelId;
    public List<Sequence> sequences = new List<Sequence>();
    public int frameRate = 30;
    public bool loopAll = true;

    public static event Action<string, PlayableDirector> SequenceStarted;
    public static event Action<string> SequenceEnded;
    public static event Action AllEnded;

    GameObject _model;
    AudioSource _audio;

    void Awake()
    {
        Time.captureFramerate = frameRate;
        EnsureCamera();
        var holder = new GameObject("Char");
        _model = Instantiate(modelPrefab, holder.transform);
        _model.name = modelId;
        _model.transform.localPosition = Vector3.zero;
        _model.transform.localRotation = Quaternion.identity;
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
        do
        {
            foreach (var seq in sequences)
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
        Bind(pd);
        pd.extrapolationMode = DirectorWrapMode.None;
        var saved = new List<(Animator, RuntimeAnimatorController)>();
        foreach (var a in _model.GetComponentsInChildren<Animator>(true))
        {
            saved.Add((a, a.runtimeAnimatorController));
            a.runtimeAnimatorController = null;
        }
        pd.RebuildGraph();
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

    // HeroUITimelineBrain.BindPlayableDirector: the name prefix marks a track to bind by
    // path; tracks without one keep their prefab binding.
    void Bind(PlayableDirector pd)
    {
        if (!(pd.playableAsset is TimelineAsset timeline)) return;
        foreach (var track in timeline.GetOutputTracks())
        {
            if (track.muted) continue;
            string n = track.name ?? "";
            if (n.Length < 2 || (n[0] != '@' && n[0] != '#' && n[0] != '&')) continue;
            string path = n.Substring(1);
            Transform t = _model.transform.Find(path) ?? pd.transform.Find(path);
            if (t == null)
            {
                var g = GameObject.Find(path);
                if (g) t = g.transform;
            }
            if (t == null)
            {
                Debug.LogWarning($"AGDlcPlayer: nothing at '{path}' for track {n}");
                continue;
            }
            // the track's declared binding type, as the game reads it
            Object bound = t.gameObject;
            var attrs = track.GetType().GetCustomAttributes(typeof(TrackBindingTypeAttribute), true);
            Type type = attrs.Length > 0 ? ((TrackBindingTypeAttribute)attrs[0]).type : null;
            if (type == typeof(Animator)) bound = t.GetComponentInChildren<Animator>(true);
            else if (type != null && type != typeof(GameObject) && typeof(Component).IsAssignableFrom(type))
                bound = t.GetComponentInChildren(type, true);
            pd.SetGenericBinding(track, bound);
        }
    }
}
