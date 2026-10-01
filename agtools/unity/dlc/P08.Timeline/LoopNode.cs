// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src).
using System;
using UnityEngine;
using UnityEngine.Playables;

// A clip that loops the director: pausing within 0.1 s of its end jumps back one clip length.
public class LoopNode : PlayableAsset
{
    public override Playable CreatePlayable(PlayableGraph graph, GameObject owner)
        => ScriptPlayable<LoopNodeBehaviour>.Create(graph, 0);
}

public class LoopNodeBehaviour : PlayableBehaviour
{
    public override void OnBehaviourPause(Playable playable, FrameData info)
    {
        float time = (float)playable.GetTime();
        float duration = (float)playable.GetDuration();
        if (Math.Abs(duration - time) <= 0.1f && playable.GetGraph().GetResolver() is PlayableDirector director)
            director.time -= duration;
    }
}
