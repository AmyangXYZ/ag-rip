// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src).
using System;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

[Serializable]
public class ManualAnimationNode : PlayableAsset, ITimelineClipAsset
{
    public AnimationClip m_clip;
    public float m_blendTime = 0.3f;
    public Animator animator;

    [NonSerialized, HideInInspector] public double speed;

    public ClipCaps clipCaps => ClipCaps.ClipIn | ClipCaps.SpeedMultiplier;

    public override double duration => m_clip != null ? m_clip.length : 1.0;

    public AnimationClip curves { get => m_clip; internal set => m_clip = value; }

    public override Playable CreatePlayable(PlayableGraph graph, GameObject go)
    {
        if (m_clip == null || m_clip.legacy) return Playable.Null;
        var playable = ScriptPlayable<ManualAnimationBehaviour>.Create(graph, 0);
        var b = playable.GetBehaviour();
        b.m_clip = m_clip;
        b.m_blendTime = m_blendTime;
        b.m_animatior = animator;
        b.m_speed = speed;
        return playable;
    }
}

// The Animancer branch of the game's behaviour is taken only when the model carries an
// AnimancerComponent; DLC models do not, so only the ManualAnimator path is kept.
public class ManualAnimationBehaviour : PlayableBehaviour
{
    public AnimationClip m_clip;
    public float m_blendTime = 0.3f;
    public Animator m_animatior;
    public double m_speed;

    Playable m_playable;
    ManualAnimator manualAnimator;

    public override void OnGraphStart(Playable playable)
    {
        if (m_animatior == null) return;
        base.OnGraphStart(playable);
        m_animatior.TryGetComponent(out manualAnimator);
        if (!manualAnimator) manualAnimator = m_animatior.gameObject.AddComponent<ManualAnimator>();
    }

    public override void OnBehaviourPlay(Playable playable, FrameData info)
    {
        if (m_animatior == null || !Application.isPlaying || !manualAnimator) return;
        m_playable = manualAnimator.Play(playable.GetHashCode(), m_clip, playable.GetTime(), m_blendTime, m_speed);
    }

    public override void OnBehaviourPause(Playable playable, FrameData info)
    {
        m_playable = Playable.Null;
        if (m_animatior == null || !Application.isPlaying || !manualAnimator) return;
        manualAnimator.Stop(playable.GetHashCode(), m_clip);
    }

    public override void PrepareFrame(Playable playable, FrameData info)
    {
        base.PrepareFrame(playable, info);
        if (!m_playable.IsNull() && m_playable.IsValid())
            m_playable.SetTime((float)playable.GetTime());
    }
}
