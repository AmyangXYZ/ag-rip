// Records what Unity draws while AGDlcPlayer plays, for the WebGPU page to draw the same
// frames with the game's translated shaders (agtools/shader_wgsl.py). Active only with
//   -agRecord <dir>   (and -executeMethod AGDlcScene.Capture, like AGDlcCapture)
// Output (<dir>):
//   meshes/<id>.bin + scene.json "meshes"   vertex streams as float32 (index uint32), per
//                                           submesh ranges, bind poses, blend-shape deltas
//   textures/<id>.png|.bin + "textures"      LDR as PNG (sRGB flag kept), HDR as RGBA16F raw
//   scene.json "materials"                   shader, enabled keywords, queue, every property
//   scene.json "renderers"                   kind, mesh, materials, bone renderer paths
//   <sequence>.frames.jsonl                  one line per frame: camera, the pipeline's global
//                                            values, renderers changed this frame (matrix,
//                                            enabled, property block), bones/blend weights and
//                                            particle meshes by offset into <sequence>.frames.bin
//   unity/<seq>_<frame>.png                  the Unity reference every -agEvery frames (unity_raw/:
//                                            the same without the post chain)
//   unity_full/<seq>_<frame>.jpg             every frame (not with -agNoFull / -agNoPost), which
//                                            agtools/dlc_web.py turns into unity/<seq>.webm
// Static renderers are written once; a frame line carries only what changed.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Rendering;
using UnityEngine.Experimental.Rendering;
using Object = UnityEngine.Object;

// Runs after every other script (the game's look-at is a LateUpdate), so a frame is
// recorded once everything that moves has moved.
[DefaultExecutionOrder(32000)]
public class AGDlcRecord : MonoBehaviour
{
    string _dir;
    readonly Dictionary<Mesh, string> _meshes = new Dictionary<Mesh, string>();
    readonly Dictionary<Texture, string> _textures = new Dictionary<Texture, string>();
    readonly Dictionary<Material, string> _materials = new Dictionary<Material, string>();
    readonly Dictionary<Renderer, string> _renderers = new Dictionary<Renderer, string>();
    readonly Dictionary<Renderer, string> _last = new Dictionary<Renderer, string>();
    readonly StringBuilder _scene = new StringBuilder();
    readonly List<string> _meshJson = new List<string>(), _texJson = new List<string>(), _matJson = new List<string>(), _rendJson = new List<string>();

    string _seq;
    int _frame;
    bool _recording;
    bool _noPost, _full;
    string _refDir = "unity";
    int _every = 15, _w = 1600, _h = 900;
    RenderTexture _rt;
    Texture2D _shot;
    StreamWriter _lines;
    FileStream _bin;
    MaterialPropertyBlock _mpb;
    Material _cubeMat;
    readonly HashSet<string> _shadowAt = new HashSet<string>((AGDlcCapture.Arg("-agShadowAt") ?? "").Split(',').Where(x => x.Length > 0));
    string _lutId;
    Mesh _bake;

    // every global the pipeline stand-in sets, by name and kind: Resources/ag_globals.json,
    // collected from its source by agtools/dlc_play.py (Shader/CommandBuffer.SetGlobal*)
    [Serializable] class G { public string name; public string kind; }
    [Serializable] class Gs { public G[] globals; }
    G[] _globals = new G[0];
    // shader name -> the uniform names its decompiled source declares (Resources/
    // ag_shader_uniforms.json, agtools/dlc_play.py): what a property block may hold
    [Serializable] class U { public string shader; public string[] names; }
    [Serializable] class Us { public U[] shaders; }
    readonly Dictionary<string, string[]> _uniforms = new Dictionary<string, string[]>();
    readonly Dictionary<string, string> _globalTex = new Dictionary<string, string>();

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Boot()
    {
        if (AGDlcCapture.Arg("-agRecord") == null) return;
        new GameObject("AGDlcRecord").AddComponent<AGDlcRecord>();
    }

    void Awake()
    {
        _dir = AGDlcCapture.Arg("-agRecord");
        foreach (var d in new[] { "", "meshes", "textures" }) Directory.CreateDirectory(Path.Combine(_dir, d));
        _mpb = new MaterialPropertyBlock();
        _bake = new Mesh();
        foreach (var p in FindObjectsByType<AGDlcPlayer>()) p.loopAll = false;
        var ta = Resources.Load<TextAsset>("ag_globals");
        if (ta) _globals = JsonUtility.FromJson<Gs>(ta.text).globals;
        var tu = Resources.Load<TextAsset>("ag_shader_uniforms");
        if (tu)
            foreach (var u in JsonUtility.FromJson<Us>(tu.text).shaders) _uniforms[u.shader] = u.names;
        Camera.onPostRender += OnPostRenderCamera;
        if (int.TryParse(AGDlcCapture.Arg("-agEvery"), out int e) && e > 0) _every = e;
        _rt = new RenderTexture(_w, _h, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
        _shot = new Texture2D(_w, _h, TextureFormat.RGB24, false);
        // -agNoPost: reference frames without the post chain (AGSimPostFX), to compare shading
        // alone; they go to unity_raw/ beside the normal unity/ ones
        _noPost = Environment.GetCommandLineArgs().Contains("-agNoPost");
        _refDir = _noPost ? "unity_raw" : "unity";
        Directory.CreateDirectory(Path.Combine(_dir, _refDir));
        // -agNoFull: only the reference frames every -agEvery, no full-rate frames
        _full = !_noPost && !Environment.GetCommandLineArgs().Contains("-agNoFull");
        if (_full) Directory.CreateDirectory(Path.Combine(_dir, "unity_full"));
        AGDlcPlayer.SequenceStarted += (name, pd) => Begin(name);
        AGDlcPlayer.SequenceEnded += name => End();
        AGDlcPlayer.AllEnded += () =>
        {
            WriteScene();
            Debug.Log("AGDlcRecord: done");
#if UNITY_EDITOR
            UnityEditor.EditorApplication.Exit(0);
#endif
        };
    }

    void Begin(string name)
    {
        _seq = name;
        _frame = 0;
        _last.Clear();
        _lines = new StreamWriter(Path.Combine(_dir, name + ".frames.jsonl"), false, new UTF8Encoding(false));
        _bin = new FileStream(Path.Combine(_dir, name + ".frames.bin"), FileMode.Create);
    }

    void End()
    {
        _lines?.Dispose();
        _bin?.Dispose();
        _lines = null;
        _bin = null;
        _seq = null;
    }

    void OnDestroy() => Camera.onPostRender -= OnPostRenderCamera;

    void DumpShadow(string name)
    {
        var atlas = typeof(AGSimShadows).GetField("_atlas", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)?.GetValue(null) as RenderTexture;
        if (!atlas) return;
        var mat = new Material(Shader.Find("Hidden/AGDepthDump"));
        var rt = RenderTexture.GetTemporary(atlas.width, atlas.height, 0, RenderTextureFormat.RFloat, RenderTextureReadWrite.Linear);
        var cb = new CommandBuffer();
        cb.SetShadowSamplingMode(atlas, ShadowSamplingMode.RawDepth);
        cb.Blit(atlas, rt, mat);
        cb.SetShadowSamplingMode(atlas, ShadowSamplingMode.CompareDepths);
        Graphics.ExecuteCommandBuffer(cb);
        var tex = new Texture2D(atlas.width, atlas.height, TextureFormat.RFloat, false, true);
        var prev = RenderTexture.active;
        RenderTexture.active = rt;
        tex.ReadPixels(new Rect(0, 0, atlas.width, atlas.height), 0, 0);
        tex.Apply();
        RenderTexture.active = prev;
        Directory.CreateDirectory(Path.Combine(_dir, "shadow"));
        File.WriteAllBytes(Path.Combine(_dir, "shadow", name + ".bin"), tex.GetRawTextureData());
        RenderTexture.ReleaseTemporary(rt);
        Destroy(tex);
        Destroy(mat);
    }

    // Once per frame, after every update: draw the main camera, recording as it finishes,
    // and keep every Nth image as the Unity reference frame (unity/<seq>_<frame>.png), every
    // image as a full-rate frame (unity_full/).
    void LateUpdate()
    {
        {
            var cam = Camera.main;
            if (_seq == null || cam == null) return;
            if (_noPost && cam.TryGetComponent(out AGSimPostFX post)) post.enabled = false;
            var prev = cam.targetTexture;
            cam.targetTexture = _rt;
            _recording = true;
            cam.Render();
            _recording = false;
            cam.targetTexture = prev;
            // -agShadowAt <seq>:<frame>[,...]: the main-light shadow atlas Unity drew for that
            // frame, as raw depth floats (shadow/<seq>_<frame>.bin, rows as Unity holds them)
            if (_shadowAt.Contains($"{_seq}:{_frame - 1}")) DumpShadow($"{_seq}_{_frame - 1:0000}");
            bool keep = (_frame - 1) % _every == 0;
            if (keep || _full)
            {
                var active = RenderTexture.active;
                RenderTexture.active = _rt;
                _shot.ReadPixels(new Rect(0, 0, _w, _h), 0, 0);
                _shot.Apply();
                RenderTexture.active = active;
            }
            // every frame as a JPEG (unity_full/), for the page's full-rate Unity playback:
            // agtools/dlc_web.py encodes them into one video per sequence
            if (_full)
                File.WriteAllBytes(Path.Combine(_dir, "unity_full", $"{_seq}_{_frame - 1:0000}.jpg"), _shot.EncodeToJPG(95));
            if (keep)
            {
                var active = RenderTexture.active;
                File.WriteAllBytes(Path.Combine(_dir, _refDir, $"{_seq}_{_frame - 1:0000}.png"), _shot.EncodeToPNG());
                // the same frame once more without the post chain (unity_raw/): shading alone,
                // from the very same state, for comparing before the post port is in
                if (!_noPost && cam.TryGetComponent(out AGSimPostFX fx) && fx.enabled)
                {
                    fx.enabled = false;
                    cam.targetTexture = _rt;
                    cam.Render();
                    cam.targetTexture = prev;
                    fx.enabled = true;
                    RenderTexture.active = _rt;
                    _shot.ReadPixels(new Rect(0, 0, _w, _h), 0, 0);
                    _shot.Apply();
                    RenderTexture.active = active;
                    Directory.CreateDirectory(Path.Combine(_dir, "unity_raw"));
                    File.WriteAllBytes(Path.Combine(_dir, "unity_raw", $"{_seq}_{_frame - 1:0000}.png"), _shot.EncodeToPNG());
                }
            }
        }
    }

    // After the main camera drew: every value below is the one this frame was drawn with
    // (the look-at runs in LateUpdate, the pipeline sets its globals before culling).
    void OnPostRenderCamera(Camera rendered)
    {
        if (!_recording || _seq == null || rendered != Camera.main) return;
        var sb = new StringBuilder(4096);
        sb.Append("{\"f\":").Append(_frame);
        var cam = Camera.main;
        if (cam)
        {
            sb.Append(",\"cam\":{\"pos\":").Append(V(cam.transform.position))
              .Append(",\"view\":").Append(M(cam.worldToCameraMatrix))
              .Append(",\"proj\":").Append(M(cam.projectionMatrix))
              .Append(",\"fov\":").Append(F(cam.fieldOfView))
              .Append(",\"near\":").Append(F(cam.nearClipPlane)).Append(",\"far\":").Append(F(cam.farClipPlane)).Append('}');
        }
        sb.Append(",\"time\":").Append(F(Time.time)).Append(",\"frameCount\":").Append(Time.frameCount);
        // the pipeline object is hidden (HideFlags): FindObjectsOfTypeAll finds it
        var pipe = Resources.FindObjectsOfTypeAll<AGSimPipeline>().FirstOrDefault(p => p && p.isActiveAndEnabled);
        if (pipe)
        {
            sb.Append(",\"plusLightCount\":").Append(pipe.plusLightCount);
            // the light layer masks are integer bits carried in floats (denormals); a
            // global read-back loses them, so they come from the pipeline's own array
            var masks = typeof(AGSimPipeline).GetField("_pMask", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)?.GetValue(pipe) as float[];
            if (masks != null)
                sb.Append(",\"lightLayerMaskBits\":[").Append(string.Join(",", masks.Take(Math.Max(1, pipe.plusLightCount)).Select(m => BitConverter.SingleToInt32Bits(m)))).Append(']');
        }
        sb.Append(",\"keywords\":[").Append(string.Join(",", Shader.enabledGlobalKeywords.Select(k => "\"" + k.name + "\""))).Append(']');
        if (AGSimShadows.LastFrame == Time.frameCount)
        {
            sb.Append(",\"shadow\":{\"count\":").Append(AGSimShadows.LastCount).Append(",\"resolution\":").Append(AGSimShadows.resolution).Append(",\"cascades\":[");
            for (int c = 0; c < AGSimShadows.LastCount; c++)
            {
                var k = AGSimShadows.LastCascades[c];
                sb.Append(c > 0 ? "," : "").Append("{\"view\":").Append(M(k.view)).Append(",\"proj\":").Append(M(k.proj))
                  .Append(",\"rect\":[").Append(F(k.rect.x)).Append(',').Append(F(k.rect.y)).Append(',').Append(F(k.rect.width)).Append(',').Append(F(k.rect.height)).Append(']')
                  .Append(",\"bias\":").Append(V4(k.bias)).Append(",\"draws\":[")
                  .Append(string.Join(",", k.draws.Where(d => d.renderer).Select(d => "[\"" + Register(d.renderer) + "\"," + d.submesh + "]"))).Append("]}");
            }
            sb.Append("]}");
        }
        // the post chain as AGSimPostFX ran it: its Final / Bloom materials' values and
        // keywords, whether bloom ran, and the colour-grading LUT it baked (exported once)
        if (cam && cam.TryGetComponent(out AGSimPostFX postFx) && postFx.enabled)
        {
            const System.Reflection.BindingFlags priv = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;
            var fin = typeof(AGSimPostFX).GetField("_final", priv)?.GetValue(postFx) as Material;
            var blo = typeof(AGSimPostFX).GetField("_bloom", priv)?.GetValue(postFx) as Material;
            var lut = typeof(AGSimPostFX).GetField("_lut", priv)?.GetValue(postFx) as RenderTexture;
            if (fin)
            {
                sb.Append(",\"post\":{\"final\":").Append(MaterialValues(fin))
                  .Append(",\"finalKeywords\":[").Append(string.Join(",", fin.enabledKeywords.Select(k => "\"" + k.name + "\""))).Append(']')
                  .Append(",\"finalShader\":\"").Append(Esc(fin.shader.name)).Append('"')
                  .Append(",\"bloomEnabled\":").Append(postFx.bloom ? 1 : 0);
                if (blo) sb.Append(",\"bloom\":").Append(MaterialValues(blo)).Append(",\"bloomShader\":\"").Append(Esc(blo.shader.name)).Append('"');
                if (lut)
                {
                    _lutId ??= RegisterTexture(lut);
                    sb.Append(",\"lut\":\"").Append(_lutId).Append('"');
                }
                sb.Append('}');
            }
        }
        sb.Append(",\"charPasses\":[").Append(string.Join(",", AGSimCharacter.LastPassDraws.Where(d => d.renderer && d.material)
            .Select(d => "[\"" + Register(d.renderer) + "\",\"" + RegisterMaterial(d.material) + "\"," + d.submesh + "," + d.pass + "]"))).Append(']');
        sb.Append(",\"globals\":{");
        bool first = true;
        foreach (var g in _globals)
        {
            string v = Global(g);
            if (v == null) continue;
            if (!first) sb.Append(',');
            first = false;
            sb.Append('"').Append(g.name).Append("\":").Append(v);
        }
        foreach (var g in _globals)
        {
            if (g.kind != "Texture") continue;
            var t = Shader.GetGlobalTexture(g.name);
            if (!t || t is RenderTexture) continue;
            if (!_globalTex.ContainsKey(g.name)) _globalTex[g.name] = RegisterTexture(t);
        }
        sb.Append("},\"r\":{");
        first = true;
        foreach (var r in FindObjectsByType<Renderer>(FindObjectsInactive.Include))
        {
            string id = Register(r);
            if (id == null) continue;
            string state = State(r);
            if (_last.TryGetValue(r, out var prev) && prev == state && !(r is SkinnedMeshRenderer) && !(r is ParticleSystemRenderer))
                continue;
            _last[r] = state;
            if (!first) sb.Append(',');
            first = false;
            sb.Append('"').Append(id).Append("\":").Append(state.Substring(0, state.Length - 1));
            if (r is SkinnedMeshRenderer smr && smr.enabled && smr.gameObject.activeInHierarchy)
            {
                sb.Append(",\"bones\":").Append(WriteBones(smr));
                if (smr.sharedMesh && smr.sharedMesh.blendShapeCount > 0)
                {
                    sb.Append(",\"blend\":[");
                    for (int i = 0; i < smr.sharedMesh.blendShapeCount; i++)
                        sb.Append(i > 0 ? "," : "").Append(F(smr.GetBlendShapeWeight(i)));
                    sb.Append(']');
                }
            }
            if (r is ParticleSystemRenderer psr && psr.enabled && psr.gameObject.activeInHierarchy && cam)
                sb.Append(",\"particles\":").Append(WriteParticles(psr, cam));
            sb.Append('}');
        }
        sb.Append("}}");
        _lines.WriteLine(sb.ToString());
        _frame++;
    }

    // enabled / active, world matrix, and the per-renderer property block
    string State(Renderer r)
    {
        var sb = new StringBuilder(256);
        bool on = r.enabled && r.gameObject.activeInHierarchy;
        sb.Append("{\"on\":").Append(on ? 1 : 0);
        if (on)
        {
            sb.Append(",\"m\":").Append(M(r.localToWorldMatrix));
            // unity_RenderingLayer.x is the renderer's mask as float bits (the light layer test)
            sb.Append(",\"layerBits\":").Append(unchecked((int)r.renderingLayerMask));
            r.GetPropertyBlock(_mpb);
            var mats = r.sharedMaterials;
            if (!_mpb.isEmpty) sb.Append(",\"mpb\":").Append(Block(_mpb, mats));
            sb.Append(",\"mats\":[").Append(string.Join(",", mats.Select(m => m ? "\"" + RegisterMaterial(m) + "\"" : "null"))).Append(']');
        }
        sb.Append('}');
        return sb.ToString();
    }

    // bone matrices: localToWorld of each bone, float32 x16, at an offset in the .bin
    string WriteBones(SkinnedMeshRenderer smr)
    {
        var bones = smr.bones;
        long at = _bin.Position;
        var buf = new byte[bones.Length * 64];
        for (int i = 0; i < bones.Length; i++)
        {
            var m = bones[i] ? bones[i].localToWorldMatrix : Matrix4x4.identity;
            for (int k = 0; k < 16; k++) Buffer.BlockCopy(BitConverter.GetBytes(m[k % 4, k / 4]), 0, buf, i * 64 + k * 4, 4);
        }
        _bin.Write(buf, 0, buf.Length);
        return "[" + at + "," + bones.Length + "]";
    }

    // a particle system's geometry as Unity builds it for this camera (world space)
    string WriteParticles(ParticleSystemRenderer psr, Camera cam)
    {
        psr.BakeMesh(_bake, cam, ParticleSystemBakeMeshOptions.BakeRotationAndScale | ParticleSystemBakeMeshOptions.BakePosition);
        if (_bake.vertexCount == 0) return "null";
        long at = _bin.Position;
        var v = _bake.vertices;
        var uv = new List<Vector4>();
        _bake.GetUVs(0, uv);
        var c = _bake.colors32;
        var idx = _bake.triangles;
        using (var w = new BinaryWriter(_bin, Encoding.UTF8, true))
        {
            foreach (var p in v) { w.Write(p.x); w.Write(p.y); w.Write(p.z); }
            for (int i = 0; i < v.Length; i++) { var u = i < uv.Count ? uv[i] : Vector4.zero; w.Write(u.x); w.Write(u.y); w.Write(u.z); w.Write(u.w); }
            for (int i = 0; i < v.Length; i++) { var cc = i < c.Length ? c[i] : new Color32(255, 255, 255, 255); w.Write(cc.r); w.Write(cc.g); w.Write(cc.b); w.Write(cc.a); }
            foreach (var i in idx) w.Write((uint)i);
        }
        return "[" + at + "," + v.Length + "," + idx.Length + "]";
    }

    // ---------------------------------------------------------------- static data
    string Register(Renderer r)
    {
        if (_renderers.TryGetValue(r, out var id)) return id;
        if (!(r is MeshRenderer || r is SkinnedMeshRenderer || r is ParticleSystemRenderer)) return null;
        id = "r" + _renderers.Count;
        _renderers[r] = id;
        var sb = new StringBuilder();
        sb.Append("{\"id\":\"").Append(id).Append("\",\"path\":\"").Append(Esc(PathOf(r.transform)))
          .Append("\",\"kind\":\"").Append(r is SkinnedMeshRenderer ? "skinned" : r is ParticleSystemRenderer ? "particles" : "mesh")
          .Append("\",\"layer\":").Append(r.gameObject.layer)
          .Append(",\"sortingOrder\":").Append(r.sortingOrder)
          .Append(",\"shadowCasting\":").Append((int)r.shadowCastingMode)
          .Append(",\"receiveShadows\":").Append(r.receiveShadows ? 1 : 0);
        // TryGetComponent: a missing component is a Unity fake-null that ?. does not catch
        Mesh mesh = r is SkinnedMeshRenderer s ? s.sharedMesh
                  : r.TryGetComponent(out MeshFilter mf) ? mf.sharedMesh : null;
        if (r is ParticleSystemRenderer pr && pr.renderMode == ParticleSystemRenderMode.Mesh) mesh = pr.mesh;
        if (mesh) sb.Append(",\"mesh\":\"").Append(RegisterMesh(mesh)).Append('"');
        if (r is SkinnedMeshRenderer smr)
        {
            sb.Append(",\"bones\":[").Append(string.Join(",", smr.bones.Select(b => "\"" + Esc(b ? PathOf(b) : "") + "\""))).Append(']');
            sb.Append(",\"quality\":").Append((int)smr.quality);
        }
        sb.Append(",\"materials\":[").Append(string.Join(",", r.sharedMaterials.Select(m => m ? "\"" + RegisterMaterial(m) + "\"" : "null"))).Append("]}");
        _rendJson.Add(sb.ToString());
        return id;
    }

    string RegisterMesh(Mesh mesh)
    {
        if (_meshes.TryGetValue(mesh, out var id)) return id;
        id = "m" + _meshes.Count;
        _meshes[mesh] = id;
        var sb = new StringBuilder();
        sb.Append("{\"id\":\"").Append(id).Append("\",\"name\":\"").Append(Esc(mesh.name)).Append("\",\"vertexCount\":").Append(mesh.vertexCount).Append(",\"streams\":{");
        using (var w = new BinaryWriter(File.Create(Path.Combine(_dir, "meshes", id + ".bin"))))
        {
            long off = 0;
            bool firstStream = true;
            void Stream(string name, int dim, List<float> data)
            {
                if (data == null || data.Count == 0) return;
                foreach (var f in data) w.Write(f);
                sb.Append(firstStream ? "" : ",").Append('"').Append(name).Append("\":[").Append(off).Append(',').Append(dim).Append(']');
                firstStream = false;
                off += data.Count * 4;
            }
            Stream("POSITION", 3, mesh.vertices.SelectMany(v => new[] { v.x, v.y, v.z }).ToList());
            Stream("NORMAL", 3, mesh.normals.SelectMany(v => new[] { v.x, v.y, v.z }).ToList());
            Stream("TANGENT", 4, mesh.tangents.SelectMany(v => new[] { v.x, v.y, v.z, v.w }).ToList());
            Stream("COLOR", 4, mesh.colors.SelectMany(c => new[] { c.r, c.g, c.b, c.a }).ToList());
            for (int ch = 0; ch < 8; ch++)
            {
                var uv = new List<Vector4>();
                mesh.GetUVs(ch, uv);
                if (uv.Count == 0) continue;
                int dim = mesh.GetVertexAttributeDimension((VertexAttribute)((int)VertexAttribute.TexCoord0 + ch));
                Stream("TEXCOORD" + ch, dim, uv.SelectMany(u => new[] { u.x, u.y, u.z, u.w }.Take(dim)).ToList());
            }
            var weights = mesh.GetAllBoneWeights();
            var perVertex = mesh.GetBonesPerVertex();
            if (weights.Length > 0)
            {
                // up to 4 influences per vertex, the four strongest (Unity's own limit for its
                // default skin weights setting is read from the renderer's quality)
                var bi = new List<float>();
                var bw = new List<float>();
                int at = 0;
                for (int v = 0; v < mesh.vertexCount; v++)
                {
                    int n = perVertex[v];
                    for (int k = 0; k < 4; k++)
                    {
                        if (k < n) { bi.Add(weights[at + k].boneIndex); bw.Add(weights[at + k].weight); }
                        else { bi.Add(0); bw.Add(0); }
                    }
                    at += n;
                }
                Stream("BLENDINDICES", 4, bi);
                Stream("BLENDWEIGHTS", 4, bw);
            }
            sb.Append("},\"submeshes\":[");
            for (int s = 0; s < mesh.subMeshCount; s++)
            {
                var d = mesh.GetSubMesh(s);
                var idx = mesh.GetIndices(s);
                sb.Append(s > 0 ? "," : "").Append("{\"offset\":").Append(off).Append(",\"count\":").Append(idx.Length)
                  .Append(",\"topology\":\"").Append(d.topology).Append("\"}");
                foreach (var i in idx) w.Write((uint)i);
                off += idx.Length * 4;
            }
            sb.Append(']');
            if (mesh.bindposeCount > 0)
            {
                sb.Append(",\"bindposes\":[");
                var bp = mesh.bindposes;
                for (int i = 0; i < bp.Length; i++) sb.Append(i > 0 ? "," : "").Append(M(bp[i]));
                sb.Append(']');
            }
            if (mesh.blendShapeCount > 0)
            {
                sb.Append(",\"blendShapes\":[");
                var dv = new Vector3[mesh.vertexCount];
                var dn = new Vector3[mesh.vertexCount];
                var dt = new Vector3[mesh.vertexCount];
                for (int b = 0; b < mesh.blendShapeCount; b++)
                {
                    sb.Append(b > 0 ? "," : "").Append("{\"name\":\"").Append(Esc(mesh.GetBlendShapeName(b))).Append("\",\"frames\":[");
                    for (int f = 0; f < mesh.GetBlendShapeFrameCount(b); f++)
                    {
                        mesh.GetBlendShapeFrameVertices(b, f, dv, dn, dt);
                        sb.Append(f > 0 ? "," : "").Append("{\"weight\":").Append(F(mesh.GetBlendShapeFrameWeight(b, f))).Append(",\"offset\":").Append(off).Append('}');
                        foreach (var a in new[] { dv, dn, dt })
                            foreach (var p in a) { w.Write(p.x); w.Write(p.y); w.Write(p.z); }
                        off += mesh.vertexCount * 36;
                    }
                    sb.Append("]}");
                }
                sb.Append(']');
            }
            sb.Append(",\"bounds\":{\"center\":").Append(V(mesh.bounds.center)).Append(",\"size\":").Append(V(mesh.bounds.size)).Append("}}");
        }
        _meshJson.Add(sb.ToString());
        return id;
    }

    string RegisterMaterial(Material m)
    {
        if (_materials.TryGetValue(m, out var id)) return id;
        id = "mat" + _materials.Count;
        _materials[m] = id;
        var sb = new StringBuilder();
        var sh = m.shader;
        sb.Append("{\"id\":\"").Append(id).Append("\",\"name\":\"").Append(Esc(m.name)).Append("\",\"shader\":\"").Append(Esc(sh.name))
          .Append("\",\"queue\":").Append(m.renderQueue)
          .Append(",\"keywords\":[").Append(string.Join(",", m.enabledKeywords.Select(k => "\"" + k.name + "\""))).Append(']')
          .Append(",\"passes\":[").Append(string.Join(",", Enumerable.Range(0, m.passCount).Select(i => "{\"name\":\"" + Esc(m.GetPassName(i)) + "\",\"lightMode\":\"" + Esc(m.GetTag("LightMode", false, "") ) + "\",\"enabled\":" + (m.GetShaderPassEnabled(m.GetPassName(i)) ? 1 : 0) + "}"))).Append(']')
          .Append(",\"props\":{");
        int n = sh.GetPropertyCount();
        bool first = true;
        for (int i = 0; i < n; i++)
        {
            string name = sh.GetPropertyName(i);
            string v;
            switch (sh.GetPropertyType(i))
            {
                case ShaderPropertyType.Color: v = C(ShaderColor(m.GetColor(name))); break;
                case ShaderPropertyType.Vector: v = V4(m.GetVector(name)); break;
                case ShaderPropertyType.Float:
                case ShaderPropertyType.Range: v = F(m.GetFloat(name)); break;
                case ShaderPropertyType.Int: v = m.GetInteger(name).ToString(); break;
                case ShaderPropertyType.Texture:
                    var t = m.GetTexture(name);
                    v = "{\"tex\":" + (t ? "\"" + RegisterTexture(t) + "\"" : "null") + ",\"st\":" + V4(new Vector4(m.GetTextureScale(name).x, m.GetTextureScale(name).y, m.GetTextureOffset(name).x, m.GetTextureOffset(name).y)) + "}";
                    break;
                default: continue;
            }
            sb.Append(first ? "" : ",").Append('"').Append(name).Append("\":").Append(v);
            first = false;
        }
        sb.Append("}}");
        _matJson.Add(sb.ToString());
        return id;
    }

    string RegisterTexture(Texture t)
    {
        if (_textures.TryGetValue(t, out var id)) return id;
        id = "t" + _textures.Count;
        _textures[t] = id;
        bool hdr = t is Cubemap || (t is Texture2D t2 && GraphicsFormatUtility.IsHDRFormat(t2.graphicsFormat)) || (t is RenderTexture rt0 && rt0.format == RenderTextureFormat.ARGBHalf);
        bool srgb = GraphicsFormatUtility.IsSRGBFormat(t.graphicsFormat);
        int faces = t is Cubemap ? 6 : 1;
        int w = t.width, h = t.height;
        var files = new List<string>();
        int mipsOut = 1;
        if (t is Cubemap cubemap)
        {
            // every face and mip, sampled along the WebGPU face directions (AGCubeFace):
            // the game reads its reflection cube's mips by roughness
            _cubeMat ??= new Material(Shader.Find("Hidden/AGCubeFace"));
            _cubeMat.SetTexture("_Cube", cubemap);
            mipsOut = Mathf.Max(1, cubemap.mipmapCount);
            for (int f = 0; f < 6; f++)
                for (int m = 0; m < mipsOut; m++)
                {
                    int s = Mathf.Max(1, w >> m);
                    var rtm = RenderTexture.GetTemporary(s, s, 0, RenderTextureFormat.ARGBHalf, RenderTextureReadWrite.Linear);
                    _cubeMat.SetFloat("_Face", f);
                    _cubeMat.SetFloat("_Mip", m);
                    Graphics.Blit(null, rtm, _cubeMat);
                    var readm = new Texture2D(s, s, TextureFormat.RGBAHalf, false, true);
                    var prevm = RenderTexture.active;
                    RenderTexture.active = rtm;
                    readm.ReadPixels(new Rect(0, 0, s, s), 0, 0);
                    readm.Apply();
                    RenderTexture.active = prevm;
                    string file = $"{id}_f{f}_m{m}.bin";
                    File.WriteAllBytes(Path.Combine(_dir, "textures", file), readm.GetRawTextureData());
                    files.Add(file);
                    Destroy(readm);
                    RenderTexture.ReleaseTemporary(rtm);
                }
        }
        else
        {
            // read back through a blit so compressed and non-readable textures come out too
            var rt = RenderTexture.GetTemporary(w, h, 0, hdr ? RenderTextureFormat.ARGBHalf : RenderTextureFormat.ARGB32,
                                                srgb ? RenderTextureReadWrite.sRGB : RenderTextureReadWrite.Linear);
            var read = new Texture2D(w, h, hdr ? TextureFormat.RGBAHalf : TextureFormat.RGBA32, false, !srgb);
            Graphics.Blit(t, rt);
            var prev = RenderTexture.active;
            RenderTexture.active = rt;
            read.ReadPixels(new Rect(0, 0, w, h), 0, 0);
            read.Apply();
            RenderTexture.active = prev;
            string file = id + (hdr ? ".bin" : ".png");
            File.WriteAllBytes(Path.Combine(_dir, "textures", file), hdr ? read.GetRawTextureData() : read.EncodeToPNG());
            files.Add(file);
            RenderTexture.ReleaseTemporary(rt);
            Destroy(read);
        }
        _texJson.Add("{\"id\":\"" + id + "\",\"name\":\"" + Esc(t.name) + "\",\"width\":" + w + ",\"height\":" + h + ",\"hdr\":" + (hdr ? 1 : 0) +
                     ",\"srgb\":" + (srgb ? 1 : 0) + ",\"cube\":" + (faces > 1 ? 1 : 0) + ",\"wrap\":\"" + t.wrapMode + "\",\"filter\":\"" + t.filterMode +
                     "\",\"mips\":" + t.mipmapCount + ",\"mipsExported\":" + mipsOut + ",\"aniso\":" + t.anisoLevel + ",\"files\":[" + string.Join(",", files.Select(f => "\"" + f + "\"")) + "]}");
        return id;
    }

    void WriteScene()
    {
        var sb = new StringBuilder();
        sb.Append("{\"meshes\":[").Append(string.Join(",\n", _meshJson)).Append("],\n\"textures\":[").Append(string.Join(",\n", _texJson))
          .Append("],\n\"materials\":[").Append(string.Join(",\n", _matJson)).Append("],\n\"renderers\":[").Append(string.Join(",\n", _rendJson))
          .Append("],\n\"globalTextures\":{").Append(string.Join(",", _globalTex.Select(kv => "\"" + kv.Key + "\":\"" + kv.Value + "\"")))
          .Append("},\n\"globalKeywords\":[").Append(string.Join(",", Shader.enabledGlobalKeywords.Select(k => "\"" + k.name + "\""))).Append("]}");
        File.WriteAllText(Path.Combine(_dir, "scene.json"), sb.ToString());
    }

    // ---------------------------------------------------------------- helpers
    string Block(MaterialPropertyBlock b, Material[] mats)
    {
        // Unity cannot list a block's contents: ask for every uniform the shaders declare
        var names = new HashSet<string>();
        foreach (var m in mats)
            if (m && _uniforms.TryGetValue(m.shader.name, out var ns)) names.UnionWith(ns);
        names.Add("unity_LightData");
        var sb = new StringBuilder("{");
        bool first = true;
        void Put(string name, string v)
        {
            sb.Append(first ? "" : ",").Append('"').Append(name).Append("\":").Append(v);
            first = false;
        }
        foreach (var name in names)
        {
            if (name == "unity_LightIndices") continue;
            if (b.HasMatrix(name)) Put(name, M(b.GetMatrix(name)));
            else if (b.HasVector(name)) Put(name, V4(b.GetVector(name)));
            else if (b.HasFloat(name)) Put(name, F(b.GetFloat(name)));
            else if (b.HasInt(name)) Put(name, b.GetInt(name).ToString());
            else if (b.HasTexture(name))
            {
                var t = b.GetTexture(name);
                if (t && !(t is RenderTexture)) Put(name, "{\"tex\":\"" + RegisterTexture(t) + "\"}");
            }
        }
        if (b.HasVector("unity_LightIndices"))
        {
            var arr = b.GetVectorArray("unity_LightIndices");
            if (arr != null) Put("unity_LightIndices", "[" + string.Join(",", arr.Select(v => IntBits(v))) + "]");
        }
        return sb.Append('}').ToString();
    }

    // a material's float / vector / colour values by name (the post materials, per frame)
    static string MaterialValues(Material m)
    {
        var sb = new StringBuilder("{");
        var sh = m.shader;
        bool first = true;
        for (int i = 0; i < sh.GetPropertyCount(); i++)
        {
            string name = sh.GetPropertyName(i), v;
            switch (sh.GetPropertyType(i))
            {
                case ShaderPropertyType.Color: v = C(ShaderColor(m.GetColor(name))); break;
                case ShaderPropertyType.Vector: v = V4(m.GetVector(name)); break;
                case ShaderPropertyType.Float: case ShaderPropertyType.Range: v = F(m.GetFloat(name)); break;
                default: continue;
            }
            sb.Append(first ? "" : ",").Append('"').Append(name).Append("\":").Append(v);
            first = false;
        }
        // values set on the material that its shader's Properties block does not list
        foreach (var name in new[] { "tonemapping", "contrast", "exposure", "_ACES_TONEMAP", "_invert", "_Grayness", "_Darkness",
                                     "_VignetteEnable", "_HasGobalDistortionTexture", "_YFlip" })
            if (m.HasFloat(name) && !sb.ToString().Contains("\"" + name + "\":"))
            {
                sb.Append(first ? "" : ",").Append('"').Append(name).Append("\":").Append(F(m.GetFloat(name)));
                first = false;
            }
        if (m.HasVector("_Params")) sb.Append(first ? "" : ",").Append("\"_Params\":").Append(V4(m.GetVector("_Params")));
        return sb.Append('}').ToString();
    }

    // A material's Color property as the shader receives it: in a linear-colour-space project
    // Unity converts it from gamma to linear on upload (RGB, not alpha; HDR values too).
    // Vector properties and global colours are passed unconverted (measured: SetGlobalColor
    // 0.5 reads back 0.5, a property block's SetColor 0.5 holds 0.214).
    static Color ShaderColor(Color c) => QualitySettings.activeColorSpace == ColorSpace.Linear ? c.linear : c;

    static string IntBits(Vector4 v)
        => "[" + BitConverter.SingleToInt32Bits(v.x) + "," + BitConverter.SingleToInt32Bits(v.y) + "," + BitConverter.SingleToInt32Bits(v.z) + "," + BitConverter.SingleToInt32Bits(v.w) + "]";

    static string Global(G g)
    {
        switch (g.kind)
        {
            case "Float": case "Int": return F(Shader.GetGlobalFloat(g.name));
            case "Vector": case "Color": return V4(Shader.GetGlobalVector(g.name));
            case "Matrix": return M(Shader.GetGlobalMatrix(g.name));
            case "VectorArray": { var a = Shader.GetGlobalVectorArray(g.name); return a == null ? null : "[" + string.Join(",", a.Select(V4)) + "]"; }
            case "FloatArray": { var a = Shader.GetGlobalFloatArray(g.name); return a == null ? null : "[" + string.Join(",", a.Select(F)) + "]"; }
            case "MatrixArray": { var a = Shader.GetGlobalMatrixArray(g.name); return a == null ? null : "[" + string.Join(",", a.Select(M)) + "]"; }
            case "Texture": return null;     // see GlobalTexture
            default: return null;
        }
    }

    static string Value(object o)
    {
        switch (o)
        {
            case float f: return F(f);
            case int i: return i.ToString();
            case Vector4 v: return V4(v);
            case Color c: return C(c);
            case Matrix4x4 m: return M(m);
            case Vector4[] a: return "[" + string.Join(",", a.Select(V4)) + "]";
            case float[] a: return "[" + string.Join(",", a.Select(F)) + "]";
            case Matrix4x4[] a: return "[" + string.Join(",", a.Select(M)) + "]";
            case Texture t: return "\"tex:" + Esc(t.name) + "\"";
            default: return "null";
        }
    }

    static string PathOf(Transform t)
    {
        var parts = new List<string>();
        for (; t; t = t.parent) parts.Add(t.name);
        parts.Reverse();
        return string.Join("/", parts);
    }

    static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
    static string F(float f) => float.IsFinite(f) ? f.ToString("R", CultureInfo.InvariantCulture) : "0";
    static string V(Vector3 v) => "[" + F(v.x) + "," + F(v.y) + "," + F(v.z) + "]";
    static string V4(Vector4 v) => "[" + F(v.x) + "," + F(v.y) + "," + F(v.z) + "," + F(v.w) + "]";
    static string C(Color c) => "[" + F(c.r) + "," + F(c.g) + "," + F(c.b) + "," + F(c.a) + "]";
    // column-major, as WGSL mat4x4 reads it
    static string M(Matrix4x4 m)
    {
        var sb = new StringBuilder("[");
        for (int k = 0; k < 16; k++) sb.Append(k > 0 ? "," : "").Append(F(m[k % 4, k / 4]));
        return sb.Append(']').ToString();
    }
}
