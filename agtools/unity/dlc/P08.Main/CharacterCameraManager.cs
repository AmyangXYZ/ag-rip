// Ported from the game's P08Main hot-update DLL (AG_cache/re/hotfix/P08Main_src/
// CharacterCameraManager.cs, CameraCfg.cs, CameraCfgGroup.cs): the home camera's setup -
// SetCameraParams, SetActiveCamera, ResetCameraDefaultCfg, SetCameraBlend - as shipped.
// Touch dragging, elasticity and tweens (the player turning the camera) are not ported:
// a sequence played without touching never runs them.
// Cinemachine here is Unity 6.6's built-in 3.x line, whose deprecated 2.x components
// (CinemachineFreeLook, CinemachineComposer) are the ones the game's data describes.
using System;
using System.Collections.Generic;
using Unity.Cinemachine;
using UnityEngine;

#pragma warning disable CS0618 // the game's own 2.x Cinemachine components

[Serializable]
public class CameraCfg
{
    public bool warpX;
    public float minScreenX;
    public float maxScreenX;
    public float defaultX;
    public float minComposerX;
    public float maxComposerX = 1f;
    public float defaultComposerX = 0.5f;
    public bool warpY;
    public float minScreenY;
    public float maxScreenY;
    public float defaultY;
    public float radius;
    public Vector3 rigHeight;
    public Vector3 dummyPosition;
    [Range(1f, 179f)] public float fov = -1f;
    [Range(-180f, 180f)] public float dutch = -99999f;
    public float defaultAxisY => 0.5f;
}

[Serializable]
public class CameraCfgGroup
{
    public List<CameraCfg> cameraCfgS;
    public bool overrideMoveSpeed;
    public Vector2 overrideMoveSpeedValue;
}

public class CharacterCameraManager : MonoBehaviour
{
    public List<CinemachineFreeLook> cinemachineFreeLookList;
    public List<Transform> dummyTrsList;
    [SerializeField] float moveSpeedX = 0.05f;
    [SerializeField] float moveSpeedY = 0.002f;
    [SerializeField] float maxMoveSpeedX = 20f;
    [SerializeField] float maxMoveSpeedY = 0.05f;
    [SerializeField] float deltaTimeX = 0.1f;
    [SerializeField] float deltaTimeY = 0.1f;
    [SerializeField] float elasticityAreaX = 5f;
    [SerializeField] float elasticityAreaY = 0.1f;
    [SerializeField] float elasticityAreaSpeed = 0.1f;
    [SerializeField] float elasticityAreaSpeedPassive = 0.1f;
    [SerializeField] AnimationCurve aimCurver;
    [SerializeField] float cameraChangeTime = 1f;
    [SerializeField] float tweenToDefaultTime = 0.3f;
    [Range(0.001f, 1f)] public float moveSpeedAffectComposerFactor = 1f;
    public int lastCameraIndex = -1;
    [SerializeField] List<CameraCfgGroup> cameraCfgGroupS;

    const int PRIORITY = 1000;
    List<CinemachineComposer> cinemachineComposerList;
    CinemachineFreeLook lastCamera;
    CameraCfgGroup currentCameraGroupCfg;

    void Awake()
    {
        if (cinemachineFreeLookList == null) return;
        cinemachineComposerList = new List<CinemachineComposer>(cinemachineFreeLookList.Count);
        foreach (var f in cinemachineFreeLookList)
            cinemachineComposerList.Add(f.GetRig(1).GetCinemachineComponent<CinemachineComposer>());
    }

    CameraCfg GetCameraCfg(int index)
        => index >= currentCameraGroupCfg.cameraCfgS.Count ? currentCameraGroupCfg.cameraCfgS[0] : currentCameraGroupCfg.cameraCfgS[index];

    Transform GetdummyTrs(int index) => index >= dummyTrsList.Count ? dummyTrsList[0] : dummyTrsList[index];

    public void SetActiveCamera(int index, bool cut = false, bool restore = true)
    {
        if (lastCameraIndex == index || cinemachineFreeLookList == null || cinemachineFreeLookList.Count < index) return;
        if (lastCamera != null) lastCamera.Priority = 0;
        SetCameraBlend(cut);
        lastCameraIndex = index;
        if (restore) ResetCameraDefaultCfg();
        cinemachineFreeLookList[index].Priority = PRIORITY;
        lastCamera = cinemachineFreeLookList[index];
        lastCamera.UpdateCameraState(Vector3.up, 0f);
    }

    public void SetCameraParams(int status)
    {
        if (cameraCfgGroupS == null || status >= cameraCfgGroupS.Count) return;
        currentCameraGroupCfg = cameraCfgGroupS[status];
        for (int i = 0; i < cinemachineFreeLookList.Count; i++)
        {
            var f = cinemachineFreeLookList[i];
            var cfg = GetCameraCfg(i);
            f.m_XAxis.m_Wrap = cfg.warpX;
            f.m_YAxis.m_Wrap = cfg.warpY;
            for (int o = 0; o < 3; o++) f.m_Orbits[o].m_Radius = cfg.radius;
            f.m_Orbits[0].m_Height = cfg.rigHeight[0];
            f.m_Orbits[1].m_Height = cfg.rigHeight[1];
            f.m_Orbits[2].m_Height = cfg.rigHeight[2];
            if (i < currentCameraGroupCfg.cameraCfgS.Count)
            {
                f.m_XAxis.m_MinValue = cfg.warpX ? cfg.minScreenX : cfg.minScreenX - elasticityAreaX;
                f.m_XAxis.m_MaxValue = cfg.warpX ? cfg.maxScreenX : cfg.maxScreenX + elasticityAreaX;
            }
            GetdummyTrs(i).localPosition = cfg.dummyPosition;
            var rig = f.GetRig(1);
            if (cfg.fov > 0f) rig.m_Lens.FieldOfView = cfg.fov;
            if (cfg.dutch >= -180f) rig.m_Lens.Dutch = cfg.dutch;
        }
    }

    public void ResetCameraDefaultCfg()
    {
        if (lastCamera == null || lastCameraIndex == -1 || lastCameraIndex >= currentCameraGroupCfg.cameraCfgS.Count) return;
        var f = cinemachineFreeLookList[lastCameraIndex];
        var composer = cinemachineComposerList[lastCameraIndex];
        var cfg = GetCameraCfg(lastCameraIndex);
        f.m_XAxis.Value = cfg.defaultX;
        f.m_YAxis.Value = cfg.defaultAxisY;
        composer.m_ScreenY = cfg.defaultY;
        f.UpdateCameraState(Vector3.up, 0f);
    }

    protected void SetCameraBlend(bool cut = false)
    {
        var brain = Camera.main.GetComponent<CinemachineBrain>();
        if (brain == null) brain = Camera.main.gameObject.AddComponent<CinemachineBrain>();
        var blend = default(CinemachineBlendDefinition);
        if (cut) blend.Style = CinemachineBlendDefinition.Styles.Cut;
        else
        {
            blend = new CinemachineBlendDefinition(CinemachineBlendDefinition.Styles.Custom, cameraChangeTime);
            blend.CustomCurve = aimCurver;
        }
        brain.DefaultBlend = blend;
    }
}
