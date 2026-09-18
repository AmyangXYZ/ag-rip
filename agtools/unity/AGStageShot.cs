// Batch-mode screenshot of a stage scene, for checking an export without opening the editor:
//   Unity.exe -batchmode -quit -projectPath <proj> -executeMethod AGStageShot.Run
//             -agScene Assets/.../X305.unity -agOut C:\path\shot
// Writes <out>_high.png and <out>_eye.png (framed on the renderers' 10th-90th percentile
// bounds), or with -agView <out>_view.png from the stage's viewpoint (AGStageCamera).
// -agFocus <object> shoots one object close up; -agHide <regex> deactivates objects first;
// -agSet <material>.<prop>=<value>,... overrides material floats for the shot (not saved).
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class AGStageShot
{
    static string Arg(string name)
    {
        var a = System.Environment.GetCommandLineArgs();
        int i = System.Array.IndexOf(a, name);
        return i >= 0 && i + 1 < a.Length ? a[i + 1] : null;
    }

    static float Pct(List<float> v, float q)
    {
        v.Sort();
        return v[Mathf.Clamp((int)(v.Count * q), 0, v.Count - 1)];
    }

    public static void Run()
    {
        EditorSceneManager.OpenScene(Arg("-agScene"));
        string outp = Arg("-agOut");
        string hide = Arg("-agHide");       // diagnosis: deactivate objects whose name matches
        if (hide != null)
        {
            var rx = new System.Text.RegularExpressions.Regex(hide);
            int n = 0;
            foreach (var t in Object.FindObjectsByType<Transform>(FindObjectsSortMode.None))
                if (t && rx.IsMatch(t.name)) { t.gameObject.SetActive(false); n++; }
            Debug.Log($"AGStageShot: hid {n} object(s) matching {hide}");
        }
        string set = Arg("-agSet");         // diagnosis: <material>.<float prop>=<value>[,...]
        if (set != null)
            foreach (var item in set.Split(','))
            {
                var kv = item.Split('=');
                int dot = kv[0].LastIndexOf('.');
                string mat = kv[0].Substring(0, dot), prop = kv[0].Substring(dot + 1);
                foreach (var r in Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None))
                    foreach (var m in r.sharedMaterials)
                        if (m && m.name == mat) m.SetFloat(prop, float.Parse(kv[1], System.Globalization.CultureInfo.InvariantCulture));
                Debug.Log($"AGStageShot: {mat}.{prop} = {kv[1]}");
            }
        var rs = Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)
                       .Where(r => r.enabled && r.gameObject.activeInHierarchy).ToList();
        var sizes = rs.Select(r => r.bounds.size.magnitude).OrderBy(s => s).ToList();
        float cap = sizes.Count > 20 ? sizes[(int)(sizes.Count * 0.98f)] : float.MaxValue;
        var c = rs.Where(r => r.bounds.size.magnitude <= cap).Select(r => r.bounds.center).ToList();
        var lo = new Vector3(Pct(c.Select(p => p.x).ToList(), .1f), Pct(c.Select(p => p.y).ToList(), .1f), Pct(c.Select(p => p.z).ToList(), .1f));
        var hi = new Vector3(Pct(c.Select(p => p.x).ToList(), .9f), Pct(c.Select(p => p.y).ToList(), .9f), Pct(c.Select(p => p.z).ToList(), .9f));
        var mid = (lo + hi) / 2;
        float span = Mathf.Max((hi - lo).magnitude, 2f);

        // render through the game-pipeline stand-in (decompiled shaders)
        Camera cam = null;
        var pipeType = System.Type.GetType("AGSimPipeline, Assembly-CSharp");
        if (pipeType != null)
        {
            var pipe = pipeType.GetMethod("Ensure").Invoke(null, null);
            pipeType.GetMethod("Refresh").Invoke(pipe, null);
            var camGo = GameObject.Find("AGStageCamera (generated)");
            if (camGo) cam = camGo.GetComponent<Camera>();
        }
        if (cam == null)
        {
            Debug.LogError("AGStageShot: no AGSimPipeline / AGStageCamera in this project");
            return;
        }
        cam.fieldOfView = 60;
        cam.nearClipPlane = 0.05f;
        cam.farClipPlane = span * 50;
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-agView") >= 0)
        {
            // the scene's own viewpoint (AGStageCamera: game camera if known, else framed)
            cam.GetComponent("AGStageCamera")?.SendMessage("Frame");
            Debug.Log($"AGStageShot view: {cam.transform.position} {cam.transform.eulerAngles} fov {cam.fieldOfView} scene {cam.gameObject.scene.name}");
            var rt = new RenderTexture(1600, 900, 24, RenderTextureFormat.ARGBHalf);
            cam.targetTexture = rt; cam.Render(); cam.targetTexture = null;
            RenderTexture.active = rt;
            var tex = new Texture2D(1600, 900, TextureFormat.RGBA32, false);
            tex.ReadPixels(new Rect(0, 0, 1600, 900), 0, 0); tex.Apply();
            File.WriteAllBytes(outp + "_view.png", tex.EncodeToPNG());
            Debug.Log("AGStageShot done: " + outp);
            return;
        }
        string focus = Arg("-agFocus");
        var target = focus == null ? null : rs.FirstOrDefault(r => r.gameObject.name == focus);
        if (target != null)
        {
            // close-up of one object (diagnosis): from two sides at ~2.5x its size
            var b = target.bounds;
            float d = Mathf.Max(b.size.magnitude, 0.5f) * 1.6f;
            Shoot(cam, b.center, new Vector3(1, 0.6f, -1), d, outp + "_focusA.png");
            Shoot(cam, b.center, new Vector3(-1, 0.4f, 1), d, outp + "_focusB.png");
        }
        else
        {
            Shoot(cam, mid, new Vector3(1, 0.9f, -1), span * 0.9f, outp + "_high.png");
            Shoot(cam, mid, new Vector3(0.2f, 0.12f, -1), span * 0.55f, outp + "_eye.png");
        }
        Debug.Log("AGStageShot done: " + outp);
    }

    static void Shoot(Camera cam, Vector3 mid, Vector3 dir, float dist, string path)
    {
        cam.transform.position = mid + dir.normalized * dist;
        cam.transform.LookAt(mid);
        var rt = new RenderTexture(1280, 720, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
        cam.targetTexture = rt;
        cam.Render();
        RenderTexture.active = rt;
        var tex = new Texture2D(1280, 720, TextureFormat.RGB24, false);
        tex.ReadPixels(new Rect(0, 0, 1280, 720), 0, 0);
        tex.Apply();
        File.WriteAllBytes(path, tex.EncodeToPNG());
        RenderTexture.active = null;
        cam.targetTexture = null;
    }
}
