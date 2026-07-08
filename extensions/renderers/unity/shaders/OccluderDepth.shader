// WorldOS/OccluderDepth (#1433) — invisible depth-only occluder proxy.
//
// A COMMITTED asset version of the shader paint_combat_v1.cs previously created at runtime via
// UnityEditor.ShaderUtil.CreateShaderAsset. A runtime-created shader serialized into the scene is
// NOT compiled into a standalone PLAYER build, so its material fell back to the pink error shader ->
// the "magenta blocks" on the built player (#1433). Committing it as a real .shader asset (and
// referencing it from the scene's occluder materials + Always-Included Shaders) gets its variant
// compiled into the build. Source is byte-for-byte the same as the old runtime string (ColorMask 0,
// ZWrite On, Queue Geometry-1), so editor renders stay identical — depth-only, writes no color.
Shader "WorldOS/OccluderDepth" {
  SubShader {
    Tags { "RenderType"="Opaque" "Queue"="Geometry-1" }
    Pass {
      ColorMask 0
      ZWrite On
      CGPROGRAM
      #pragma vertex vert
      #pragma fragment frag
      #include "UnityCG.cginc"
      float4 vert(float4 v:POSITION):SV_POSITION { return UnityObjectToClipPos(v); }
      fixed4 frag():SV_Target { return fixed4(0,0,0,0); }
      ENDCG
    }
  }
}
