// Ported from the game's P08.Timeline hot-update DLL (decrypted with tools/re/cdph.py,
// decompiled with ILSpy: AG_cache/re/hotfix/P08Timeline_src). Behaviour as shipped.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;
using UnityEngine.Timeline;
using Object = UnityEngine.Object;

// At runtime the track drives no animation output of its own: each clip hands its
// AnimationClip to the character's ManualAnimator, which cross-fades between clips.
[Serializable]
[DisplayName("DLC配置/Animator混合轨道")]
[TrackClipType(typeof(ManualAnimationNode), false)]
[TrackBindingType(typeof(Animator))]
[TrackColor(0.39f, 0.2f, 0.53f)]
public class ManualAnimatorTrack : TrackAsset
{
    public override IEnumerable<PlayableBinding> outputs
    {
        get
        {
            if (!Application.isPlaying)
                yield return AnimationPlayableBinding.Create(name, this);
            else
                yield return ScriptPlayableBinding.Create(name, this, null);
        }
    }

    public override Playable CreateTrackMixer(PlayableGraph graph, GameObject go, int inputCount)
    {
        if (!Application.isPlaying)
            return AnimationMixerPlayable.Create(graph, inputCount);
        return Playable.Create(graph, inputCount);
    }

    protected override Playable CreatePlayable(PlayableGraph graph, GameObject go, TimelineClip clip)
    {
        if (clip.asset is ManualAnimationNode node)
        {
            node.animator = GetBinding(go != null ? go.GetComponent<PlayableDirector>() : null);
            node.speed = clip.timeScale;
        }
        return base.CreatePlayable(graph, go, clip);
    }

    internal Animator GetBinding(PlayableDirector director)
    {
        if (director == null) return null;
        Object key = isSubTrack ? (Object)parent : this;
        Object bound = director.GetGenericBinding(key);
        Animator animator = bound as Animator;
        if (animator == null && bound is GameObject go) animator = go.GetComponent<Animator>();
        return animator;
    }
}
