// Ported from the game's P08Main hot-update DLL (AG_cache/re/hotfix/P08Main_src:
// LookAtComponent, LookAtBone, LookAtCompMgr). Behaviour as shipped.
using System;
using System.Collections.Generic;
using UnityEngine;

// Turns one bone (head or eye) toward a target after animation, clamped and sine-smoothed.
public class LookAtComponent : MonoBehaviour
{
    public enum EBoneType
    {
        None,
        Head,
        Eye
    }

    [Tooltip("骨骼类型,以便查询")] public EBoneType eBoneType = EBoneType.Head;
    [Tooltip("头骨Transform")] public Transform head;
    [Tooltip("目标Transform")] public Transform target;
    [Tooltip("旋转头骨的权重"), Range(0f, 1f)] public float headWeight = 0.8f;
    [Tooltip("IK位置权重或解算器的主权重"), Range(0f, 1f)] public float IKPositionWeight = 1f;
    [HideInInspector] public Vector3 IKPosition;
    [Tooltip("夹紧头骨的旋转"), Range(0f, 1f)] public float clampWeightHead = 0.5f;
    [Tooltip("夹紧的正弦平滑迭代次数"), Range(0f, 2f)] public int clampSmoothing = 2;
    [Tooltip("脊柱骨骼的权重分布")] public AnimationCurve spineWeightCurve = new AnimationCurve(new Keyframe(0f, 0.3f), new Keyframe(1f, 1f));

    Vector3[] headForwards = new Vector3[1];
    LookAtBone headBone;
    bool headIsEmpty => headBone == null;

    void Awake()
    {
        if (head != null && headBone == null)
        {
            headBone = new LookAtBone(head);
            headBone.Initiate(transform);
        }
    }

    void OnEnable() => LookAtCompMgr.instance.Registor(this);
    void OnDisable() { if (LookAtCompMgr.m_instance) LookAtCompMgr.m_instance.UnRegistor(this); }

    public void Manual_LateUpdate()
    {
        if (IKPositionWeight <= 0f || target == null) return;
        IKPositionWeight = Mathf.Clamp(IKPositionWeight, 0f, 1f);
        IKPosition = target.position;
        if (headWeight <= 0f || headIsEmpty) return;
        Vector3 forward = headBone.forward;
        Vector3 toward = Vector3.Lerp(forward, (IKPosition - head.transform.position).normalized, headWeight * IKPositionWeight).normalized;
        GetForwards(ref headForwards, forward, toward, 1, clampWeightHead);
        headBone.LookAt(headForwards[0], headWeight * IKPositionWeight);
    }

    protected Vector3[] GetForwards(ref Vector3[] forwards, Vector3 baseForward, Vector3 targetForward, int bones, float clamp)
    {
        if (clamp >= 1f || IKPositionWeight <= 0f)
        {
            for (int i = 0; i < forwards.Length; i++) forwards[i] = baseForward;
            return forwards;
        }
        float angle = Vector3.Angle(baseForward, targetForward);
        float dot = 1f - angle / 180f;
        float clampW = clamp > 0f ? Mathf.Clamp(1f - (clamp - dot) / (1f - dot), 0f, 1f) : 1f;
        float clampF = clamp > 0f ? Mathf.Clamp(dot / clamp, 0f, 1f) : 1f;
        for (int j = 0; j < clampSmoothing; j++) clampF = Mathf.Sin(clampF * (float)Math.PI * 0.5f);
        if (forwards.Length == 1)
            forwards[0] = Vector3.Slerp(baseForward, targetForward, clampF * clampW);
        else
        {
            float step = 1f / (forwards.Length - 1);
            for (int k = 0; k < forwards.Length; k++)
                forwards[k] = Vector3.Slerp(baseForward, targetForward, spineWeightCurve.Evaluate(step * k) * clampF * clampW);
        }
        return forwards;
    }
}

public class LookAtBone
{
    public Transform transform;
    public Vector3 axis = -Vector3.right;
    public Vector3 forward => transform.rotation * axis;

    public LookAtBone(Transform transform) { this.transform = transform; }

    public void Initiate(Transform root)
    {
        if (transform != null) axis = Quaternion.Inverse(transform.rotation) * root.forward;
    }

    public void LookAt(Vector3 direction, float weight)
    {
        if (transform == null) return;
        Quaternion delta = Quaternion.FromToRotation(forward, direction);
        Quaternion rotation = transform.rotation;
        transform.rotation = Quaternion.Lerp(rotation, delta * rotation, weight);
    }
}

// Runs every LookAtComponent after animation, heads before eyes.
public class LookAtCompMgr : MonoBehaviour
{
    public static LookAtCompMgr m_instance;
    readonly List<LookAtComponent> m_comps = new List<LookAtComponent>();

    public static LookAtCompMgr instance
    {
        get
        {
            if (m_instance == null) m_instance = new GameObject("LookAtCompMgr").AddComponent<LookAtCompMgr>();
            return m_instance;
        }
    }

    public void Registor(LookAtComponent comp)
    {
        if (m_comps.Contains(comp)) return;
        if (comp.eBoneType == LookAtComponent.EBoneType.Head) m_comps.Insert(0, comp);
        else m_comps.Add(comp);
    }

    public void UnRegistor(LookAtComponent comp) => m_comps.Remove(comp);

    void LateUpdate()
    {
        for (int i = 0; i < m_comps.Count; i++)
            if (m_comps[i] != null) m_comps[i].Manual_LateUpdate();
    }
}
