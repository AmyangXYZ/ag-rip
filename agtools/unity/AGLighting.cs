// Keeps Unity's lightmapper away from the stages. The game ships no Unity lightmaps
// (every renderer has lightmap index 65535, LightmapSettings lists none) and its shaders'
// LIGHTMAP_ON variants do not compile (unity_Lightmap_HDR undeclared), so an editor bake
// ("Generate Lighting") turns the stage magenta. Every scene gets one shared
// LightingSettings asset with baked and realtime GI off.
// Runs from AGManifest.Batch.
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class AGLighting
{
    const string SettingsPath = "Assets/AGTools/AG_NoBake.lighting";

    static LightingSettings Settings()
    {
        var s = AssetDatabase.LoadAssetAtPath<LightingSettings>(SettingsPath);
        if (!s)
        {
            s = new LightingSettings { name = "AG_NoBake" };
            AssetDatabase.CreateAsset(s, SettingsPath);
        }
        s.bakedGI = false;
        s.realtimeGI = false;
        EditorUtility.SetDirty(s);
        return s;
    }

    public static int Lock()
    {
        var settings = Settings();
        int n = 0;
        foreach (var guid in AssetDatabase.FindAssets("t:Scene", new[] { "Assets" }))
        {
            var path = AssetDatabase.GUIDToAssetPath(guid);
            var scene = EditorSceneManager.OpenScene(path, OpenSceneMode.Single);
            if (Lightmapping.TryGetLightingSettings(out var current) && current == settings) continue;
            Lightmapping.lightingSettings = settings;
            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene);
            n++;
        }
        AssetDatabase.SaveAssets();
        Debug.Log("AGLighting: baking locked off in " + n + " scene(s)");
        return n;
    }
}
