using UnityEngine;
using UnityEditor;
using System.Net;
using System.IO;

/// <summary>
/// CombatSurfaceDemo (A4/B2 demo) — an EDIT-MODE menu item that GETs the engine combat surface
/// (synchronously, no Play mode) and positions the painterly scene's rigged actors at the engine's
/// authoritative cells. It also owns the COMBAT-OVERLAY visuals that belong to the combat renderer
/// (not the static tavern build): each actor's contact shadow FOLLOWS it (fixes the orphaning bug
/// where moving an actor left its build-time shadow behind), and each actor gets a BG/PoE-style floor
/// SELECTION RING (gold ally / red enemy) — the visual-critic panel flagged the missing rings as a
/// cross-lens CRITICAL (L5 findability + L1/L2 grounding). Cell&lt;-&gt;world mirrors ClosedLoopBuilder
/// (14x10, cell 5, row-flip). The rings/shadow moves are renderer-local VIEW only (engine stays sole writer).
/// </summary>
public static class CombatSurfaceDemo
{
    const string ViewerUrl = "http://127.0.0.1:8765";
    const string CampaignId = "camp_6ee62cf998fe";
    const int Cols = 14, Rows = 10;
    const float CellSize = 5f, FloorY = 0f;

    static float OriginX { get { return -(Cols * CellSize) / 2f; } }
    const float OriginZ = 0f;
    static Vector3 CellToWorld(int c, int r) { return new Vector3(OriginX + (c + 0.5f) * CellSize, FloorY, OriginZ + (Rows - r - 0.5f) * CellSize); }

    [System.Serializable] class Tok { public string id; public string name; public string team; public int x; public int y; }
    [System.Serializable] class Surf { public string turnToken; public Tok[] tokens; }

    [MenuItem("Tools/WorldOS/Combat/Position Actors From Surface")]
    public static void PositionFromSurface()
    {
        string json;
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(ViewerUrl + "/combat-surface?campaign=" + CampaignId);
            req.Timeout = 6000;
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var sr = new StreamReader(resp.GetResponseStream()))
                json = sr.ReadToEnd();
        }
        catch (System.Exception e) { Debug.LogError("[CSD] GET failed: " + e.Message); return; }

        var s = JsonUtility.FromJson<Surf>(json);
        if (s == null || s.tokens == null) { Debug.LogError("[CSD] no tokens in surface"); return; }
        foreach (var t in s.tokens)
        {
            bool foe = (t.team == "foe");
            string name = foe ? "MonsterGoblin" : "HeroFighter";
            var go = GameObject.Find(name);
            if (go == null) { Debug.LogWarning("[CSD] actor not found: " + name); continue; }
            var w = CellToWorld(t.x, t.y);
            Vector3 old = go.transform.position;
            go.transform.position = new Vector3(w.x, go.transform.position.y, w.z);  // keep rig Y
            Vector3 d = new Vector3(w.x - old.x, 0f, w.z - old.z);
            MoveShadow(name, d);          // shadow follows the actor (orphaning fix)
            EnsureRing(go.transform, foe);
            Debug.Log("[CSD] " + name + " (" + t.team + ") -> cell (" + t.x + "," + t.y + ")");
        }
        SceneView.RepaintAll();
    }

    // Move the actor's build-time contact shadow + AO by the same delta so they stay under the feet.
    static void MoveShadow(string actorName, Vector3 delta)
    {
        foreach (var nm in new[] { actorName + "_Shadow", actorName + "_Shadow_AO" })
        {
            var g = GameObject.Find(nm);
            if (g != null) g.transform.position += delta;
        }
    }

    // Find-or-create the BG/PoE selection ring under the actor (gold ally / red enemy) and place it at the cell.
    static void EnsureRing(Transform actor, bool foe)
    {
        string nm = actor.name + "_Ring";
        var ring = GameObject.Find(nm);
        if (ring == null)
        {
            var sh = Shader.Find("WorldOS/SelectionRing");
            if (sh == null) { Debug.LogWarning("[CSD] SelectionRing shader missing"); return; }
            var q = GameObject.CreatePrimitive(PrimitiveType.Quad);
            q.name = nm;
            UnityEngine.Object.DestroyImmediate(q.GetComponent<Collider>());
            q.transform.SetParent(actor.parent, false);   // sibling under Actors (no actor scale inheritance)
            q.transform.localEulerAngles = new Vector3(90f, 0f, 0f);
            q.transform.localScale = new Vector3(foe ? 5f : 6f, foe ? 5f : 6f, 1f);
            var m = new Material(sh);
            m.SetColor("_Color", foe ? new Color(0.88f, 0.28f, 0.30f, 1f) : new Color(1.0f, 0.80f, 0.42f, 1f));
            m.SetFloat("_Alpha", foe ? 0.80f : 0.85f);
            m.renderQueue = 1100;          // after contact shadow (1000), before actors (2000)
            var rr = q.GetComponent<Renderer>();
            rr.sharedMaterial = m;
            rr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            rr.receiveShadows = false;
            ring = q;
        }
        // Snap to the actor's VISUAL center + size the ring to its footprint (a fixed ring around the
        // small goblin read as decoupled — panel L2). Also clamp the atmospheric wash so a back-row
        // combatant stays SOLID/findable (panel L4/L5: the goblin was a washed-out ghost). Combat must
        // read the enemy — visibility overrides the static-tavern depth styling for combatants.
        var rs = actor.GetComponentsInChildren<Renderer>();
        Vector3 fc = actor.position; float fw = foe ? 5f : 6f;
        if (rs.Length > 0)
        {
            var b = rs[0].bounds;
            foreach (var rn in rs)
            {
                b.Encapsulate(rn.bounds);
                var mm = rn.sharedMaterial;
                if (mm != null && mm.HasProperty("_AtmDepth") && mm.GetFloat("_AtmDepth") > 0.35f) mm.SetFloat("_AtmDepth", 0.35f);
            }
            fc = b.center;
            fw = Mathf.Clamp(Mathf.Max(b.size.x, b.size.z) * 2.0f, 3f, 7f);
        }
        ring.transform.localScale = new Vector3(fw, fw, 1f);
        ring.transform.position = new Vector3(fc.x, 0.05f, fc.z);
    }
}
