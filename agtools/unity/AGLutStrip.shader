// 3D colour-grading LUT (SceneSetting._colorGraddingLut) -> the 2D strip the game's
// Final pass samples (width size*size, height size, slice = blue). Identity when the
// scene has no grading LUT.
Shader "Hidden/AG/LutStrip"
{
    Properties { _Lut3D ("LUT", 3D) = "" {} }
    SubShader
    {
        Cull Off ZWrite Off ZTest Always
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"
            sampler3D _Lut3D;
            float _Identity, _Size;
            float4 frag(v2f_img i) : SV_Target
            {
                float2 px = i.uv * float2(_Size * _Size, _Size) - 0.5;
                float slice = floor(px.x / _Size);
                float3 rgb = float3(px.x - slice * _Size, px.y, slice) / (_Size - 1);
                if (_Identity > 0.5) return float4(rgb, 1);
                return float4(tex3D(_Lut3D, (rgb * (_Size - 1) + 0.5) / _Size).rgb, 1);
            }
            ENDCG
        }
    }
}
