// Ported from the game's P08Main hot-update DLL (AG_cache/re/hotfix/P08Main_src/
// ConstraintPointGroup.cs), as shipped: the named attach points on a character model
// (102202ui_custom: L_Hand, R_Hand) that ConstraintNodeTrack pins fx to.
using System;
using System.Collections.Generic;
using UnityEngine;

public class ConstraintPointGroup : MonoBehaviour
{
    [Serializable]
    public struct ConstraintPoint
    {
        public string point;
        public Transform transform;
    }

    [SerializeField]
    private List<ConstraintPoint> _points;

    private ConstraintPoint GetConstraintPoint(string point)
    {
        return _points.Find(p => p.point == point);
    }

    public Transform GetConstraintTransform(string point)
    {
        return GetConstraintPoint(point).transform;
    }
}
