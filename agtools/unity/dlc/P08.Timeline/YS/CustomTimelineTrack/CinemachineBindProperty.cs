// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src/YS.CustomTimelineTrack).
using System;
using System.ComponentModel;
using Unity.Cinemachine;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace YS.CustomTimelineTrack
{
    // Bound by type to a CinemachineVirtualCameraBase, which the game's HeroUITimelineBrain
    // never binds: in game the track does nothing and the vcam keeps its prefab LookAt.
    [Serializable]
    [DisplayName("CinemachineExtend/CinemachineExtendBind")]
    [TrackBindingType(typeof(CinemachineVirtualCameraBase))]
    [TrackClipType(typeof(CinemachineBindPropertyNode))]
    public class CinemachineBindProperty : TrackAsset
    {
    }
}
