// The game's volume stack, as its pipeline sees it (reverse-engineered, see
// AG_cache/re/spec/SceneSetting$$Update.c): SceneSetting.Update writes the scene's own
// values into VolumeDefaultsManager defaults for EnvironmentSetting,
// CharacterEnvironmentSetting and PostProcessSetting; Volume profiles then override
// individual parameters (m_OverrideState), blended by weight in priority order - the
// pipeline only ever reads the resulting VolumeStack. This resolves one parameter the
// same way, using the rebuilt game classes (fields only) via reflection.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using UnityEngine;

public static class AGVolumes
{
    const BindingFlags F = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

    // component.parameter -> SceneSetting field that SceneSetting.Update writes as its default
    static readonly Dictionary<string, string> Defaults = new Dictionary<string, string>
    {
        {"EnvironmentSetting.tint", "_tint"},
        {"EnvironmentSetting.fogEnable", "_fogEnabled"},
        {"EnvironmentSetting.fogRotation", "_fogRotation"},
        {"EnvironmentSetting.fogColor", "_fogColor"},
        {"EnvironmentSetting.fogColor2", "_fogColor2"},
        {"EnvironmentSetting.fogStart", "_fogStart"},
        {"EnvironmentSetting.fogEnd", "_fogEnd"},
        {"EnvironmentSetting.fogHeight", "_fogHeight"},
        {"EnvironmentSetting.fogHeightGradient", "_fogHeightGradient"},
        {"EnvironmentSetting.bakeReflectionScale", "_bakeReflectionScale"},
        {"EnvironmentSetting.setLightProbe", "_setLightProbe"},
        {"EnvironmentSetting.sceneLightProbe", "_sceneLightProbe"},
        {"EnvironmentSetting.skyColor", "_skyColor"},
        {"EnvironmentSetting.equatorColor", "_equatorColor"},
        {"EnvironmentSetting.groundColor", "_groundColor"},
        {"EnvironmentSetting.realtimeShadowColor", "_realtimeShadowColor"},
        {"CharacterEnvironmentSetting.acceptLightProbe", "_sceneLightProbe"},
        {"CharacterEnvironmentSetting.probeLightingBase", "_probeLightingBase"},
        {"CharacterEnvironmentSetting.probeLightingScale", "_probeLightingScale"},
        {"PostProcessSetting.exporsure", "_exposure"},
        {"PostProcessSetting.contrast", "_contrast"},
        {"PostProcessSetting.threshold", "_threshold"},
        {"PostProcessSetting.invert", "_invert"},
        {"PostProcessSetting.grayness", "_grayness"},
        {"PostProcessSetting.darkness", "_darkness"},
    };

    static object Field(object o, string name)
    {
        if (o == null) return null;
        var f = o.GetType().GetField(name, F);
        return f?.GetValue(o);
    }

    static MonoBehaviour FindSceneSetting()
    {
        foreach (var mb in UnityEngine.Object.FindObjectsByType<MonoBehaviour>(FindObjectsSortMode.None))
            if (mb && mb.GetType().Name == "SceneSetting") return mb;
        return null;
    }

    struct Layer { public float priority, weight; public UnityEngine.Object component; }

    static List<Layer> _layers;
    static int _frame = -1;

    /// <summary>Volume profile components that apply at the camera, lowest priority first.</summary>
    static List<Layer> Layers(Vector3 camPos)
    {
        if (_layers != null && _frame == Time.frameCount && Application.isPlaying) return _layers;
        _frame = Time.frameCount;
        _layers = new List<Layer>();
        foreach (var v in UnityEngine.Object.FindObjectsByType<MonoBehaviour>(FindObjectsSortMode.None))
        {
            if (!v || v.GetType().Name != "Volume" || !v.isActiveAndEnabled) continue;
            float weight = Convert.ToSingle(Field(v, "weight") ?? 1f);
            bool global = Convert.ToInt32(Field(v, "m_IsGlobal") ?? 1) != 0;
            if (!global)
            {
                // local volume: inside one of its colliders (+ blendDistance, linear falloff)
                float blend = Convert.ToSingle(Field(v, "blendDistance") ?? 0f);
                float best = float.MaxValue;
                foreach (var c in v.GetComponents<Collider>())
                    if (c && c.enabled) best = Mathf.Min(best, (c.ClosestPoint(camPos) - camPos).magnitude);
                if (best > blend) continue;
                if (blend > 0f) weight *= 1f - best / blend;
            }
            var profile = Field(v, "sharedProfile") as UnityEngine.Object;
            if (!(Field(profile, "components") is System.Collections.IEnumerable comps)) continue;
            foreach (var c in comps)
                if (c is UnityEngine.Object uo && uo)
                    _layers.Add(new Layer { priority = Convert.ToSingle(Field(v, "priority") ?? 0f), weight = weight, component = uo });
        }
        _layers = _layers.OrderBy(l => l.priority).ToList();
        return _layers;
    }

    /// <summary>component ("EnvironmentSetting"), parameter ("fogStart") -> resolved value.</summary>
    public static T Get<T>(string component, string param, T fallback, Vector3 camPos)
    {
        object value = null;
        if (Defaults.TryGetValue(component + "." + param, out var ssField))
            value = Field(FindSceneSetting(), ssField);
        if (component == "EnvironmentSetting" && param == "bakeReflectionTex")
        {
            var ss = FindSceneSetting();
            value = Convert.ToInt32(Field(ss, "_bakeReflection") ?? 0) != 0 ? Field(ss, "bakeReflectionTex") : null;
        }
        foreach (var l in Layers(camPos))
        {
            if (l.component.GetType().Name != component) continue;
            if (Convert.ToInt32(Field(l.component, "active") ?? 1) == 0) continue;
            var p = Field(l.component, param);
            if (p == null || Convert.ToInt32(Field(p, "m_OverrideState") ?? 0) == 0) continue;
            var v = Field(p, "m_Value");
            value = Interp(value, v, l.weight);
        }
        return Cast(value, fallback);
    }

    static object Interp(object from, object to, float t)
    {
        if (t >= 1f || from == null) return t > 0f ? to : from;
        switch (to)
        {
            case float b when from is float a: return Mathf.Lerp(a, b, t);
            case Color b when from is Color a: return Color.Lerp(a, b, t);
            case Vector4 b when from is Vector4 a: return Vector4.Lerp(a, b, t);
            default: return t > 0f ? to : from;
        }
    }

    static T Cast<T>(object v, T fallback)
    {
        if (v == null) return fallback;
        if (v is T t) return t;
        try
        {
            if (typeof(T) == typeof(bool)) return (T)(object)(Convert.ToInt32(v) != 0);
            return (T)Convert.ChangeType(v, typeof(T));
        }
        catch { return fallback; }
    }
}
