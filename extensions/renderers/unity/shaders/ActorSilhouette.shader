// WorldOS/ActorSilhouette (#1545) — walk-behind silhouette pass.
//
// A flat, unlit, team-tinted pass added as a SECOND material on each actor renderer at spawn
// (CombatSurfaceClient.SpawnActor). It renders ONLY where the actor is occluded by a depth-proxy box
// (WorldOS/OccluderDepth, ZWrite On): the proxy has written a nearer depth, so the actor's front-face
// fragments there are FARTHER than the buffer -> ZTest Greater fires and paints the silhouette. Where the
// actor is visible, its front faces sit at their own opaque depth (equal, not greater) -> nothing draws.
// Cull Back keeps only front faces, so a visible actor never tints itself with its own back faces.
// ZWrite Off + Transparent queue so it composites over the plate after all opaque geometry. This is the
// BG2/PoE convention (character behind a wall/prop stays a readable silhouette instead of vanishing) and
// covers the owner's "character disappears near doors" report. Must be in Always-Included Shaders so
// Shader.Find resolves it in the standalone player build (mirrors WorldOS/OccluderDepth, #1433).
//
// #1677 OCCLUDER-SCOPING (the g4 ghost-in-the-open fix): ZTest Greater ALONE is not sufficient to scope
// the tint to "behind an occluder." #1545 was a SECOND MATERIAL on the same renderer, so in the open the
// silhouette's front faces sat at EXACTLY the actor's own opaque depth (same draw, same MVP) and Greater
// reliably FAILED. #1572 re-hosted the pass as a SEPARATE clone SkinnedMeshRenderer; two independent
// skinning dispatches of the same rig do not produce bit-identical depth, so in the open the clone's
// fragments read as marginally FARTHER than the actor's own body -> Greater fires -> the whole figure
// tints ghost-cyan (#1677, install-blocking). The robust realization of the #1545 intent ("silhouette
// only behind OCCLUDER depth") is a STENCIL gate: WorldOS/OccluderDepth stamps stencil bit 1 wherever an
// occluder proxy renders, and this pass draws ONLY where that bit is set (Comp Equal, Ref 1). In the open
// no proxy covers the actor -> stencil 0 -> the pass is culled regardless of the fragile equal-depth test.
// Behind a proxy, stencil==1 AND ZTest Greater (actor farther than the proxy's nearer depth) -> the tint
// draws. WORLDOS_SILHOUETTE kill-switch is unchanged (C#-side: no clone spawned when disabled).
Shader "WorldOS/ActorSilhouette" {
  Properties { _Color ("Tint", Color) = (1,1,1,0.45) }
  SubShader {
    Tags { "RenderType"="Transparent" "Queue"="Transparent" }
    // #1677: draw ONLY where an OccluderDepth proxy stamped stencil bit 1 (i.e. behind an occluder),
    // so an unoccluded actor never tints itself against its own (clone-skinned, non-bit-identical) depth.
    Stencil { Ref 1 Comp Equal }
    Pass {
      ZTest Greater
      ZWrite Off
      Cull Back
      Blend SrcAlpha OneMinusSrcAlpha
      CGPROGRAM
      #pragma vertex vert
      #pragma fragment frag
      #include "UnityCG.cginc"
      fixed4 _Color;
      float4 vert(float4 v:POSITION):SV_POSITION { return UnityObjectToClipPos(v); }
      fixed4 frag():SV_Target { return _Color; }
      ENDCG
    }
  }
}
