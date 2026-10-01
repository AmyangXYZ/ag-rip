// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src).
using System.ComponentModel;
using UnityEngine;
using UnityEngine.Playables;

// Moves a follower toward a target once it has turned or moved past a threshold, at a
// speed read off a curve of the remaining distance.
[DisplayName("缓动跟随(SlowFollow)")]
public class SlowFollowNode : PlayableAsset
{
    public ETargetType targetType = ETargetType.Transform;
    public ExposedReference<Transform> target;
    public ExposedReference<Transform> follower;
    public float angleThreshold = 999f;
    public float distanceThreshold = 999f;
    [Tooltip("x为归一化的距离")]
    public AnimationCurve speedCurve = new AnimationCurve(new Keyframe(0f, 1f), new Keyframe(1f, 1f));

    public override Playable CreatePlayable(PlayableGraph graph, GameObject owner)
    {
        var playable = ScriptPlayable<SlowFollowBehaviour>.Create(graph, 0);
        var b = playable.GetBehaviour();
        b.targetType = targetType;
        b.target = target;
        b.follower = follower;
        b.angleThreshold = angleThreshold;
        b.distanceThreshold = distanceThreshold;
        b.speedCurve = speedCurve;
        return playable;
    }
}

public class SlowFollowBehaviour : PlayableBehaviour
{
    public ETargetType targetType;
    public ExposedReference<Transform> target;
    public ExposedReference<Transform> follower;
    public float angleThreshold;
    public float distanceThreshold;
    public AnimationCurve speedCurve;

    Transform _targetTrans, _followerTrans;
    Quaternion _lastRot, _currentRot;
    Vector3 _lastPos, _currentPos, _targetPos;
    bool _isMoving;
    float _maxDistance;

    public override void OnGraphStart(Playable playable)
    {
        base.OnGraphStart(playable);
        Init(playable);
    }

    void Init(Playable playable)
    {
        var resolver = playable.GetGraph().GetResolver();
        _targetTrans = targetType == ETargetType.MainCamera
            ? (Camera.main != null ? Camera.main.transform : null)
            : target.Resolve(resolver);
        _followerTrans = follower.Resolve(resolver);
        if (_targetTrans == null || _followerTrans == null)
        {
            Debug.LogError("_targetTrans == null or _followerTrans == null");
            return;
        }
        _lastRot = _targetTrans.rotation;
        _targetPos = _lastPos = _targetTrans.position;
        _followerTrans.position = _targetPos;
        _isMoving = false;
    }

    public override void ProcessFrame(Playable playable, FrameData info, object playerData)
    {
        base.ProcessFrame(playable, info, playerData);
        if (_targetTrans == null || _followerTrans == null) return;
        if (Vector3.Distance(_targetTrans.position, _currentPos) > distanceThreshold) Init(playable);
        _currentRot = _targetTrans.rotation;
        _currentPos = _targetTrans.position;
        float angle = Quaternion.Angle(_lastRot, _currentRot);
        float dist = Vector3.Distance(_lastPos, _currentPos);
        if ((angle >= angleThreshold || dist >= distanceThreshold) && !_isMoving)
        {
            _lastRot = _currentRot;
            _lastPos = _currentPos;
            _targetPos = _targetTrans.position;
            _maxDistance = dist;
            _isMoving = true;
        }
        float sq = Vector3.SqrMagnitude(_followerTrans.position - _targetPos);
        if (sq <= 0.0001f) _isMoving = false;
        if (_isMoving)
        {
            _targetPos = _targetTrans.position;
            float x = 1f - Mathf.Clamp01(sq / (_maxDistance * _maxDistance));
            _followerTrans.position = Vector3.MoveTowards(_followerTrans.position, _targetPos, speedCurve.Evaluate(x) * info.deltaTime);
        }
    }
}
