Shader "WorldOS/ContactShadow"
{
    // Floor-hugging soft contact shadow. Alpha-blended dark blob that darkens whatever
    // (the painted plate) is behind it. Unlit, ZWrite Off, ZTest Always so it reliably
    // lands on top of the camera-locked plate regardless of floor depth (the plate is
    // BackdropUnlit ZTest Always; a plain Sprites/Default quad was not darkening — this
    // forces the composite). Authored CGPROGRAM (Metal TBDR friendly).
    Properties
    {
        _MainTex ("Shadow (radial alpha)", 2D) = "white" {}
        _Color   ("Shadow Color", Color) = (0,0,0,0.7)
    }
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" }
        Pass
        {
            ZWrite Off
            ZTest Always
            Cull Off
            Blend SrcAlpha OneMinusSrcAlpha

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            sampler2D _MainTex;
            float4 _MainTex_ST;
            fixed4 _Color;

            struct appdata { float4 vertex : POSITION; float2 uv : TEXCOORD0; };
            struct v2f { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };

            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                return o;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                fixed a = tex2D(_MainTex, i.uv).a;   // radial alpha falloff
                return fixed4(_Color.rgb, a * _Color.a);
            }
            ENDCG
        }
    }
}
