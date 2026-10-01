// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src/YS.CustomTimelineTrack).
using System;
using System.ComponentModel;
using Unity.Cinemachine;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace YS.CustomTimelineTrack
{
    [DisplayName("剧情Timeline/相机/相机景深(StoryCameraDepthOfField)")]
    [TrackClipType(typeof(StoryCameraDepthOfFieldNode))]
    public class StoryCameraDepthOfFieldTrack : TrackAsset
    {
    }
}
