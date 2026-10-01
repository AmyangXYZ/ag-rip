// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src/YS.CustomTimelineTrack).
using System;
using System.ComponentModel;
using Unity.Cinemachine;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace YS.CustomTimelineTrack
{
    [DisplayName("剧情Timeline/后宅/UI 轨道")]
    [TrackColor(0.26332188f, 0.6738754f, 0.8529412f)]
    [TrackClipType(typeof(StoryTimelineDormMaskUINode))]
    public class StoryTimelineDormUITrack : TrackAsset
    {
        public override Playable CreateTrackMixer(PlayableGraph graph, GameObject go, int inputCount)
            => ScriptPlayable<StoryTimelineDormUITrackBehaviour>.Create(graph, inputCount);
    }

    public class StoryTimelineDormUITrackBehaviour : PlayableBehaviour
    {
        public override void PrepareFrame(Playable playable, FrameData info)
        {
            int n = playable.GetInputCount();
            for (int i = 0; i < n; i++)
            {
                Playable input = playable.GetInput(i);
                if (input.GetPlayableType() == typeof(StoryTimelineDormMaskUINodeBehaviour))
                    ((ScriptPlayable<StoryTimelineDormMaskUINodeBehaviour>)input).GetBehaviour().SetWeight(playable.GetInputWeight(i));
            }
        }
    }
}
