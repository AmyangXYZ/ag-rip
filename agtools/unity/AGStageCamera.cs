// Stage scenes have no camera of their own - the game spawns it with the character.
// This makes one (not saved into the scene), framed on the stage, HDR, carrying the
// game's post chain, with fly controls in Play mode:
//   right mouse + move = look, WASD/QE = move, Shift = fast, wheel = speed.
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

[ExecuteAlways]
public class AGStageCamera : MonoBehaviour
{
    public float speed = 3f;
    float _yaw, _pitch;

    public static void Ensure()
    {
        foreach (var c in FindObjectsOfType<Camera>())
            if (c.enabled && c.gameObject.activeInHierarchy && c.GetComponent<AGStageCamera>()) return;
        if (FindObjectsOfType<Camera>().Any(c => c.enabled && c.CompareTag("MainCamera"))) return;
        var go = new GameObject("AGStageCamera (generated)") { hideFlags = HideFlags.DontSave, tag = "MainCamera" };
        var cam = go.AddComponent<Camera>();
        cam.allowHDR = true;
        cam.depthTextureMode = DepthTextureMode.Depth;  // the pipeline's depth grab (AGSimPipeline)
        cam.clearFlags = CameraClearFlags.SolidColor;   // the stage draws its own sky
        cam.backgroundColor = Color.black;
        cam.nearClipPlane = 0.05f;
        cam.farClipPlane = 2000f;
        go.AddComponent<AGSimPostFX>();
        go.AddComponent<AGStageCamera>().Frame();
    }

    static float Pct(List<float> v, float q) { v.Sort(); return v[Mathf.Clamp((int)(v.Count * q), 0, v.Count - 1)]; }

    // Where the game's own camera stands, for scenes whose camera is placed by game
    // code (home scenes): Assets/AGTools/Resources/ag_viewpoints.json, written by
    // stage_unity.py, keyed by scene name. Matched against the game's own pictures.
    [System.Serializable] class View { public string scene; public float[] position; public float[] euler; public float fov; }
    [System.Serializable] class Views { public View[] views; }

    bool Viewpoint()
    {
        var ta = Resources.Load<TextAsset>("ag_viewpoints");
        if (ta == null) return false;
        var scene = gameObject.scene.IsValid() && !string.IsNullOrEmpty(gameObject.scene.name)
            ? gameObject.scene.name : UnityEngine.SceneManagement.SceneManager.GetActiveScene().name;
        var v = JsonUtility.FromJson<Views>(ta.text)?.views?.FirstOrDefault(x => x.scene == scene);
        if (v == null) return false;
        transform.position = new Vector3(v.position[0], v.position[1], v.position[2]);
        transform.rotation = Quaternion.Euler(v.euler[0], v.euler[1], v.euler[2]);
        if (v.fov > 0) GetComponent<Camera>().fieldOfView = v.fov;
        _yaw = v.euler[1]; _pitch = v.euler[0];
        return true;
    }

    public void Frame()
    {
        if (Viewpoint()) return;
        var rs = FindObjectsOfType<Renderer>().Where(r => r.enabled).ToList();
        if (rs.Count == 0) return;
        var sizes = rs.Select(r => r.bounds.size.magnitude).OrderBy(s => s).ToList();
        float cap = sizes.Count > 20 ? sizes[(int)(sizes.Count * 0.98f)] : float.MaxValue;
        var c = rs.Where(r => r.bounds.size.magnitude <= cap).Select(r => r.bounds.center).ToList();
        var lo = new Vector3(Pct(c.Select(p => p.x).ToList(), .1f), Pct(c.Select(p => p.y).ToList(), .1f), Pct(c.Select(p => p.z).ToList(), .1f));
        var hi = new Vector3(Pct(c.Select(p => p.x).ToList(), .9f), Pct(c.Select(p => p.y).ToList(), .9f), Pct(c.Select(p => p.z).ToList(), .9f));
        var mid = (lo + hi) / 2;
        float span = Mathf.Max((hi - lo).magnitude, 2f);
        transform.position = mid + new Vector3(0.3f, 0.25f, -1f).normalized * span * 0.6f;
        transform.LookAt(mid);
        var e = transform.eulerAngles; _yaw = e.y; _pitch = e.x;
    }

    void Update()
    {
        if (!Application.isPlaying) return;
        speed = Mathf.Max(0.1f, speed * (1f + Input.mouseScrollDelta.y * 0.1f));
        if (Input.GetMouseButton(1))
        {
            _yaw += Input.GetAxis("Mouse X") * 3f;
            _pitch = Mathf.Clamp(_pitch - Input.GetAxis("Mouse Y") * 3f, -89f, 89f);
            transform.rotation = Quaternion.Euler(_pitch, _yaw, 0);
        }
        var v = new Vector3((Input.GetKey(KeyCode.D) ? 1 : 0) - (Input.GetKey(KeyCode.A) ? 1 : 0),
                            (Input.GetKey(KeyCode.E) ? 1 : 0) - (Input.GetKey(KeyCode.Q) ? 1 : 0),
                            (Input.GetKey(KeyCode.W) ? 1 : 0) - (Input.GetKey(KeyCode.S) ? 1 : 0));
        float s = speed * (Input.GetKey(KeyCode.LeftShift) ? 4f : 1f) * Time.deltaTime;
        transform.position += transform.TransformDirection(v) * s;
    }
}
