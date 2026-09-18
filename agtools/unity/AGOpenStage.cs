// Opens the stage scene when the project is opened on Unity's empty "Untitled"
// scene (AssetRipper projects have no startup scene), frames it, and sets up the
// game-pipeline stand-in (AGSimPipeline + camera with the game's post chain) for
// every scene that is opened.
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

[InitializeOnLoad]
public static class AGOpenStage
{
    static AGOpenStage()
    {
        // The game renders in Linear. A bundles-only export can come out in Gamma (AssetRipper
        // writes ProjectSettings late), which silently changes every colour; enforce it here.
        if (PlayerSettings.colorSpace != ColorSpace.Linear)
        {
            Debug.Log("AGOpenStage: switching the project to Linear colour space (as the game)");
            PlayerSettings.colorSpace = ColorSpace.Linear;
        }
        EditorApplication.delayCall += Open;
        EditorSceneManager.sceneOpened += (s, m) => EditorApplication.delayCall += Setup;
    }

    static void Setup()
    {
        if (Application.isBatchMode) return;
        AGSimPipeline.Ensure();
        // Scene view starts where the Game view camera is (the game's viewpoint, if known)
        var cam = GameObject.Find("AGStageCamera (generated)");
        if (cam && SceneView.lastActiveSceneView != null && !SessionState.GetBool("AGOpenStage.aligned:" + cam.scene.path, false))
        {
            SessionState.SetBool("AGOpenStage.aligned:" + cam.scene.path, true);
            SceneView.lastActiveSceneView.AlignViewToObject(cam.transform);
        }
    }

    static void Open()
    {
        if (Application.isBatchMode) return;
        if (!SessionState.GetBool("AGOpenStage.done", false))
        {
            SessionState.SetBool("AGOpenStage.done", true);
            if (string.IsNullOrEmpty(EditorSceneManager.GetActiveScene().path))
            {
                // generated prefab scenes first (modifier spaces), then the stage's own
                var scene = AssetDatabase.FindAssets("t:Scene", new[] { "Assets" })
                                         .Select(AssetDatabase.GUIDToAssetPath)
                                         .OrderBy(p => p.StartsWith("Assets/AGScenes/") ? 0 : 1).ThenBy(p => p)
                                         .FirstOrDefault();
                if (scene != null)
                {
                    EditorSceneManager.OpenScene(scene);
                    Debug.Log("AGOpenStage: opened " + scene);
                }
            }
        }
        Setup();
    }
}
