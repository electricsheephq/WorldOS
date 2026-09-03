// WorldOS/UnlitColor — the HUD quad shader (#1777).
//
// Why this file exists: CombatSurfaceClient's HP bars and the active-turn marker used the BUILT-IN
// "Unlit/Color". No scene asset references it, so the player build strips it: Shader.Find resolved in the
// Editor and returned null in the shipped .app, and `new Material(null)` threw an ArgumentNullException
// that unwound out of the /combat-surface poll coroutine's MoveNext — killing the fetch loop for the whole
// session. A PROJECT shader can be guaranteed instead: BuildMacOSPlayer.RequiredAlwaysIncluded registers it
// into Graphics -> Always-Included Shaders at build time, and qa/check_always_included_shaders.py fails the
// pre-flight if that guarantee is ever removed.
//
// Semantics: a solid _Color quad (the property name CombatSurfaceClient's `material.color` writes), alpha
// blended, ZWrite off and ZTest Always so a HUD element above an actor's head is never eaten by the
// painterly backdrop or a depth-only occluder proxy in front of it. No texture, no lighting, no fog.
Shader "WorldOS/UnlitColor"
{
    Properties
    {
        _Color ("Color", Color) = (1,1,1,1)
    }
    SubShader
    {
        Tags { "Queue"="Overlay" "RenderType"="Transparent" "IgnoreProjector"="True" "PreviewType"="Plane" }
        Blend SrcAlpha OneMinusSrcAlpha
        Cull Off
        Lighting Off
        ZWrite Off
        ZTest Always
        Fog { Mode Off }

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            fixed4 _Color;

            struct appdata_t { float4 vertex : POSITION; UNITY_VERTEX_INPUT_INSTANCE_ID };
            struct v2f { float4 vertex : SV_POSITION; UNITY_VERTEX_OUTPUT_STEREO };

            v2f vert (appdata_t v)
            {
                v2f o;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_INITIALIZE_OUTPUT(v2f, o);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.vertex = UnityObjectToClipPos(v.vertex);
                return o;
            }

            fixed4 frag (v2f i) : SV_Target { return _Color; }
            ENDCG
        }
    }
    Fallback Off
}
