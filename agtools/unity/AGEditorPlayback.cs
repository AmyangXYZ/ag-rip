// Plays the stage's own motion in Edit mode, so the scene looks as it does in the
// game without pressing Play: Animator clips (sampled through AnimationMode, so
// nothing is written into the scene), particle systems, and shader time (_Time
// scrolling, flicker) by repainting the views. Menu: AG > Animate in Edit Mode.
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;

[InitializeOnLoad]
public static class AGEditorPlayback
{
    const string Key = "AGEditorPlayback.on";
    const string Menu_ = "AG/Animate in Edit Mode";
    static double _last, _nextRepaint;
    static float _t;

    public static bool On
    {
        get => EditorPrefs.GetBool(Key, true);
        set => EditorPrefs.SetBool(Key, value);
    }

    static AGEditorPlayback()
    {
        EditorApplication.update += Tick;
        EditorApplication.playModeStateChanged += s => { if (s == PlayModeStateChange.ExitingEditMode) Stop(); };
        EditorSceneManager.sceneClosing += (s, r) => Stop();
        AssemblyReloadEvents.beforeAssemblyReload += Stop;
    }

    [MenuItem(Menu_)]
    static void Toggle() { On = !On; if (!On) Stop(); }

    [MenuItem(Menu_, true)]
    static bool ToggleCheck() { Menu.SetChecked(Menu_, On); return true; }

    static void Stop()
    {
        if (AnimationMode.InAnimationMode()) AnimationMode.StopAnimationMode();
    }

    // the clip the Animator starts in: default state of the first layer
    static AnimationClip StartClip(Animator a)
    {
        var rc = a.runtimeAnimatorController;
        if (rc == null) return null;
        if (rc is AnimatorController ac && ac.layers.Length > 0)
        {
            var st = ac.layers[0].stateMachine.defaultState;
            if (st != null && st.motion is AnimationClip c) return c;
        }
        return rc.animationClips.FirstOrDefault();
    }

    static void Tick()
    {
        if (Application.isPlaying || Application.isBatchMode || !On) return;
        double now = EditorApplication.timeSinceStartup;
        if (now < _nextRepaint) return;               // ~30 fps
        _nextRepaint = now + 1.0 / 30.0;
        float dt = (float)System.Math.Min(0.1, now - _last);
        _last = now;
        _t += dt;
        Step(_t, dt);
        SceneView.RepaintAll();
        UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
    }

    // shared with batch checks: advance every animated thing to time t
    public static void Step(float t, float dt)
    {
        var animators = Object.FindObjectsByType<Animator>(FindObjectsSortMode.None)
                              .Where(a => a.isActiveAndEnabled).ToList();
        if (animators.Count > 0)
        {
            if (!AnimationMode.InAnimationMode()) AnimationMode.StartAnimationMode();
            AnimationMode.BeginSampling();
            foreach (var a in animators)
            {
                var c = StartClip(a);
                if (c == null || c.length <= 0) continue;
                float ct = c.isLooping ? t % c.length : Mathf.Min(t, c.length);
                AnimationMode.SampleAnimationClip(a.gameObject, c, ct);
            }
            AnimationMode.EndSampling();
        }
        foreach (var ps in Object.FindObjectsByType<ParticleSystem>(FindObjectsSortMode.None))
        {
            if (!ps.gameObject.activeInHierarchy || !ps.main.playOnAwake) continue;
            // roots only: Simulate(withChildren) advances the sub-systems
            var p = ps.transform.parent;
            if (p != null && p.GetComponentInParent<ParticleSystem>() != null) continue;
            ps.Simulate(dt, true, false, false);
        }
        Shader.SetGlobalFloat("_AGEditorTime", t);
    }
}
