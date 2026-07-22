// WorldOS/OccluderDepth (#1433) — invisible depth-only occluder proxy.
//
// A COMMITTED asset version of the shader paint_combat_v1.cs previously created at runtime via
// UnityEditor.ShaderUtil.CreateShaderAsset. A runtime-created shader serialized into the scene is
// NOT compiled into a standalone PLAYER build, so its material fell back to the pink error shader ->
// the "magenta blocks" on the built player (#1433). Committing it as a real .shader asset (and
// referencing it from the scene's occluder materials + Always-Included Shaders) gets its variant
// compiled into the build. Source is byte-for-byte the same as the old runtime string (ColorMask 0,
// ZWrite On, Queue Geometry-1), so editor renders stay identical — depth-only, writes no color.
//
// #1677: the proxy also STAMPS stencil bit 1 wherever it renders, so WorldOS/ActorSilhouette can scope
// its walk-behind tint to "behind an occluder proxy" (Stencil Comp Equal Ref 1) instead of relying on a
// fragile equal-depth test against the actor's OWN clone-skinned body — the g4 ghost-in-the-open defect.
// The proxy still renders FIRST (Queue Geometry-1, on a cleared depth+stencil buffer), so it always
// passes depth and Replace writes 1 across its footprint; regular geometry never touches stencil, so the
// mark survives to the Transparent-queue silhouette pass. Depth output is byte-identical to before.
Shader "WorldOS/OccluderDepth" {
  SubShader {
    Tags { "RenderType"="Opaque" "Queue"="Geometry-1" }
    // #1677: mark stencil bit 1 across the proxy footprint (the silhouette pass reads it, Comp Equal).
    Stencil { Ref 1 Comp Always Pass Replace }
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
