// A depth texture's raw values into a float target, for AGDlcRecord to compare the shadow
// atlas Unity drew with the WebGPU page's (the atlas is sampled in RawDepth mode).
Shader "Hidden/AGDepthDump"
{
    Properties { _MainTex ("Depth", 2D) = "white" {} }
    SubShader
    {
        Cull Off ZWrite Off ZTest Always
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"
            UNITY_DECLARE_DEPTH_TEXTURE(_MainTex);
            float4 frag(v2f_img i) : SV_Target
            {
                float d = SAMPLE_DEPTH_TEXTURE(_MainTex, i.uv);
                return float4(d, d, d, d);
            }
            ENDCG
        }
    }
}
