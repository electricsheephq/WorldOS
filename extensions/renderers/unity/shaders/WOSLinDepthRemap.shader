// WOSLinDepthRemap — linear view-space depth normalized over a PER-SCENE near/far range (material props
// _Near/_Far) instead of the hardcoded /80 of WOS/LinDepth. The atelier scene's view depth spans ~64-96
// at the contract camera, so /80 saturates near-white with no usable gradient; remapping over the measured
// [_Near,_Far] gives a full 0..1 gradient for the paint-over structure lock. Near=0 (white) .. far=1 (black)
// is inverted here so foreground reads bright, matching typical depth-guide conventions; flip if needed.
// _WOSDepthNear/_WOSDepthFar are GLOBAL uniforms (not in a Properties block) so RenderWithShader picks
// them up from Shader.SetGlobalFloat(...) — per-material props would not bind under replacement rendering.
Shader "WOS/LinDepthRemap" {
  SubShader {
    Tags { "RenderType"="Opaque" }
    Pass {
      CGPROGRAM
      #pragma vertex vert
      #pragma fragment frag
      #include "UnityCG.cginc"
      float _WOSDepthNear; float _WOSDepthFar;
      struct v2f { float4 pos:SV_POSITION; float d:TEXCOORD0; };
      v2f vert(appdata_base v){ v2f o; o.pos=UnityObjectToClipPos(v.vertex); o.d = -mul(UNITY_MATRIX_MV, v.vertex).z; return o; }
      fixed4 frag(v2f i):SV_Target { float t=saturate((i.d-_WOSDepthNear)/max(1e-3,(_WOSDepthFar-_WOSDepthNear))); float g=1.0-t; return fixed4(g,g,g,1); }
      ENDCG
    }
  }
}
