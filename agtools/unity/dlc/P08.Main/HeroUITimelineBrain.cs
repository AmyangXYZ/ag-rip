// Ported from the game's P08Main hot-update DLL (AG_cache/re/hotfix/P08Main_src/
// HeroUITimelineBrain.cs): how a home-screen sequence's tracks are bound. The Lua side
// (manager/heroraisetrack/herouitimeline.lua PlayAction) adds this component to the
// model root, parents the sequence prefab under it, then calls BindPlayableDirector and
// RebuildPlayableDirector.
//   BindPlayableDirector  every unmuted output track by its declared binding type:
//                         HeroUITimelineBrain -> this (ConstraintNodeTrack, HeroLoopTrack,
//                         CharacterRenderControllerTrack); Animator / CharacterEffect /
//                         CharacterEffectOverrider / CriLipsExPlayer / SceneSetting -> that
//                         component under the object the track's "@path" names; a track
//                         without a path keeps its prefab binding
//   FindTarget            the path under the model root, then under the sequence, then
//                         GameObject.Find, then each additional scene's roots
// The component types other than Animator live in assemblies P08.Main does not reference
// (P08.RenderPipeline, P08.Base, P08.ReplicaExt.Runtime), so they are matched by name -
// the same types the game names. Not ported: the signal markers (no DLC sequence here
// carries any; a warning names one if it does) and PlayEffect/StopEffect (Lua-driven
// pooled fx, not timeline content).
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.SceneManagement;
using UnityEngine.Timeline;

public class HeroUITimelineBrain : MonoBehaviour
{
    public bool isPoseLooping;

    public string talking;

    private GameObject _charGo;

    private static List<GameObject> _cacheList = new List<GameObject>();

    private bool isDlc
    {
        get
        {
            if (!gameObject.name.Contains("custom"))
                return gameObject.CompareTag("DLCCharacter");
            return true;
        }
    }

    public GameObject GetCharModelGo()
    {
        if (_charGo == null)
        {
            Animator a = GetComponentInChildren<Animator>();
            if (a == null) Debug.LogError(gameObject.name + "无法找到角色模型GameObject");
            else _charGo = a.gameObject;
        }
        return _charGo;
    }

    public void BindPlayableDirector(PlayableDirector pd)
    {
        TimelineAsset timelineAsset = pd.playableAsset as TimelineAsset;
        if (timelineAsset == null)
        {
            Debug.LogError(gameObject.name + " could not bind timeline for " + pd.gameObject.name);
            return;
        }
        for (int i = 0; i < timelineAsset.outputTrackCount; i++)
        {
            TrackAsset outputTrack = timelineAsset.GetOutputTrack(i);
            if (outputTrack.mutedInHierarchy) continue;
            var attr = (TrackBindingTypeAttribute)Attribute.GetCustomAttribute(outputTrack.GetType(), typeof(TrackBindingTypeAttribute));
            if (attr == null) continue;
            Type type = attr.type;
            if (type == typeof(HeroUITimelineBrain))
                pd.SetGenericBinding(outputTrack, this);
            else if (type == typeof(Animator))
                BindComponent(pd, outputTrack, type, false);
            else if (type != null && (type.Name == "CharacterEffect" || type.Name == "CriLipsExPlayer" || type.Name == "SceneSetting"))
                BindComponent(pd, outputTrack, type, false);
            else if (type != null && type.Name == "CharacterEffectOverrider")
                BindComponent(pd, outputTrack, type, true);
            if (outputTrack.GetMarkerCount() > 0)
                Debug.LogWarning($"HeroUITimelineBrain: the markers on {outputTrack.name} are not ported");
        }
    }

    public void RebuildPlayableDirector(PlayableDirector pd)
    {
        pd.RebuildGraph();
        pd.playableGraph.Evaluate(0f);
    }

    public GameObject FindTarget(string targetPath, Transform extraTrans = null)
    {
        if (string.IsNullOrEmpty(targetPath)) return null;
        Transform t = transform.Find(targetPath);
        if (t != null) return t.gameObject;
        t = extraTrans != null ? extraTrans.Find(targetPath) : null;
        if (t != null) return t.gameObject;
        GameObject go = GameObject.Find(targetPath);
        if (go != null) return go;
        int sceneCount = SceneManager.sceneCount;
        for (int i = 1; i < sceneCount; i++)
        {
            SceneManager.GetSceneAt(i).GetRootGameObjects(_cacheList);
            foreach (GameObject cache in _cacheList)
            {
                t = cache.transform.Find(targetPath);
                if (t != null) return t.gameObject;
            }
        }
        return null;
    }

    // BindAnimator / BindCharacterEffect / BindCriLipsExPlayer / BindSceneSeting, and
    // BindCharacterEffectOverrider, which adds the component when it is missing
    private void BindComponent(PlayableDirector pd, TrackAsset track, Type type, bool add)
    {
        GameObject go = FindBindTarget(pd, track);
        if (go == null) return;
        Component c = go.GetComponentInChildren(type);
        if (c == null && add) c = go.AddComponent(type);
        if (c != null) pd.SetGenericBinding(track, c);
    }

    private GameObject FindBindTarget(PlayableDirector pd, TrackAsset track)
    {
        string text = track.name;
        bool flag = text.StartsWith("@") || text.StartsWith("#") || text.StartsWith("&");
        if (!flag && pd.GetGenericBinding(track) != null) return null;
        if (flag)
        {
            GameObject go = FindTarget(text.Substring(1), pd.transform);
            if (go != null) return go;
            Debug.LogError("无法找到绑定对象: " + pd.gameObject.name + "的轨道" + track.GetType().Name + "(" + track.name + ")");
            return null;
        }
        return isDlc ? null : gameObject;
    }
}
