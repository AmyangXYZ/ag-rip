// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src): the data as
// shipped. The game plays the cue through its CRI ADX2 AudioManager; this project plays
// the sequence's extracted audio (voice + scene, agtools/extract_voice.py) from
// AGDlcPlayer instead, so the node makes no sound of its own.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using UnityEngine;
using UnityEngine.Playables;

[Serializable]
public class CriCueInfo
{
    public string mCueSheet;
    public string mCueName;
    public bool mUseStream;
}

[Serializable]
[DisplayName("开始播放区间")]
public class StoryCriwareNode : PlayableAsset
{
    public bool m_isComboSkillAsset;
    public string mCueSheet;
    public string mCueName;
    public string mCueAcb;
    public string mCueAwb;
    public int mCueId;
    public bool mUseStream;
    public bool mIsVoice;
    public bool mAutoPlayEnd;
    public bool mIsMusic;
    public bool m_useRandom;
    public List<int> m_weightList = new List<int>();
    public List<CriCueInfo> m_criCueInfoList = new List<CriCueInfo>();
    public bool mLoopIfPlaying;
    public bool mMarried;
    public CriCueInfo mCriCueInfoOfMarried = new CriCueInfo();
    [HideInInspector] public double CueLength = -1.0;

    public override double duration => CueLength == -1.0 ? 1.0 : CueLength;

    public override Playable CreatePlayable(PlayableGraph graph, GameObject owner)
        => Playable.Create(graph);
}
