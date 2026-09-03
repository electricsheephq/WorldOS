Shader "WorldOS/PainterlyRoom"
{
    Properties
    {
        _MainTex ("Albedo", 2D) = "white" {}
        _Tint ("Tint", Color) = (1,1,1,1)
        _TriScale ("World units per tile", Float) = 2.0
        _TriSharpness ("Blend sharpness", Range(1,8)) = 4
        _Desat ("Desaturate", Range(0,1)) = 0.25
        _KeyColor ("Key (hearth) Color", Color) = (1.0,0.604,0.271,1)
        _AmbientColor ("Ambient Fill Color", Color) = (0.227,0.247,0.333,1)
        _KeyDir ("Key Dir (world)", Vector) = (0.5,0.55,0.7,0)
        _KeyStrength ("Key Strength", Range(0,3)) = 1.0
        _RimStrength ("Rim Strength", Range(0,2)) = 0.5
        _BounceStrength ("Warm Floor Bounce", Range(0,1)) = 0.2
        _Posterize ("Value Posterize Steps", Range(2,16)) = 6.0
        _BrushStrength ("Brush-Grain Strength", Range(0,1)) = 0.40
        _BrushScale ("Brush-Grain Scale", Range(2,60)) = 20.0
        _PaletteSnap ("Palette Snap (sat pull)", Range(0,1)) = 0.30
        _PaintLift ("Paint Value Lift", Range(0,0.3)) = 0.07
        _AmbientLift ("Ambient Lift (no black mass)", Range(0,0.5)) = 0.18
        _MaxLuma ("Max Luma (stay below hearth)", Range(0.3,1)) = 0.72
        _TermSharp ("Terminator Sharpness", Range(0,1)) = 0.5
        _CoolRimStrength ("Cool Rim (back-facing) Strength", Range(0,2)) = 0.55
        _CoolRimColor ("Cool Rim Color", Color) = (0.10,0.21,0.31,1)
        _SceneLitSideTint ("Scene Lit-Side Tint (hearth amber)", Color) = (0.78,0.48,0.12,1)
        _SceneShadowTint ("Scene Shadow-Side Tint (stone blue-grey)", Color) = (0.17,0.22,0.32,1)
        _SceneContamination ("Scene Color Contamination 0..1", Range(0,0.5)) = 0.18
        _AtmDepth ("Atmospheric Wash 0..1", Range(0,1)) = 0.0
        _AtmColor ("Atmospheric Fog Color", Color) = (0.13,0.13,0.16,1)
    }
    SubShader
    {
        Tags { "Queue"="Geometry" "RenderType"="Opaque" }
        Pass
        {
            Tags { "LightMode"="ForwardBase" }
            ZWrite On
            Cull Back
            CGPROGRAM
            #pragma target 3.0
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_fwdbase
            #include "UnityCG.cginc"
            #include "Lighting.cginc"
            #include "AutoLight.cginc"

            sampler2D _MainTex;
            fixed4 _Tint, _KeyColor, _AmbientColor, _CoolRimColor;
            fixed4 _SceneLitSideTint, _SceneShadowTint, _AtmColor;
            float4 _KeyDir;
            float _TriScale, _TriSharpness, _Desat, _KeyStrength, _RimStrength, _BounceStrength;
            float _Posterize, _BrushStrength, _BrushScale, _PaletteSnap, _PaintLift, _AmbientLift;
            float _MaxLuma, _TermSharp, _CoolRimStrength, _SceneContamination, _AtmDepth;

            struct appdata { float4 vertex : POSITION; float3 normal : NORMAL; };
            struct v2f { float4 pos : SV_POSITION; float3 wpos : TEXCOORD0; float3 normal : TEXCOORD1; float3 view : TEXCOORD2; };
            v2f vert(appdata v)
            {
                v2f o; o.pos = UnityObjectToClipPos(v.vertex);
                o.wpos = mul(unity_ObjectToWorld, v.vertex).xyz;
                o.normal = UnityObjectToWorldNormal(v.normal);
                o.view = _WorldSpaceCameraPos - o.wpos;
                return o;
            }
            float lum(fixed3 c) { return dot(c, fixed3(0.299,0.587,0.114)); }
            float hash21(float2 p) { p=frac(p*float2(123.34,456.21)); p+=dot(p,p+45.32); return frac(p.x*p.y); }
            float noise(float2 p)
            {
                float2 q=floor(p), f=frac(p), u=f*f*(3.0-2.0*f);
                return lerp(lerp(hash21(q),hash21(q+float2(1,0)),u.x),lerp(hash21(q+float2(0,1)),hash21(q+1),u.x),u.y);
            }
            float3 triWeights(float3 n)
            {
                float3 w=pow(max(abs(n),0.0001),_TriSharpness);
                return w/max(w.x+w.y+w.z,0.0001);
            }
            fixed3 triSample(float3 p, float3 w)
            {
                p /= max(_TriScale,0.0001);
                fixed3 x=tex2D(_MainTex,p.zy).rgb;
                fixed3 y=tex2D(_MainTex,p.xz).rgb;
                fixed3 z=tex2D(_MainTex,p.xy).rgb;
                return x*w.x+y*w.y+z*w.z;
            }
            // Explicit fire beacons (set as globals by the kit builder / capture payload: xyz = world pos, w = range;
            // color.rgb = light colour * intensity). Independent of Unity's vertex-light plumbing, so the brazier
            // pools land on the floor deterministically in Editor captures AND in the player.
            float4 _WOSFirePos[4]; float4 _WOSFireColor[4]; float _WOSFireCount;
            fixed3 pointLights(float3 p, float3 n)
            {
                fixed3 c=0;
                for (int f=0;f<4;f++)
                {
                    if (f>=_WOSFireCount) break;
                    float3 d=_WOSFirePos[f].xyz-p; float d2=max(dot(d,d),0.0001); float rng=max(_WOSFirePos[f].w,0.01);
                    float att=saturate(1.0-sqrt(d2)/rng); att*=att;
                    c+=_WOSFireColor[f].rgb*lerp(0.35,1.0,saturate(dot(n,d*rsqrt(d2))))*att;
                }
                // (Unity vertex-light term removed 2026-09-03: demoted ForcePixel lights leaked the blue CoolKey into unity_4Light*;
                // point lighting for painterly room surfaces is owned by the explicit _WOSFire* globals.)

                return c;
            }
            fixed4 frag(v2f i) : SV_Target
            {
                float3 N=normalize(i.normal), V=normalize(i.view), L=normalize(_KeyDir.xyz), w=triWeights(N);
                fixed3 albedo=triSample(i.wpos,w)*_Tint.rgb; float a=lum(albedo); albedo=lerp(albedo,a.xxx,_Desat);
                float lambert=dot(N,L), lo=lerp(-0.25,0.18,_TermSharp), hi=lerp(0.55,0.34,_TermSharp);
                float keyMask=smoothstep(lo,hi,lambert);
                fixed3 key=lerp(fixed3(1,1,1),_KeyColor.rgb,0.92), cool=_AmbientColor.rgb*1.9+fixed3(0,0.02,0.10);
                fixed3 lit=albedo*lerp(cool,key,keyMask)*lerp(0.72+_AmbientLift,1.05*_KeyStrength,keyMask);
                lit+=albedo*pointLights(i.wpos,N);
                lit+=albedo*((fixed3(0.16,0.14,0.13)+_KeyColor.rgb*0.06)*0.55)*saturate(-lambert*1.6);
                lit+=albedo*_KeyColor.rgb*(keyMask*0.12+saturate(-N.y)*_BounceStrength);
                float fres=pow(1.0-saturate(dot(N,V)),4.0);
                lit+=_KeyColor.rgb*fres*saturate(dot(N,L))*_RimStrength*0.35;
                lit+=_CoolRimColor.rgb*fres*saturate(-dot(N,L)+0.1)*_CoolRimStrength*0.55;
                float v=max(lum(lit),0.0001), steps=max(2.0,_Posterize), sv=v*steps;
                float vq=(floor(sv)+smoothstep(0.32,0.68,frac(sv)))/steps;
                lit=(lit/v)*lerp(v,vq,0.78);
                float scale=_BrushScale/max(_TriScale,0.0001);
                float g=noise(i.wpos.zy*scale)*w.x+noise(i.wpos.xz*scale)*w.y+noise(i.wpos.xy*scale)*w.z;
                float grain=(g-0.5)*2.0; lit*=1.0+grain*_BrushStrength*0.30; lit+=_PaintLift*grain*keyMask*0.6;
                float lp=lum(lit); lit=lerp(lit,lp.xxx,_PaletteSnap*0.5); lit=lerp(lit,lit*lerp(cool,key,keyMask),_PaletteSnap*0.4);
                lit=lerp(lit,lit*lerp(_SceneShadowTint.rgb,_SceneLitSideTint.rgb,keyMask),_SceneContamination);
                float la=lum(lit), fv=lum(_AtmColor.rgb);
                lit=lerp(lit,la.xxx,_AtmDepth*0.45); lit=lerp(lit,lerp(lit,fv.xxx,0.5),_AtmDepth*0.55);
                lit=lerp(lit,_AtmColor.rgb+(lit-la),_AtmDepth*0.30);
                float lf=lum(lit); if (lf>_MaxLuma) lit*=_MaxLuma/lf;
                return fixed4(saturate(lit),1);
            }
            ENDCG
        }
    }
    Fallback "Standard"
}
