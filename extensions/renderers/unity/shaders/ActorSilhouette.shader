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
Shader "WorldOS/ActorSilhouette" {
  Properties { _Color ("Tint", Color) = (1,1,1,0.45) }
  SubShader {
    Tags { "RenderType"="Transparent" "Queue"="Transparent" }
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
