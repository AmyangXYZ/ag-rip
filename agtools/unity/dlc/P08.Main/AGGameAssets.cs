// Stand-in for the game's Asset loader (Asset.Initialize / Asset.InstantiateWithoutCache):
// the game loads an asset by its bundle path ("Prop/ch_113701_prop_taicishu_light"); the
// reference project has that prefab at Assets/Com*/ABResources/<path>.prefab. AGDlcScene
// finds every path a sequence asks for (DynamicTimelineTrackBinding.CharBindings) and
// AGDlcPlayer registers the prefabs here at Awake, before any sequence is instantiated.
using System.Collections.Generic;
using UnityEngine;

public static class AGGameAssets
{
    static readonly Dictionary<string, GameObject> _prefabs = new Dictionary<string, GameObject>();

    public static void Register(string path, GameObject prefab)
    {
        if (!string.IsNullOrEmpty(path) && prefab) _prefabs[path.ToLowerInvariant()] = prefab;
    }

    // Asset.InstantiateWithoutCache(path, false)
    public static GameObject InstantiateWithoutCache(string path)
    {
        if (string.IsNullOrEmpty(path) || !_prefabs.TryGetValue(path.ToLowerInvariant(), out var prefab))
        {
            Debug.LogError($"AGGameAssets: no prefab registered for '{path}' (AGDlcScene resolves CharBindings paths)");
            return null;
        }
        var go = Object.Instantiate(prefab);
        go.name = prefab.name;
        return go;
    }
}
