using UnityEngine;
using UnityEditor;

/// <summary>
/// Editor script - run via Tools > Setup Painterly Scene
/// Sets up the fake-3D tavern scene with correct materials and camera.
/// </summary>
public class SetupPainterlyScene : MonoBehaviour
{
    [MenuItem("Tools/Setup Painterly Scene")]
    public static void Setup()
    {
        // --- CAMERA ---
        var camGO = GameObject.Find("Main Camera");
        if (camGO != null)
        {
            var cam = camGO.GetComponent<Camera>();
            // Orthographic dimetric: size controls how much world fits vertically
            cam.orthographic = false; // perspective gives more natural feel for 3D props
            cam.fieldOfView = 35f;    // narrow FOV = less distortion, closer to ortho look
            cam.farClipPlane = 100f;
            cam.nearClipPlane = 0.1f;
            cam.backgroundColor = new Color(0.05f, 0.04f, 0.03f, 1f); // dark tavern
            cam.clearFlags = CameraClearFlags.SolidColor;
            
            // Position: high back, angled 30° down
            camGO.transform.position = new Vector3(0f, 5f, -6f);
            camGO.transform.eulerAngles = new Vector3(28f, 0f, 0f);
        }

        // --- DIRECTIONAL LIGHT (warm amber candlelight) ---
        var lightGO = GameObject.Find("Directional Light");
        if (lightGO != null)
        {
            var lt = lightGO.GetComponent<Light>();
            lt.color = new Color(1.0f, 0.72f, 0.35f, 1f);
            lt.intensity = 1.1f;
            lt.shadows = LightShadows.Soft;
            lt.shadowStrength = 0.6f;
            lt.shadowBias = 0.05f;
            lightGO.transform.eulerAngles = new Vector3(35f, -45f, 0f);
        }

        // --- BACKDROP QUAD: Unlit/Texture so painting renders without lighting tint ---
        var backdropGO = GameObject.Find("Backdrop");
        if (backdropGO != null)
        {
            backdropGO.transform.position = new Vector3(0f, 3.5f, 9f);
            backdropGO.transform.eulerAngles = Vector3.zero;
            backdropGO.transform.localScale = new Vector3(18f, 10.5f, 1f);

            var tex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/tavern_backdrop.png");
            if (tex != null)
            {
                // Built-in pipeline: Unlit/Texture shows painting without lighting
                var mat = new Material(Shader.Find("Unlit/Texture"));
                mat.mainTexture = tex;
                mat.renderQueue = 900; // render before geometry
                AssetDatabase.CreateAsset(mat, "Assets/BackdropMat.mat");
                backdropGO.GetComponent<Renderer>().material = mat;
                var r = backdropGO.GetComponent<Renderer>();
                r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                r.receiveShadows = false;
            }
            else
            {
                Debug.LogWarning("SetupPainterlyScene: tavern_backdrop.png not found in Assets");
            }
        }

        // --- ACTOR: Standard lit, muted purple-grey ---
        var actorGO = GameObject.Find("Actor");
        if (actorGO != null)
        {
            actorGO.transform.position = new Vector3(0f, 1.0f, 3f);
            actorGO.transform.localScale = new Vector3(0.55f, 1.0f, 0.55f);

            var mat = new Material(Shader.Find("Standard"));
            mat.color = new Color(0.52f, 0.44f, 0.62f, 1f);
            mat.SetFloat("_Metallic", 0f);
            mat.SetFloat("_Glossiness", 0.2f);
            AssetDatabase.CreateAsset(mat, "Assets/ActorMat.mat");
            actorGO.GetComponent<Renderer>().material = mat;

            var r = actorGO.GetComponent<Renderer>();
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;
            r.receiveShadows = true;
        }

        // --- TABLE: dark wood brown, z=1.2 (between camera and actor at z=3) ---
        var tableGO = GameObject.Find("ForegroundTable");
        if (tableGO != null)
        {
            tableGO.transform.position = new Vector3(0f, 0.45f, 1.2f);
            tableGO.transform.localScale = new Vector3(3.2f, 0.85f, 1.1f);

            var mat = new Material(Shader.Find("Standard"));
            mat.color = new Color(0.38f, 0.24f, 0.10f, 1f);
            mat.SetFloat("_Metallic", 0f);
            mat.SetFloat("_Glossiness", 0.15f);
            AssetDatabase.CreateAsset(mat, "Assets/TableMat.mat");
            tableGO.GetComponent<Renderer>().material = mat;

            var r = tableGO.GetComponent<Renderer>();
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;
            r.receiveShadows = true;
        }

        // --- FLOOR: dark stone ---
        var floorGO = GameObject.Find("Floor");
        if (floorGO != null)
        {
            floorGO.transform.position = new Vector3(0f, 0f, 3.5f);
            floorGO.transform.localScale = new Vector3(3.5f, 1f, 3f);

            var mat = new Material(Shader.Find("Standard"));
            mat.color = new Color(0.22f, 0.20f, 0.18f, 1f);
            mat.SetFloat("_Metallic", 0f);
            mat.SetFloat("_Glossiness", 0.05f);
            AssetDatabase.CreateAsset(mat, "Assets/FloorMat.mat");
            floorGO.GetComponent<Renderer>().material = mat;

            var r = floorGO.GetComponent<Renderer>();
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            r.receiveShadows = true;
        }

        // Refresh
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        Debug.Log("SetupPainterlyScene: DONE - BuiltIn pipeline, Unlit backdrop");
    }
}
