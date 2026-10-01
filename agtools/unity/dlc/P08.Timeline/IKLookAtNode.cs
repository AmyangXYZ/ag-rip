// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src).
using System.Collections.Generic;
using System.ComponentModel;
using UnityEngine;
using UnityEngine.Playables;

public enum ETargetType
{
    MainCamera,
    Transform
}

// Turns the character's head (or eyes) toward a target with a weight curve over the clip,
// through the LookAtComponents under the model's "Components" object.
[DisplayName("注视(IKLookAt)")]
public class IKLookAtNode : PlayableAsset
{
    public LookAtComponent.EBoneType boneType = LookAtComponent.EBoneType.Head;
    public ETargetType targetType = ETargetType.Transform;
    public ExposedReference<Transform> target;
    public AnimationCurve curve = new AnimationCurve(new Keyframe(0f, 1f), new Keyframe(1f, 1f));
    public bool disableOnFinished = true;

    public override Playable CreatePlayable(PlayableGraph graph, GameObject owner)
    {
        var playable = ScriptPlayable<IKLookAtBehaviour>.Create(graph, 0);
        var b = playable.GetBehaviour();
        b.boneType = boneType;
        b.targetType = targetType;
        b.target = target;
        b.curve = curve;
        b.disableOnFinished = disableOnFinished;
        return playable;
    }
}

public class IKLookAtBehaviour : PlayableBehaviour
{
    public LookAtComponent.EBoneType boneType;
    public ETargetType targetType;
    public ExposedReference<Transform> target;
    public AnimationCurve curve;
    public bool disableOnFinished;

    Transform _targetTrans;
    List<LookAtComponent> _comps;

    void GetLookAtComponent(object playerData)
    {
        if (_comps != null) return;
        var animator = playerData as Animator;
        if (animator == null) { Debug.LogError("IKLookAtNode.animator == null"); return; }
        Transform root = animator.transform.Find("Components");
        if (root == null) { Debug.LogError("IKLookAtNode Components Transform == null"); return; }
        LookAtComponent[] all = root.GetComponentsInChildren<LookAtComponent>();
        if (all == null || all.Length == 0) { Debug.LogError("IKLookAtNode.LookAtComponent == null"); return; }
        _comps = new List<LookAtComponent>();
        foreach (var c in all)
            if (c.eBoneType == boneType) _comps.Add(c);
    }

    public override void OnGraphStart(Playable playable)
    {
        if (targetType == ETargetType.MainCamera)
        {
            _targetTrans = Camera.main != null ? Camera.main.transform : null;
            return;
        }
        _targetTrans = target.Resolve(playable.GetGraph().GetResolver());
    }

    public override void OnPlayableDestroy(Playable playable)
    {
        base.OnPlayableDestroy(playable);
        _comps = null;
    }

    public override void ProcessFrame(Playable playable, FrameData info, object playerData)
    {
        GetLookAtComponent(playerData);
        if (_comps == null) return;
        float weight = curve.Evaluate((float)(playable.GetTime() / playable.GetDuration()));
        foreach (var c in _comps)
        {
            c.headWeight = weight;
            c.target = _targetTrans;
            c.enabled = true;
        }
    }

    public override void OnBehaviourPause(Playable playable, FrameData info)
    {
        base.OnBehaviourPause(playable, info);
        if (_comps == null || !disableOnFinished) return;
        foreach (var c in _comps)
            if (c != null) c.enabled = false;
    }
}
