// Ported from the game's P08Main hot-update DLL (AG_cache/re/hotfix/P08Main_src). Behaviour
// as shipped; LuaForUtil.IsManualAnimatorBlend is the game's default, true.
using System.Collections;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

// The character's body motion in a DLC sequence: its own PlayableGraph with a two-input
// mixer, each new clip cross-faded in over the clip node's blend time.
public class ManualAnimator : MonoBehaviour
{
    PlayableGraph m_graph;
    AnimationPlayableOutput m_output;
    AnimationMixerPlayable m_mixRoot;
    int currentClipIndex = -1;
    int currentHashCode;
    double _lastSpeed = 1.0;
    bool _isBlending;
    const double SENSELESS_WEIGHT = 0.005;

    void Awake()
    {
        m_graph = PlayableGraph.Create("ManualAnimator" + gameObject.name + gameObject.GetHashCode());
        var output = AnimationPlayableOutput.Create(m_graph, "AnimationOutput", GetComponent<Animator>());
        m_mixRoot = AnimationMixerPlayable.Create(m_graph, 2);
        output.SetSourcePlayable(m_mixRoot);
        m_output = output;
    }

    void OnEnable() => m_graph.Play();

    void OnDisable()
    {
        _isBlending = false;
        StopAllCoroutines();
        for (int i = 0; i < 2; i++)
        {
            Playable input = m_mixRoot.GetInput(i);
            if (!input.IsNull())
            {
                DestoryPlayable(input);
                m_mixRoot.DisconnectInput(i);
            }
        }
        currentClipIndex = -1;
        currentHashCode = 0;
        m_graph.Stop();
    }

    void DestoryPlayable(Playable playable)
    {
        if (playable.IsNull()) return;
        int n = playable.GetInputCount();
        for (int i = 0; i < n; i++) DestoryPlayable(playable.GetInput(i));
        m_graph.DestroyPlayable(playable);
    }

    void CullSenselessBlending(AnimationMixerPlayable mixer, double mixerWeight)
    {
        if (mixer.IsNull()) return;
        int n = mixer.GetInputCount();
        for (int i = 0; i < n; i++)
        {
            Playable input = mixer.GetInput(i);
            if (input.IsNull()) continue;
            double w = mixerWeight * mixer.GetInputWeight(i);
            if (w < SENSELESS_WEIGHT)
            {
                DestoryPlayable(input);
                mixer.DisconnectInput(i);
            }
            else if (input.GetPlayableType() == typeof(AnimationMixerPlayable))
                CullSenselessBlending((AnimationMixerPlayable)input, w);
        }
    }

    public Playable Play(int hashCode, AnimationClip clip, double time, float blendTime = 0.3f, double speed = 1.0)
    {
        if (clip == null) return Playable.Null;
        if (currentClipIndex == -1)
        {
            var first = AnimationClipPlayable.Create(m_graph, clip);
            first.SetTime(time);
            first.SetSpeed(0.0);
            m_mixRoot.ConnectInput(0, first, 0);
            m_mixRoot.SetInputWeight(0, 1f);
            currentClipIndex = 0;
            currentHashCode = hashCode;
            _lastSpeed = speed;
            m_graph.Evaluate(0f);
            return first;
        }
        if (hashCode == currentHashCode)
        {
            Playable same = m_mixRoot.GetInput(currentClipIndex);
            same.SetTime(time);
            return same;
        }
        int keep = _isBlending ? 1 - currentClipIndex : currentClipIndex;
        int slot = 1 - keep;
        Playable old = m_mixRoot.GetInput(slot);
        Playable last = m_mixRoot.GetInput(keep);
        if (!old.IsNull())
        {
            if (_isBlending)
            {
                var nested = AnimationMixerPlayable.Create(m_graph, 2);
                nested.SetSpeed(0.0);
                float wOld = m_mixRoot.GetInputWeight(slot);
                float wLast = m_mixRoot.GetInputWeight(keep);
                m_mixRoot.DisconnectInput(keep);
                m_mixRoot.DisconnectInput(slot);
                m_graph.Connect(old, 0, nested, 0);
                m_graph.Connect(last, 0, nested, 1);
                nested.SetInputWeight(0, wOld);
                nested.SetInputWeight(1, wLast);
                old.SetSpeed(1.0);
                last.SetSpeed(1.0);
                m_graph.Connect(nested, 0, m_mixRoot, keep);
                m_mixRoot.SetInputWeight(keep, 1f);
                m_graph.Evaluate(0f);
                CullSenselessBlending(nested, 1.0);
            }
            else
            {
                DestoryPlayable(old);
                m_mixRoot.DisconnectInput(slot);
            }
        }
        var next = AnimationClipPlayable.Create(m_graph, clip);
        next.SetTime(time);
        next.SetSpeed(0.0);
        m_mixRoot.ConnectInput(slot, next, 0);
        if (!last.IsNull())
        {
            StopAllCoroutines();
            StartCoroutine(CoroutineFunc(slot, keep, blendTime, _lastSpeed));
        }
        else
        {
            m_mixRoot.SetInputWeight(slot, 1f);
            m_mixRoot.SetInputWeight(keep, 0f);
        }
        currentHashCode = hashCode;
        currentClipIndex = slot;
        _lastSpeed = speed;
        return next;
    }

    public void Stop(int hashCode, AnimationClip clip) { }

    IEnumerator CoroutineFunc(int index, int lastIndex, float duration, double lastSpeed)
    {
        Playable lastPlayable = m_mixRoot.GetInput(lastIndex);
        bool hasLast = !lastPlayable.IsNull();
        m_mixRoot.SetInputWeight(index, 0f);
        m_mixRoot.SetInputWeight(lastIndex, 1f);
        _isBlending = true;
        float timer = 0f;
        while (timer < duration)
        {
            float w = Mathf.Clamp01(timer / duration);
            timer += Time.deltaTime;
            m_mixRoot.SetInputWeight(index, w);
            m_mixRoot.SetInputWeight(lastIndex, 1f - w);
            if (hasLast) SetTimeRecursive(lastPlayable, Time.deltaTime, lastSpeed);
            yield return null;
        }
        m_mixRoot.SetInputWeight(index, 1f);
        m_mixRoot.SetInputWeight(lastIndex, 0f);
        _isBlending = false;
        if (hasLast)
        {
            DestoryPlayable(lastPlayable);
            m_mixRoot.DisconnectInput(lastIndex);
        }
    }

    static void SetTimeRecursive(Playable p, float dt, double speed)
    {
        if (p.IsNull() || !p.IsValid()) return;
        p.SetTime(p.GetTime() + dt * speed);
        for (int i = 0; i < p.GetInputCount(); i++) SetTimeRecursive(p.GetInput(i), dt, speed);
    }

    void OnDestroy() => m_graph.Destroy();
}
