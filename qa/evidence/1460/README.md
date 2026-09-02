# W6.1 (#1460) — occluder-vs-PainterlyActor ZTest verification evidence

Captured on the GEX44 Unity box (display :0, 6000.5.1f1) via the editor-only
`OccluderVerify.cs` MenuItems (`Tools/WorldOS/W6.1/…`), `manage_camera` screenshot
`super_size:2` (5120×2880; committed here downscaled to 2560px for repo weight).

Scene: `Assets/Scenes/M1CombatV1_canonical.unity` (the camp fixture). Three actor roots
were forced to cover both material paths — `Actor_char_f50d226067d4` → **WorldOS/PainterlyActor**
(Transparent-queue 2-pass: ZWrite depth-prime + alpha-blended color), the other two →
**Standard** (opaque, Queue Geometry).

| frame | what it shows |
|-------|---------------|
| `occ_before_no-occluder.png` | baseline — all 3 actors visible around the fire (selection rings + bodies). |
| `occ_after_occluded.png` | after dropping an **invisible** `WorldOS/OccluderDepth` slab (Queue=Geometry-1, ColorMask 0, ZWrite On) between the camera and each actor: **all 3 actor bodies are HIDDEN** and the painted backdrop (log, ground) shows through. |

## Verdict — the interplay is CORRECT

The occluder renders in the opaque phase (Geometry-1 = 1999, before the Transparent actors at
3000) writing DEPTH but no color; on Metal TBDR opaque depth (queue < 2500) is retained into the
transparent phase. An actor fragment behind the slab has greater depth than the slab's written
depth, so both the PainterlyActor color pass (ZTest LEqual, ZWrite Off) **and** its depth-prime
pass fail the depth test there, and the Standard opaque actors fail it the same way — the bodies
are discarded, revealing the plate. Crucially the slab draws **no color** (the backdrop shows
through, not a black box), which also confirms the `WorldOS/OccluderDepth` shader resolves — i.e.
the `OccluderDepthOnly` name bug (which fell back to visible black boxes) is refuted. The ground
selection rings (separate floor decals, not actor bodies) correctly remain.
