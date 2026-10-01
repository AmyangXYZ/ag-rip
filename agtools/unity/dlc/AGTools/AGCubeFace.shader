// One face and mip of a cubemap into a float target, for AGDlcRecord: the cube is sampled
// along the D3D / WebGPU face directions (s right, t down within each face), with
// raw row 0 = t = -1, so the rows upload to a WebGPU cube face unchanged. Sampling rather
// than copying: the game's cubes are block-compressed (BC6H), which a copy into an
// uncompressed target does not decode.
Shader "Hidden/AGCubeFace"
{
    Properties { _Cube ("Cube", Cube) = "" {} }
    SubShader
    {
        Cull Off ZWrite Off ZTest Always
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            samplerCUBE _Cube;
            float _Face;
            float _Mip;
            struct v2f { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };
            v2f vert(appdata_img v) { v2f o; o.pos = UnityObjectToClipPos(v.vertex); o.uv = v.texcoord; return o; }
            float4 frag(v2f i) : SV_Target
            {
                float s = i.uv.x * 2 - 1;
                float t = i.uv.y * 2 - 1;
                float3 d;
                int f = (int)round(_Face);
                if (f == 0) d = float3(1, -t, -s);
                else if (f == 1) d = float3(-1, -t, s);
                else if (f == 2) d = float3(s, 1, t);
                else if (f == 3) d = float3(s, -1, -t);
                else if (f == 4) d = float3(s, -t, 1);
                else d = float3(-s, -t, -1);
                return texCUBElod(_Cube, float4(d, _Mip));
            }
            ENDCG
        }
    }
}
