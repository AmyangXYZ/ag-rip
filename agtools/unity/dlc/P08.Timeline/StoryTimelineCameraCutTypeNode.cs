// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src).
using Unity.Cinemachine;
using UnityEngine;
using UnityEngine.Playables;

// Sets how the main camera's CinemachineBrain blends into the next virtual camera; the
// cut itself is the ActivationTrack that switches the VirtualCamera object on.
public class StoryTimelineCameraCutTypeNode : PlayableAsset
{
    public CinemachineBlendDefinition cameraBlend;

    public override Playable CreatePlayable(PlayableGraph graph, GameObject owner)
    {
        var playable = ScriptPlayable<StoryTimelineCameraCutTypeNodeBehaviour>.Create(graph, 0);
        playable.GetBehaviour().cameraBlend = cameraBlend;
        return playable;
    }
}

public class StoryTimelineCameraCutTypeNodeBehaviour : PlayableBehaviour
{
    public CinemachineBlendDefinition cameraBlend;
    CinemachineBrain brain;

    public override void OnBehaviourPlay(Playable playable, FrameData info)
    {
        base.OnBehaviourPlay(playable, info);
        if (brain == null && Camera.main != null) brain = Camera.main.GetComponent<CinemachineBrain>();
        if (brain == null) return;
        if (brain.IsBlending)
        {
            brain.enabled = false;
            brain.enabled = true;
        }
        brain.DefaultBlend = cameraBlend;
    }
}
