// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src/YS.CustomTimelineTrack).
using System;
using System.ComponentModel;
using Unity.Cinemachine;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace YS.CustomTimelineTrack
{
    public enum PROPERTY_TYPE
    {
        LookAt,
        Follow
    }

    public class CinemachineBindPropertyNode : PlayableAsset
    {
        public PROPERTY_TYPE propertyType;
        public string gameObjectName = "";

        public override Playable CreatePlayable(PlayableGraph graph, GameObject owner) => Playable.Create(graph);
    }
}
