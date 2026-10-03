// Builds the press-Play scene for a DLC skin: the stage scene saved as
// Assets/AGScenes/DLC_<skin>.unity with an AGDlcPlayer holding the model prefab, the
// sequence prefabs and their audio (listed in ag_dlc.json by agtools/dlc_play.py).
//   Unity -batchmode -quit -projectPath <p> -executeMethod AGDlcScene.Batch
// or menu AG > Build DLC scene.
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class AGDlcScene
{
    [System.Serializable] class Seq { public string name; public string prefab; public string audio; }
    [System.Serializable] class Spec { public string skin; public string modelId; public string model; public string stageScene; public Seq[] sequences; }

    [MenuItem("AG/Build DLC scene")]
    public static void Build()
    {
        var spec = JsonUtility.FromJson<Spec>(File.ReadAllText("ag_dlc.json"));
        var scene = EditorSceneManager.OpenScene(spec.stageScene, OpenSceneMode.Single);
        var player = new GameObject("AGDlcPlayer").AddComponent<AGDlcPlayer>();
        player.modelId = spec.modelId;
        player.modelPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(spec.model);
        if (player.modelPrefab == null) Debug.LogError("AGDlcScene: no model at " + spec.model);
        foreach (var s in spec.sequences)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(s.prefab);
            if (prefab == null) { Debug.LogError("AGDlcScene: no sequence prefab at " + s.prefab); continue; }
            player.sequences.Add(new AGDlcPlayer.Sequence
            {
                name = s.name,
                prefab = prefab,
                audio = string.IsNullOrEmpty(s.audio) ? null : AssetDatabase.LoadAssetAtPath<AudioClip>(s.audio),
            });
            // what the sequence's DynamicTimelineTrackBinding loads by bundle path:
            // Assets/Com*/ABResources/<path>.prefab
            foreach (var dtb in prefab.GetComponentsInChildren<DynamicTimelineTrackBinding>(true))
                foreach (var cb in dtb.CharBindings)
                {
                    if (string.IsNullOrEmpty(cb.path) || player.gameAssets.Exists(a => a.path == cb.path)) continue;
                    string want = ("/ABResources/" + cb.path + ".prefab").ToLowerInvariant();
                    string found = null;
                    foreach (var g in AssetDatabase.FindAssets(Path.GetFileName(cb.path) + " t:Prefab"))
                    {
                        string p = AssetDatabase.GUIDToAssetPath(g);
                        if (p.ToLowerInvariant().EndsWith(want)) { found = p; break; }
                    }
                    if (found == null) { Debug.LogError($"AGDlcScene: {s.name} loads '{cb.path}' - no Assets/*/ABResources/{cb.path}.prefab"); continue; }
                    player.gameAssets.Add(new AGDlcPlayer.GameAsset { path = cb.path, prefab = AssetDatabase.LoadAssetAtPath<GameObject>(found) });
                    Debug.Log($"AGDlcScene: {s.name} loads {cb.path} = {found}");
                }
        }
        Directory.CreateDirectory("Assets/AGScenes");
        string path = $"Assets/AGScenes/DLC_{spec.skin}.unity";
        EditorSceneManager.SaveScene(scene, path, true);
        EditorSceneManager.OpenScene(path, OpenSceneMode.Single);
        Debug.Log($"AGDlcScene: {path}, {player.sequences.Count} sequence(s)");
    }

    public static void Batch()
    {
        Build();
        AssetDatabase.SaveAssets();
    }

    // A fresh project's one launch (agtools/dlc_play.py): the stage project's import and
    // manifest (AGManifest.Batch), then the DLC scene.
    public static void BatchAll()
    {
        AGManifest.Batch();
        Batch();
    }

    // Open the DLC scene and enter Play mode; AGDlcCapture (runtime, -agOut) writes the
    // frames and exits the editor when the last sequence ends. Run without -quit.
    public static void Capture()
    {
        var spec = JsonUtility.FromJson<Spec>(File.ReadAllText("ag_dlc.json"));
        EditorSceneManager.OpenScene($"Assets/AGScenes/DLC_{spec.skin}.unity", OpenSceneMode.Single);
        EditorApplication.isPlaying = true;
    }
}
