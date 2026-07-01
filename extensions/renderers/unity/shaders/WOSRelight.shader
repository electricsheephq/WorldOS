Shader "WOS/Relight" {
  Properties { _MainTex("Diffuse",2D)="white"{} _NormalTex("ViewNormal",2D)="white"{} _DepthTex("Depth",2D)="black"{} }
  SubShader {
    Tags { "Queue"="Geometry" "RenderType"="Opaque" }
    Pass {
      ZWrite On
      CGPROGRAM
      #pragma vertex vert
      #pragma fragment frag
      #include "UnityCG.cginc"
      sampler2D _MainTex; sampler2D _NormalTex; sampler2D _DepthTex;
      float3 _KeyDir; float3 _KeyCol; float3 _FillDir; float3 _FillCol;
      float3 _SkyAmb; float3 _GroundAmb; float _Bounce;
      float4 _OrthoExt;
      float4 _P0; float3 _P0Col; float4 _P1; float3 _P1Col; float4 _P2; float3 _P2Col;
      struct v2f { float4 pos:SV_POSITION; float2 uv:TEXCOORD0; };
      v2f vert(appdata_full v){ v2f o; o.pos=UnityObjectToClipPos(v.vertex); o.uv=v.texcoord; return o; }
      float3 pointLit(float3 P, float3 N, float4 L, float3 col){
        float3 d=L.xyz-P; float dist=length(d);
        float atten=smoothstep(L.w, L.w*0.15, dist);          // smooth falloff (no hard ring seam)
        return col*atten*saturate(dot(N, normalize(d)));
      }
      fixed4 frag(v2f i):SV_Target {
        float3 diff=tex2D(_MainTex,i.uv).rgb;
        float3 N=tex2D(_NormalTex,i.uv).rgb*2.0-1.0;
        // hemisphere ambient + a fraction of the diffuse itself as bounce GI (PoE2 plate-GI; keeps corners alive)
        float3 amb=lerp(_GroundAmb, _SkyAmb, saturate(N.y*0.5+0.5)) + diff*_Bounce;
        if(dot(N,N)<0.02){ return fixed4(diff*(_GroundAmb+_SkyAmb)*0.5 + diff*_Bounce, 1); } // bg: ambient only, soft
        N=normalize(N);
        float lin=tex2D(_DepthTex,i.uv).r * _OrthoExt.z;
        float3 P=float3((i.uv.x-0.5)*2.0*_OrthoExt.x, (i.uv.y-0.5)*2.0*_OrthoExt.y, -(_OrthoExt.w + tex2D(_DepthTex,i.uv).r*_OrthoExt.z));
        float3 lit=amb;
        lit += _KeyCol * saturate(dot(N, normalize(_KeyDir)));
        lit += _FillCol * saturate(dot(N, normalize(_FillDir)));
        lit += pointLit(P,N,_P0,_P0Col);
        lit += pointLit(P,N,_P1,_P1Col);
        lit += pointLit(P,N,_P2,_P2Col);
        return fixed4(diff*lit, 1);
      }
      ENDCG
    }
  }
}
