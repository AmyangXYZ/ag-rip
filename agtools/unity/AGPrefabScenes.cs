// Scenes for stages that ship as prefabs rather than scenes (the modifier-mode
// "SourceSpace" arenas are effect prefabs, each carrying its own SceneSetting):
// reads <project>/ag_prefab_scenes.txt, one "prefab asset path<TAB>scene name" per
// line, and writes Assets/AGScenes/<scene name>.unity with the prefab instanced at
// the origin, as the game spawns it.
// Batch (with the manifest): Unity.exe -batchmode -quit -projectPath <p> -executeMethod AGManifest.Batch
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class AGPrefabScenes
{
    public static int Build()
    {
        var project = Directory.GetParent(Application.dataPath).FullName;
        var list = Path.Combine(project, "ag_prefab_scenes.txt");
        if (!File.Exists(list)) return 0;
        Directory.CreateDirectory(Path.Combine(Application.dataPath, "AGScenes"));
        int n = 0;
        foreach (var line in File.ReadAllLines(list))
        {
            var parts = line.Split('\t');
            if (parts.Length < 2) continue;
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(parts[0]);
            if (!prefab) { Debug.LogWarning("AGPrefabScenes: no prefab at " + parts[0]); continue; }
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene);
            go.transform.SetPositionAndRotation(Vector3.zero, Quaternion.identity);
            EditorSceneManager.SaveScene(scene, "Assets/AGScenes/" + parts[1] + ".unity");
            n++;
        }
        AssetDatabase.SaveAssets();
        Debug.Log("AGPrefabScenes: " + n + " scene(s)");
        return n;
    }
}
