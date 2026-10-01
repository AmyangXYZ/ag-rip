// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src): the data as
// shipped. The game's depth of field is its SimPipeline post effect, which this
// project's pipeline stand-in does not draw; 107402's only DoF track is muted.
using System;
using System.ComponentModel;
using UnityEngine;
using UnityEngine.Playables;

public enum DepthOfFieldType
{
    None,
    Low,
    High
}

[Serializable]
[DisplayName("相机景深")]
public class StoryCameraDepthOfFieldNode : PlayableAsset
{
    public DepthOfFieldType mDepthOfFieldType;
    public float mDepthOfFieldNear = 5f;
    public float mDepthOfFieldFar = 10f;
    public AnimationCurve mDepthOfFieldScale = new AnimationCurve(new Keyframe(0f, 1f), new Keyframe(1f, 1f));

    public override Playable CreatePlayable(PlayableGraph graph, GameObject owner) => Playable.Create(graph);
}
