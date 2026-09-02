Shader "WorldOS/PainterlyActor"
{
    // Scene-driven PAINTERLY relight + REAL-TIME PAINT post for composited 3D actors so they
    // read as HAND-PAINTED INTO the plate instead of "3D maquettes on a painted plate" (the L4
    // ceiling). The relight (warm hearth KEY + cool fill + directional rim + warm bounce) is the
    // converged r4 model; the v2 hybrid ADDS a real-time painterly pass that survives Mecanim
    // deformation (per-fragment surface treatment on the live skinned mesh, NOT a frozen bake):
    //   (1) KUWAHARA variance-minimizing quadrant filter -> flattens CG micro-detail to painterly regions
    //   (2) VALUE POSTERIZE -> brushy discrete value steps instead of a continuous CG ramp
    //   (3) BRUSH-GRAIN -> procedural directional value-noise = visible strokes, not plastic CG surface
    //   (4) PALETTE-SNAP -> pulls sat/hue toward the muted painted plate range
    //   (5) EDGE-FEATHER -> dissolves the razor silhouette (the #1 maquette tell) with an alpha-blended,
    //       grain-broken grazing-angle fade so the contour reads as brush bristles, not a vector cut.
    // Two passes: a ZWrite depth-prime (so the body self-sorts correctly while transparent), then an
    // alpha-blended color pass for the feathered edge. All tunable via uniforms (no recompile to iterate).
    Properties
    {
        _MainTex      ("Albedo (Meshy)", 2D)   = "white" {}
        _BaseColor    ("Tint", Color)          = (1,1,1,1)
        _KeyColor     ("Key (hearth) Color", Color) = (1.0,0.604,0.271,1)
        _AmbientColor ("Ambient Fill Color", Color) = (0.227,0.247,0.333,1)
        _KeyDir       ("Key Dir (world)", Vector) = (0.5,0.55,0.7,0)
        _KeyStrength  ("Key Strength", Range(0,3)) = 1.0
        _RimStrength  ("Rim Strength", Range(0,2)) = 0.5
        _Desat        ("Desaturate", Range(0,1)) = 0.25
        _BounceStrength ("Warm Floor Bounce", Range(0,1)) = 0.2
        // ---- real-time painterly pass (v2 hybrid; the L4-ceiling breaker) ----
        _Kuwahara     ("Kuwahara Radius (texels)", Range(0,5)) = 2.5
        _Posterize    ("Value Posterize Steps", Range(2,16)) = 6.0
        _BrushStrength("Brush-Grain Strength", Range(0,1)) = 0.40
        _BrushScale   ("Brush-Grain Scale", Range(2,60)) = 20.0
        _EdgeSoften   ("Edge Feather Width", Range(0,1)) = 0.42
        _PaletteSnap  ("Palette Snap (sat pull)", Range(0,1)) = 0.30
        _PaintLift    ("Paint Value Lift", Range(0,0.3)) = 0.07
        _AmbientLift  ("Ambient Lift (no black mass)", Range(0,0.5)) = 0.18
        _MaxLuma      ("Max Luma (stay below hearth)", Range(0.3,1)) = 0.72
        _TermSharp    ("Terminator Sharpness", Range(0,1)) = 0.5
        _CoolRimStrength ("Cool Rim (back-facing) Strength", Range(0,2)) = 0.55
        _CoolRimColor ("Cool Rim Color", Color) = (0.10, 0.21, 0.31, 1)
        // ---- scene-color CONTAMINATION (R11 L4 fix: backdrop-bleed tint to feel painted-into-scene) ----
        // Set per scene: lit side = hearth warm amber, shadow side = cool stone grey-blue
        _SceneLitSideTint ("Scene Lit-Side Tint (hearth amber)", Color) = (0.78, 0.48, 0.12, 1)
        _SceneShadowTint  ("Scene Shadow-Side Tint (stone blue-grey)", Color) = (0.17, 0.22, 0.32, 1)
        _SceneContamination ("Scene Color Contamination 0..1", Range(0,0.5)) = 0.18
        // ---- camera-DEPTH atmospheric integration (L4 CRITICAL: distant actors must wash into the
        // room's ambient — equal-contrast-at-depth was the #1 "pasted sprite" tell) ----
        _AtmDepth     ("Atmospheric Wash 0..1 (set per actor by depth)", Range(0,1)) = 0.0
        _AtmColor     ("Atmospheric Fog Color (scene ambient)", Color) = (0.13,0.13,0.16,1)
    }
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" }

        // PASS 1 — depth prime so the alpha-blended body sorts against itself (no see-through limbs).
        Pass
        {
            ColorMask 0
            ZWrite On
            ZTest LEqual
            Cull Back
            CGPROGRAM
            #pragma vertex vshadow
            #pragma fragment fshadow
            #include "UnityCG.cginc"
            sampler2D _MainTex; float4 _MainTex_ST; float4 _MainTex_TexelSize;
            float _Kuwahara, _EdgeSoften;
            struct ai { float4 v:POSITION; float3 n:NORMAL; float2 uv:TEXCOORD0; };
            struct vo { float4 pos:SV_POSITION; };
            vo vshadow(ai i){ vo o; o.pos=UnityObjectToClipPos(i.v); return o; }
            fixed4 fshadow(vo i):SV_Target{ return 0; }
            ENDCG
        }

        // PASS 2 — painterly relit color, alpha-blended for the feathered silhouette.
        Pass
        {
            Blend SrcAlpha OneMinusSrcAlpha
            ZWrite Off
            ZTest LEqual
            Cull Back

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            sampler2D _MainTex;
            float4 _MainTex_ST;
            float4 _MainTex_TexelSize;   // (1/w, 1/h, w, h)
            fixed4 _BaseColor;
            fixed4 _KeyColor;
            fixed4 _AmbientColor;
            float4 _KeyDir;
            float  _KeyStrength;
            float  _RimStrength;
            float  _Desat;
            float  _BounceStrength;
            float  _Kuwahara;
            float  _Posterize;
            float  _BrushStrength;
            float  _BrushScale;
            float  _EdgeSoften;
            float  _PaletteSnap;
            float  _PaintLift;
            float  _AmbientLift;
            float  _MaxLuma;
            float  _TermSharp;
            float  _CoolRimStrength;
            fixed4 _CoolRimColor;
            fixed4 _SceneLitSideTint;
            fixed4 _SceneShadowTint;
            float  _SceneContamination;
            float  _AtmDepth;
            fixed4 _AtmColor;

            struct appdata { float4 vertex : POSITION; float3 normal : NORMAL; float2 uv : TEXCOORD0; };
            struct v2f
            {
                float4 pos    : SV_POSITION;
                float3 wnormal: TEXCOORD0;
                float3 wview  : TEXCOORD1;
                float2 uv     : TEXCOORD2;
            };

            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.wnormal = UnityObjectToWorldNormal(v.normal);
                float3 wpos = mul(unity_ObjectToWorld, v.vertex).xyz;
                o.wview = normalize(_WorldSpaceCameraPos - wpos);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                return o;
            }

            float lum(fixed3 c) { return dot(c, fixed3(0.299, 0.587, 0.114)); }

            fixed3 desat(fixed3 c, float amt)
            {
                float l = lum(c);
                return lerp(c, fixed3(l,l,l), amt);
            }

            float hash21(float2 p)
            {
                p = frac(p * float2(123.34, 456.21));
                p += dot(p, p + 45.32);
                return frac(p.x * p.y);
            }
            float vnoise(float2 p)
            {
                float2 i = floor(p); float2 f = frac(p);
                float a = hash21(i);
                float b = hash21(i + float2(1,0));
                float c = hash21(i + float2(0,1));
                float d = hash21(i + float2(1,1));
                float2 u = f * f * (3.0 - 2.0 * f);
                return lerp(lerp(a,b,u.x), lerp(c,d,u.x), u.y);
            }

            // KUWAHARA (4-quadrant, variance-minimizing) — flattens CG micro-detail to painterly regions.
            fixed3 kuwahara(float2 uv, float r)
            {
                if (r < 0.25) return tex2D(_MainTex, uv).rgb;
                float2 tx = _MainTex_TexelSize.xy * r;
                fixed3 bestMean = tex2D(_MainTex, uv).rgb; float bestVar = 1e9;
                const float2 q[4] = { float2(-1,-1), float2(1,-1), float2(-1,1), float2(1,1) };
                [unroll]
                for (int k = 0; k < 4; k++)
                {
                    fixed3 m = 0; float lm = 0; float lm2 = 0; const int N = 4;
                    [unroll]
                    for (int sx = 0; sx <= 1; sx++)
                    [unroll]
                    for (int sy = 0; sy <= 1; sy++)
                    {
                        float2 o = float2(q[k].x * sx, q[k].y * sy) * tx;
                        fixed3 s = tex2D(_MainTex, uv + o).rgb;
                        m += s; float l = lum(s); lm += l; lm2 += l * l;
                    }
                    m /= N; lm /= N; lm2 /= N;
                    float var = lm2 - lm * lm;
                    if (var < bestVar) { bestVar = var; bestMean = m; }
                }
                return bestMean;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                float3 N = normalize(i.wnormal);
                float3 L = normalize(_KeyDir.xyz);
                float3 V = normalize(i.wview);

                // (PAINT 1) KUWAHARA-flattened albedo, tint + gentle desaturate
                fixed3 tex = kuwahara(i.uv, _Kuwahara);
                fixed3 albedo = desat(tex * _BaseColor.rgb, _Desat);

                // DIRECTIONAL key with a REAL, HARDER terminator (R2v2 L3): shadow side -> COOL fill,
                // key side -> warm. The r2 panel said the terminator was too soft/centered and the cool
                // fill too weak (uniformly warm-bathed). _TermSharp narrows the key->shadow transition;
                // the cool fill is deepened (darker + bluer) so the shadow side genuinely goes cool.
                float lambert = dot(N, L);
                float lo = lerp(-0.25, 0.18, _TermSharp);   // higher _TermSharp -> tighter band -> harder edge
                float hi = lerp(0.55, 0.34, _TermSharp);
                float keyMask = smoothstep(lo, hi, lambert);
                fixed3 keyLight = lerp(fixed3(1,1,1), _KeyColor.rgb, 0.92);  // R13: 0.80→0.92 for purer amber (fix neutral-grey highlight)
                // deeper, bluer cool fill on the shadow side (L3 r2: push toward the scene blue ambient).
                fixed3 coolFill = _AmbientColor.rgb * 1.9 + fixed3(0.0, 0.02, 0.10);
                fixed3 lightTint = lerp(coolFill, keyLight, keyMask);
                // exposure: the r-final L4 CONSENSUS CRITICAL was the actor crushing to a near-black blob
                // with no internal value planes. RAISE the shadow-side floor hard so the body spans
                // readable midtones (4-5 posterize steps) INSIDE the plate's mid range; the lit side is
                // still held below the hearth (MaxLuma clamp downstream).
                float exposure = lerp(0.72 + _AmbientLift, 1.05 * _KeyStrength, keyMask);

                fixed3 lit = albedo * lightTint * exposure;

                // LIFT the black point WITHOUT killing the terminator (r8 L3: a uniform warm fill washed
                // the body flat = no directional read). Apply the fill DIRECTIONALLY: the SHADOW side gets
                // a COOL fill (lifts it off black + warm/cool chroma split), the KEY side gets a touch of
                // WARM bounce. This preserves the warm-right / cool-left terminator while ensuring nothing
                // crushes to pure black (the tavern is firelit). keyMask: 0=shadow .. 1=key.
                // r9 L3: the cool fill was too saturated/blue -> read as splotchy albedo on the LIT side
                // (interleaved terminator). DESATURATE the cool fill ~50% toward neutral, and apply it
                // ONLY where N·L<0 (the genuine shadow side) so the warm key side stays unambiguously warm
                // = one clean continuous terminator, not per-bodypart patches.
                float coolGate = saturate(-lambert * 1.6);                          // 1 deep shadow side .. 0 lit side
                // r10 L3: a fire-lit interior's shadow side is a low-chroma WARM-GREY (hearth bounce), not
                // blue moonlight. Bias the fill toward warm umber-grey, heavily desaturated.
                fixed3 coolNeutral = (fixed3(0.16,0.14,0.13) + _KeyColor.rgb * 0.06) * 0.55; // warm-grey ambient bounce
                fixed3 warmLift = _KeyColor.rgb * 0.12;                              // warm, key side only
                lit += albedo * coolNeutral * coolGate;                             // lift ONLY the shadow side
                lit += albedo * warmLift * keyMask;                                 // warm bounce ONLY the key side

                // warm floor-bounce
                float down = saturate(-N.y);
                lit += albedo * _KeyColor.rgb * down * _BounceStrength;

                // directional warm rim — KEPT SUBTLE; the r2 panel's #1 tell was the rim/bloom HALO that
                // made the hero out-glow the hearth. The rim is now a thin sliver only on the strongly
                // hearth-facing silhouette, scaled well down, and clamped (no additive bloom blow-out).
                float fres = pow(1.0 - saturate(dot(N, V)), 4.0);    // tighter (4) so it's a sliver, not a band
                float keyFacing = saturate(dot(N, L));               // 0 on shadow side, 1 on key side (no 0.5 bias)
                float rim = fres * keyFacing * _RimStrength;
                lit += _KeyColor.rgb * rim * 0.35;

                // COOL BACK-RIM — silhouette separation from the OPPOSITE side (azimuth ~220, behind/away from
                // hearth). This is a cool steel-blue sliver on the back edge of the figure, giving depth and
                // "painted into the world" separation. backFacing: max when N points AWAY from key (shadow side
                // silhouette). The cool rim adds the blue-gray edge that PoE2/Disco Elysium figures all have.
                float backFacing = saturate(-dot(N, L) + 0.1);        // strongest on shadow-side silhouette
                float coolRim = fres * backFacing * _CoolRimStrength;
                lit += _CoolRimColor.rgb * coolRim * 0.55;

                // CLAMP actor luminance below the hearth (L3+L4 r2 CONSENSUS CRITICAL: the actor must
                // never be the brightest thing in frame). Scale the whole result down if its luma exceeds
                // _MaxLuma, preserving hue.
                float lNow = lum(lit);
                if (lNow > _MaxLuma) lit *= (_MaxLuma / lNow);
                lit = saturate(lit);

                // ============ REAL-TIME PAINTERLY PASS (the L4-ceiling breaker) ============
                // (PAINT 2) VALUE POSTERIZE — quantize lit value into brushy steps (keep hue). The
                // r-final L4 panel said the hard stair-step banding read as "crunchy digital", not
                // brushwork. SOFTEN each band transition with a grain-driven dither so the steps blend
                // like overlapping strokes; bias steps toward the MIDTONES so the form keeps 4-5 readable
                // planes instead of collapsing to a black band.
                // r10 L4 (BOTH runs): independent RGB posterize produced an "orange/black checker / mosaic"
                // = indexed-color CG tell. FIX: quantize VALUE only and snap CHROMA to follow the value
                // plane (a single hue per region), with a SOFT (smoothstep) boundary so planes read as
                // broad painted masses with brushy transitions, not hard aliased tiles.
                float vLit = max(lum(lit), 1e-4);
                float steps = max(2.0, _Posterize);
                // soft-quantize value: snap toward the nearest band but ramp across a small luma window so
                // band edges are smooth, not stair-stepped (boundary_softness).
                float sv = vLit * steps;
                float fb = frac(sv);
                float soft = smoothstep(0.5 - 0.18, 0.5 + 0.18, fb);   // soft step around each boundary
                float vQ = (floor(sv) + soft) / steps;
                float vTarget = lerp(vLit, vQ, 0.78);
                // apply as a VALUE scale (preserves hue/chroma ratio => no RGB checker), then desaturate
                // slightly within the plane so chroma is consistent across the mass.
                fixed3 chroma = lit / vLit;                            // hue/sat direction
                float lc = lum(chroma);
                chroma = lerp(chroma, fixed3(lc,lc,lc), 0.12);         // calm per-pixel chroma jitter within a plane
                lit = chroma * vTarget;

                // (PAINT 3) BRUSH-GRAIN — DIRECTIONAL strokes (r8 L4: isotropic white-noise read as TV
                // static, not brushwork). STRETCH the noise hard along one axis so it reads as elongated
                // directional strokes that follow the figure's flow, and DROP the amplitude (~40%) so the
                // value-quantize does the band-blending, not the noise.
                float2 strokeUv = float2(i.uv.x * _BrushScale, i.uv.y * _BrushScale * 0.22); // y-stretched = vertical strokes
                strokeUv += float2(strokeUv.y * 0.25, 0.0);          // slight shear -> hand-laid diagonal
                float g = vnoise(strokeUv) * 0.65 + vnoise(strokeUv * float2(2.3, 1.0)) * 0.35;
                float grain = (g - 0.5) * 2.0;                       // -1..1
                lit *= (1.0 + grain * _BrushStrength * 0.30);        // lower amplitude (was 0.5)
                lit += _PaintLift * grain * keyMask * 0.6;           // subtle lifted strokes on the lit side

                // (PAINT 4) PALETTE-SNAP — pull sat toward the muted painted plate range.
                float lP = lum(lit);
                lit = lerp(lit, fixed3(lP, lP, lP), _PaletteSnap * 0.5);
                lit = lerp(lit, lit * lightTint, _PaletteSnap * 0.4);

                // (PAINT 4.6) SCENE-COLOR CONTAMINATION (R11 L4 fix) — bleed backdrop palette into the
                // actor so it feels PAINTED INTO the scene rather than COMPOSITED ON TOP. Lit side picks up
                // the hearth warm amber; shadow side picks up the cool stone grey. keyMask controls which.
                // BG2/PoE characters get this "for free" because they are hand-painted to match the backdrop;
                // our 3D model needs it synthetically. A small blend (0.15-0.25) is enough for the eye to
                // register "same painter, same color world" without losing the character's own hue identity.
                if (_SceneContamination > 0.001) {
                    fixed3 sceneBlend = lerp(_SceneShadowTint.rgb, _SceneLitSideTint.rgb, keyMask);
                    lit = lerp(lit, lit * sceneBlend, _SceneContamination);
                }

                // (PAINT 4.5) ATMOSPHERIC DEPTH WASH (L4 CRITICAL fix) — a distant actor must lose
                // local contrast + saturation and bleed toward the room's ambient, the way a painted
                // figure recedes. _AtmDepth is set per-actor by its row depth (0 front .. ~0.5 back).
                // (a) desaturate toward grey, (b) compress contrast toward the fog value, (c) tint
                // toward the ambient fog color. Front actors (_AtmDepth=0) are untouched.
                if (_AtmDepth > 0.001)
                {
                    float la = lum(lit);
                    fixed3 fog = _AtmColor.rgb;
                    float fogV = lum(fog);
                    // desaturate (lose ~half sat at full depth)
                    lit = lerp(lit, fixed3(la,la,la), _AtmDepth * 0.45);
                    // compress local contrast toward the fog value (lift blacks, pull highlights)
                    lit = lerp(lit, lerp(lit, fixed3(fogV,fogV,fogV), 0.5), _AtmDepth * 0.55);
                    // tint toward the ambient fog hue
                    lit = lerp(lit, fog + (lit - la), _AtmDepth * 0.30);
                }

                // RE-CLAMP luma after the painterly pass (grain/lift can push values back up; the actor
                // must stay below the hearth — the binding r2 cross-lens fix).
                float lFinal = lum(lit);
                if (lFinal > _MaxLuma) lit *= (_MaxLuma / lFinal);
                lit = saturate(lit);

                // (PAINT 5) EDGE-FEATHER — the razor silhouette is the #1 maquette tell. Fade alpha on
                // grazing-angle fragments, broken by grain so the contour dissolves like bristles. The
                // interior stays opaque; only the ~few-px silhouette band feathers into the plate.
                float facing = saturate(dot(N, V));                  // 0 at silhouette, 1 facing camera
                float edge = smoothstep(0.0, max(0.02, _EdgeSoften), facing);
                float feather = saturate(edge + (g - 0.5) * 0.40);   // grain-broken edge
                float alpha = feather;

                return fixed4(lit, alpha);
            }
            ENDCG
        }
    }
    Fallback "Standard"
}
