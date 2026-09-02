Shader "WorldOS/PainterlyBackdrop"
{
    // LEVER 2 — UNIFY the painterly language onto the FLOOR/PLATE. The actors carry a real-time
    // painterly post (kuwahara flatten + soft value-posterize + directional brush-grain +
    // palette-snap); the plate was a flat passthrough photo-texture => the frame read as TWO
    // rendering languages ("posterized actors on a flat floor"). This shader applies the SAME
    // recipe to the backdrop plate so the whole frame is ONE painting. It is NOT a relight (the
    // plate is already lit by Scenario); it is a surface-style match:
    //   (1) KUWAHARA-lite flatten   -> merge photo micro-detail into painterly regions
    //   (2) SOFT VALUE POSTERIZE    -> brushy discrete value masses (value-only, hue preserved => no checker)
    //   (3) DIRECTIONAL BRUSH-GRAIN -> visible vertical-ish strokes matched to the actor grain
    //   (4) PALETTE-SNAP            -> calm chroma into the muted painted range (same pull as actors)
    //   (5) gentle S-curve contrast -> reads as deliberate paint values, not a flat scan
    // All tunable; defaults are matched to PainterlyActor's plate-side numbers. Strength is
    // deliberately LOWER on the plate than the actors (the plate already has good painterly DNA from
    // Scenario; we only need to add brush surface + value structure so it sits in the same family).
    Properties
    {
        _MainTex      ("Plate", 2D) = "white" {}
        _Kuwahara     ("Kuwahara Radius (texels)", Range(0,5)) = 2.0
        _Posterize    ("Value Posterize Steps", Range(2,24)) = 10.0
        _PosterStrength("Posterize Mix", Range(0,1)) = 0.45
        _BrushStrength("Brush-Grain Strength", Range(0,1)) = 0.10
        _BrushScale   ("Brush-Grain Scale", Range(2,120)) = 60.0
        _PaletteSnap  ("Palette Snap (sat pull)", Range(0,1)) = 0.18
        _Contrast     ("Paint Contrast", Range(0.5,2)) = 1.12
        _Saturation   ("Saturation", Range(0,2)) = 1.04
        // ---- exposure + tonal repair (L5 tactical-legibility + L6 crushed-void/monochrome fixes) ----
        _Exposure     ("Exposure (gain)", Range(0.5,3)) = 1.0
        _ShadowLift   ("Shadow Lift (kill crushed black)", Range(0,0.3)) = 0.0
        _ShadowTint   ("Shadow Tint (cool the darks, kill amber monochrome)", Color) = (0.05,0.06,0.09,1)
        _ShadowTintAmt("Shadow Tint Amount", Range(0,1)) = 0.0
    }
    SubShader
    {
        Tags { "Queue"="Background" "RenderType"="Opaque" }

        Pass
        {
            ZWrite Off
            ZTest Always
            Cull Off

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            sampler2D _MainTex;
            float4 _MainTex_ST;
            float4 _MainTex_TexelSize;   // (1/w, 1/h, w, h)
            float _Kuwahara, _Posterize, _PosterStrength;
            float _BrushStrength, _BrushScale, _PaletteSnap, _Contrast, _Saturation;
            float _Exposure, _ShadowLift, _ShadowTintAmt;
            fixed4 _ShadowTint;

            struct appdata { float4 vertex:POSITION; float2 uv:TEXCOORD0; };
            struct v2f { float2 uv:TEXCOORD0; float4 vertex:SV_POSITION; };

            v2f vert (appdata v)
            {
                v2f o;
                o.vertex = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                return o;
            }

            float lum(fixed3 c){ return dot(c, fixed3(0.299,0.587,0.114)); }

            float hash21(float2 p){ p=frac(p*float2(123.34,456.21)); p+=dot(p,p+45.32); return frac(p.x*p.y); }
            float vnoise(float2 p){
                float2 i=floor(p); float2 f=frac(p);
                float a=hash21(i), b=hash21(i+float2(1,0)), c=hash21(i+float2(0,1)), d=hash21(i+float2(1,1));
                float2 u=f*f*(3.0-2.0*f);
                return lerp(lerp(a,b,u.x), lerp(c,d,u.x), u.y);
            }

            // KUWAHARA (4-quadrant, variance-minimizing) — flattens photo micro-detail to painterly regions.
            fixed3 kuwahara(float2 uv, float r)
            {
                if (r < 0.25) return tex2D(_MainTex, uv).rgb;
                float2 tx = _MainTex_TexelSize.xy * r;
                fixed3 bestMean = tex2D(_MainTex, uv).rgb; float bestVar = 1e9;
                const float2 q[4] = { float2(-1,-1), float2(1,-1), float2(-1,1), float2(1,1) };
                [unroll]
                for (int k=0;k<4;k++)
                {
                    fixed3 m=0; float lm=0,lm2=0; const int N=4;
                    [unroll] for (int sx=0;sx<=1;sx++)
                    [unroll] for (int sy=0;sy<=1;sy++)
                    {
                        float2 o=float2(q[k].x*sx,q[k].y*sy)*tx;
                        fixed3 s=tex2D(_MainTex, uv+o).rgb;
                        m+=s; float l=lum(s); lm+=l; lm2+=l*l;
                    }
                    m/=N; lm/=N; lm2/=N;
                    float var=lm2-lm*lm;
                    if (var<bestVar){ bestVar=var; bestMean=m; }
                }
                return bestMean;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                fixed3 c = kuwahara(i.uv, _Kuwahara);

                // TONAL REPAIR (L5/L6): exposure gain to lift the dim plate into a playable range,
                // a SHADOW LIFT that raises the black point so the crushed-black left void becomes a
                // legible dark-mid (never lets a primary mass die to 0), and a COOL SHADOW TINT that
                // injects blue/teal into the darks to break the monochrome-amber wash (the darker a
                // pixel, the more it gets tinted/lifted => the hearth highlight is untouched).
                c *= _Exposure;
                float vRaw = lum(c);
                // tighter dark mask so the tint/lift touch only the DEEP shadows, not the warm mids
                float darkW = 1.0 - smoothstep(0.0, 0.22, vRaw);   // 1 in deep darks .. 0 by mid
                c += _ShadowLift * darkW;                          // lift the black point in the darks only
                // cool the darks by ROTATING hue toward the cool tint at constant-ish value (not ADDING
                // light) — a bright additive tint flooded the frame into haze. Blend toward a value-matched
                // cool version of the dark pixel instead.
                float vd = lum(c);
                fixed3 coolDark = _ShadowTint.rgb * (vd / max(lum(_ShadowTint.rgb), 0.001));  // cool hue at this pixel's value
                c = lerp(c, coolDark, _ShadowTintAmt * darkW);
                c = saturate(c);

                // SOFT VALUE POSTERIZE (value-only => hue preserved, no RGB mosaic). Matches the
                // actor's soft-posterize so floor + actors share the same value-mass structure.
                float v = max(lum(c), 1e-4);
                float steps = max(2.0, _Posterize);
                float sv = v * steps;
                float fb = frac(sv);
                float soft = smoothstep(0.5-0.18, 0.5+0.18, fb);
                float vQ = (floor(sv) + soft) / steps;
                float vTarget = lerp(v, vQ, _PosterStrength);
                fixed3 chroma = c / v;
                c = chroma * vTarget;

                // DIRECTIONAL BRUSH-GRAIN — same vertical-stretched stroke family as the actors, very
                // subtle on the plate (it already has paint DNA; we just want matching surface tooth).
                float2 strokeUv = float2(i.uv.x * _BrushScale, i.uv.y * _BrushScale * 0.22);
                strokeUv += float2(strokeUv.y * 0.25, 0.0);
                float g = vnoise(strokeUv)*0.65 + vnoise(strokeUv*float2(2.3,1.0))*0.35;
                float grain = (g - 0.5) * 2.0;
                c *= (1.0 + grain * _BrushStrength * 0.30);

                // PALETTE-SNAP — calm chroma into the muted painted range (same pull direction as actors).
                float lP = lum(c);
                c = lerp(c, fixed3(lP,lP,lP), _PaletteSnap * 0.5);

                // saturation + gentle paint S-curve contrast (anchored at 0.5).
                float lS = lum(c);
                c = lerp(fixed3(lS,lS,lS), c, _Saturation);
                c = saturate((c - 0.5) * _Contrast + 0.5);

                return fixed4(c, 1.0);
            }
            ENDCG
        }
    }
    Fallback "Unlit/Texture"
}
