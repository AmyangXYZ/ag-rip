// Ported from the game's P08.Timeline hot-update DLL (AG_cache/re/hotfix/P08Timeline_src/
// ConstraintNodeTrack.cs), as shipped: while a clip plays, its child object follows a
// point of the character - a ParentConstraint (no offset) to the transform the model's
// ConstraintPointGroup names (constraintPoint), or to the object at parentPath
// (isUsePath). 102202 touch2: the moon rides her left hand, the trails her right. The
// track is bound to the model's HeroUITimelineBrain (HeroUITimelineBrain.BindPlayableDirector).
using System;
using System.ComponentModel;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

[Serializable]
[DisplayName("UI.Timeline/节点约束轨道(ConstraintNodeTrack)")]
[TrackClipType(typeof(ParentConstrainNode))]
[TrackBindingType(typeof(HeroUITimelineBrain))]
public class ConstraintNodeTrack : TrackAsset
{
    public string parentPath;

    public string constraintPoint;

    public bool isUsePath;

    protected override Playable CreatePlayable(PlayableGraph graph, GameObject gameObject, TimelineClip clip)
    {
        if (clip.asset is ParentConstrainNode node)
        {
            var director = graph.GetResolver() as PlayableDirector;
            var brain = director != null ? director.GetGenericBinding(this) as HeroUITimelineBrain : null;
            if (brain == null) return Playable.Null;
            GameObject target = brain.FindTarget(parentPath, director.transform);
            if (target == null) target = brain.gameObject;
            if (isUsePath)
            {
                node.parent = target;
            }
            else
            {
                var group = target.GetComponentInChildren<ConstraintPointGroup>();
                if (group == null)
                {
                    Debug.LogError($"ParentConstrainBehaviour 无法从 {target} 上找到 ConstraintPointGroup");
                    return Playable.Null;
                }
                Transform point = group.GetConstraintTransform(constraintPoint);
                if (point == null)
                {
                    Debug.LogError($"ParentConstrainBehaviour 无法从 {target} 上找到约束点 {constraintPoint}");
                    return Playable.Null;
                }
                node.parent = point.gameObject;
            }
        }
        return base.CreatePlayable(graph, gameObject, clip);
    }
}
