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
    [DisplayName("剧情Timeline/相机/切换方式(StoryTimelineCameraCutType)")]
    [TrackClipType(typeof(StoryTimelineCameraCutTypeNode))]
    public class StoryTimelineCameraCutTypeTrack : TrackAsset
    {
    }
}
