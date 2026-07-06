Shader "WOS/ViewNormal" {
  SubShader {
    Tags { "RenderType"="Opaque" }
    Pass {
      CGPROGRAM
      #pragma vertex vert
      #pragma fragment frag
      #include "UnityCG.cginc"
      struct v2f { float4 pos:SV_POSITION; float3 vn:TEXCOORD0; };
      v2f vert(appdata_base v){ v2f o; o.pos=UnityObjectToClipPos(v.vertex); o.vn=mul((float3x3)UNITY_MATRIX_IT_MV, v.normal); return o; }
      fixed4 frag(v2f i):SV_Target { float3 n=normalize(i.vn); return fixed4(n*0.5+0.5, 1); }
      ENDCG
    }
  }
}
