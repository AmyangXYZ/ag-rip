// Ported from the game's P08.Timeline (AG_cache/re/hotfix/P08Timeline_src).
// The game instantiates UI/Dorm/StoryMaskUI (a full-screen Image named "mask") under its
// story canvas and colours it by the gradient over the clip. Here the same full-screen
// colour is drawn by a screen-space overlay canvas made on the spot.
using System.ComponentModel;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.UI;

[DisplayName("后宅-UI遮罩")]
public class StoryTimelineDormMaskUINode : PlayableAsset
{
    public string ui_path = "UI/Dorm/StoryMaskUI";
    public Gradient color_grad;

    public override Playable CreatePlayable(PlayableGraph graph, GameObject owner)
    {
        var playable = ScriptPlayable<StoryTimelineDormMaskUINodeBehaviour>.Create(graph, 0);
        playable.GetBehaviour().ui_path = ui_path;
        playable.GetBehaviour().color_grad = color_grad;
        return playable;
    }
}

public class StoryTimelineDormMaskUINodeBehaviour : PlayableBehaviour
{
    public string ui_path;
    public Gradient color_grad;
    GameObject ui_go;
    Image mask_img;
    bool isRunning;

    public void SetWeight(float weight)
    {
        if (isRunning && weight == 0f) { isRunning = false; Exit(); }
        if (!isRunning && weight == 1f) isRunning = true;
    }

    void Exit()
    {
        if (ui_go != null) Object.Destroy(ui_go);
        ui_go = null;
        mask_img = null;
    }

    public override void OnBehaviourPlay(Playable playable, FrameData info)
    {
        if (ui_go != null) return;
        ui_go = new GameObject("StoryMaskUI");
        var canvas = ui_go.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceOverlay;
        canvas.sortingOrder = 1000;
        var mask = new GameObject("mask", typeof(RectTransform));
        mask.transform.SetParent(ui_go.transform, false);
        var rt = (RectTransform)mask.transform;
        rt.anchorMin = Vector2.zero;
        rt.anchorMax = Vector2.one;
        rt.offsetMin = rt.offsetMax = Vector2.zero;
        mask_img = mask.AddComponent<Image>();
        mask_img.raycastTarget = false;
        mask_img.color = color_grad.Evaluate(0f);
    }

    public override void OnGraphStop(Playable playable) => Exit();

    public override void ProcessFrame(Playable playable, FrameData info, object playerData)
    {
        if (mask_img == null) return;
        mask_img.color = color_grad.Evaluate((float)(playable.GetTime() / playable.GetDuration()));
    }
}
