// Frames of the DLC sequences as AGDlcPlayer plays them, for checking against the game
// and the web port. Active only when Unity is started with -agOut <dir>:
//   Unity -batchmode -projectPath <p> -executeMethod AGDlcScene.Capture -agOut <dir> [-agEvery 15] [-agSize 1600x900]
// writes <dir>/<sequence>_<frame>.png (frame = the sequence's own frame at 30 fps), then quits.
using System;
using System.IO;
using UnityEngine;
using UnityEngine.Playables;

public class AGDlcCapture : MonoBehaviour
{
    string _out;
    int _every = 15, _w = 1600, _h = 900;
    string _seq;
    PlayableDirector _pd;
    int _frame;
    RenderTexture _rt;
    Texture2D _tex;

    public static string Arg(string name)
    {
        var a = Environment.GetCommandLineArgs();
        int i = Array.IndexOf(a, name);
        return i >= 0 && i + 1 < a.Length ? a[i + 1] : null;
    }

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Boot()
    {
        if (Arg("-agOut") == null) return;
        new GameObject("AGDlcCapture").AddComponent<AGDlcCapture>();
    }

    void Awake()
    {
        _out = Arg("-agOut");
        Directory.CreateDirectory(_out);
        if (int.TryParse(Arg("-agEvery"), out int e) && e > 0) _every = e;
        var size = Arg("-agSize");
        if (size != null && size.Contains("x"))
        {
            var p = size.Split('x');
            _w = int.Parse(p[0]);
            _h = int.Parse(p[1]);
        }
        _rt = new RenderTexture(_w, _h, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
        _tex = new Texture2D(_w, _h, TextureFormat.RGB24, false);
        foreach (var p in FindObjectsByType<AGDlcPlayer>(FindObjectsSortMode.None)) p.loopAll = false;
        AGDlcPlayer.SequenceStarted += (name, pd) => { _seq = name; _pd = pd; _frame = 0; };
        AGDlcPlayer.SequenceEnded += name => { _seq = null; _pd = null; };
        AGDlcPlayer.AllEnded += Quit;
    }

    void LateUpdate()
    {
        if (_seq == null || _pd == null) return;
        if (_frame % _every == 0) Shoot($"{_seq}_{_frame:0000}.png");
        _frame++;
    }

    void Shoot(string name)
    {
        var cam = Camera.main;
        if (cam == null) return;
        var prev = cam.targetTexture;
        cam.targetTexture = _rt;
        cam.Render();
        cam.targetTexture = prev;
        var active = RenderTexture.active;
        RenderTexture.active = _rt;
        _tex.ReadPixels(new Rect(0, 0, _w, _h), 0, 0);
        _tex.Apply();
        RenderTexture.active = active;
        File.WriteAllBytes(Path.Combine(_out, name), _tex.EncodeToPNG());
    }

    void Quit()
    {
        Debug.Log("AGDlcCapture: done");
#if UNITY_EDITOR
        UnityEditor.EditorApplication.Exit(0);
#else
        Application.Quit();
#endif
    }
}
