// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src/YS.CustomTimelineTrack).
using System;
using System.ComponentModel;
using Unity.Cinemachine;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace YS.CustomTimelineTrack
{
    [Serializable]
    [DisplayName("剧情Timeline/角色/IK(IKController)")]
    [TrackClipType(typeof(IKLookAtNode))]
    [TrackClipType(typeof(SlowFollowNode))]
    [TrackBindingType(typeof(Animator))]
    [TrackColor(1f, 0.1f, 0.7f)]
    public class IKControllerTrack : TrackAsset
    {
    }
}
