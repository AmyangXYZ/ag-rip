// ReplicaRenderer's depth grab: the camera depth after the opaques, copied to an R32
// texture (raw device depth) that the game binds as _CameraDepthTexture and
// _DepthIntermediate. Here the source is the built-in camera depth texture.
Shader "Hidden/AG/CopyDepth"
{
    SubShader
    {
        Cull Off ZWrite Off ZTest Always
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"
            UNITY_DECLARE_DEPTH_TEXTURE(_CameraDepthTexture);
            float4 frag(v2f_img i) : SV_Target
            {
                return SAMPLE_RAW_DEPTH_TEXTURE(_CameraDepthTexture, i.uv).rrrr;
            }
            ENDCG
        }
    }
}
