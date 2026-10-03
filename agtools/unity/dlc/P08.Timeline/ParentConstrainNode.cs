// Ported from the game's P08.Timeline hot-update DLL (AG_cache/re/hotfix/P08Timeline_src/
// ParentConstrainNode.cs, ParentConstrainBehaviour.cs), as shipped. See ConstraintNodeTrack.
using System;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

[Serializable]
public class ParentConstrainNode : PlayableAsset
{
    [SerializeField]
    public ExposedReference<GameObject> childGameObject;

    [HideInInspector]
    public GameObject parent;

    public override Playable CreatePlayable(PlayableGraph graph, GameObject owner)
    {
        var playable = ScriptPlayable<ParentConstrainBehaviour>.Create(graph, 0);
        var behaviour = playable.GetBehaviour();
        behaviour.child = childGameObject.Resolve(graph.GetResolver());
        behaviour.parent = parent;
        return playable;
    }
}

public class ParentConstrainBehaviour : PlayableBehaviour
{
    public GameObject child;

    public GameObject parent;

    private ParentConstraint parentConstraint;

    public override void OnBehaviourPlay(Playable playable, FrameData info)
    {
        base.OnBehaviourPlay(playable, info);
        if (parent == null)
        {
            Debug.LogError("ParentConstrainBehaviour has no parent!");
            if (parentConstraint != null) parentConstraint.constraintActive = false;
            return;
        }
        // UnityExtension.GetComponentOrAdd
        if (!child.TryGetComponent(out parentConstraint)) parentConstraint = child.AddComponent<ParentConstraint>();
        if (parentConstraint == null)
        {
            Debug.LogError("ParentConstrainBehaviour could not create ParentConstraint!");
            return;
        }
        if (parentConstraint.sourceCount == 0)
            parentConstraint.AddSource(new ConstraintSource { sourceTransform = parent.transform, weight = 1f });
        parentConstraint.constraintActive = true;
    }

    public override void OnBehaviourPause(Playable playable, FrameData info)
    {
        if (parentConstraint != null) parentConstraint.constraintActive = false;
    }
}
