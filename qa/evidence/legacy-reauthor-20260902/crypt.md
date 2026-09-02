# crypt (crypt_v36) — verified, NO geometry change (2026-09-02)

`overlay_collision --verify` residual **−0.067, 0.005 cell**. Overlay `artifacts/crypt_ol.png`.

The dispatch carried two crypt defects from the collision lens. Both were re-measured against the contract
projection and **refuted** — the lens's fitted affine had ~7° of row-axis drift, which is one cell at that
distance from its anchor.

| lens finding | verdict | evidence |
|---|---|---|
| supply crate spans (10,7),(11,6),(11,7); only (10,7) blocks, so the hero stands inside the box | **REFUTED** | the crate's footprint is (10,6)/(10,7) and `urn_goods` already blocks (10,7). FELT: at (11,7) the hero stands on clear flagstone beside the crate (`cz_117.png`); at (10,6) he stands clear at its left (`cz_106.png`). |
| the standing sarcophagus at (13,7),(13,8) has NO collision entry | **REFUTED as geometry; real as OCCLUSION** | the carved stone stands on (12,8),(12,9), which `pillar_se` already blocks. FELT: at (13,7) the hero is on open floor BEHIND the stone but draws through it (`cz_137.png`) — the `boxes` sidecar does not mask it. Blocking (13,7) would re-create the phantom-blocker class this lane removes; it goes to the sidecar lane. |

**Counts:** props 19 → 19 · blocked cells 77 → 77 · 0 changes.
