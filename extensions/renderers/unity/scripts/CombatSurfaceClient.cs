using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// CombatSurfaceClient (S2/A4) — renders the ENGINE's combat surface on the painterly tavern.
///
/// READ-ONLY on game state: it GETs build_combat_surface and positions the scene's existing rigged
/// actors (HeroFighter / MonsterGoblin) at the engine's authoritative cells — the engine stays the
/// SOLE WRITER. A click (or the public DoMove/DoAttack, for headless/programmatic driving) POSTs a
/// move_to_cell / attack INTENT to /move; the engine validates + resolves it (movement budget, OA,
/// initiative) and returns the refreshed surface, which we re-render. Cell<->world mirrors
/// ClosedLoopBuilder / GridPathController exactly (14x10, cell 5, row-flipped) so a token lands on
/// the painted floor.
/// </summary>
public class CombatSurfaceClient : MonoBehaviour
{
    [Header("Viewer (reverse-tunnel to the Mac engine)")]
    public string ViewerUrl = "http://127.0.0.1:8765";
    public string CampaignId = "";
    public float PollInterval = 1.5f;

    [Header("Grid — mirror ClosedLoopBuilder (14x10, cell 5)")]
    public int Cols = 14;
    public int Rows = 10;
    public float CellSize = 5f;
    public float FloorY = 0f;

    Transform _hero, _goblin;
    string _turnToken = "";
    string _goblinId = "";
    int _goblinX = -1, _goblinY = -1;
    bool _busy = false;

    [System.Serializable] public class Tok { public string id; public string name; public string team; public int x; public int y; public bool isCurrent; }
    [System.Serializable] public class Surf { public string turnToken; public bool can_act; public Tok[] tokens; }
    [System.Serializable] public class MoveResp { public bool ok; public string reason; public Surf combat; }

    float OriginX { get { return -(Cols * CellSize) / 2f; } }
    const float OriginZ = 0f;
    Vector3 CellToWorld(int c, int r) { return new Vector3(OriginX + (c + 0.5f) * CellSize, FloorY, OriginZ + (Rows - r - 0.5f) * CellSize); }
    bool WorldToCell(Vector3 w, out int c, out int r)
    {
        c = Mathf.RoundToInt((w.x - OriginX) / CellSize - 0.5f);
        r = Mathf.RoundToInt(Rows - (w.z - OriginZ) / CellSize - 0.5f);
        return c >= 0 && c < Cols && r >= 0 && r < Rows;
    }

    void Start()
    {
        // Additive config resolution (#1322 W5a): the standalone player build has no Inspector to
        // hand-edit, so the app host (NSWorkspace launch w/ configuration.environment, mirroring
        // native-bridge.js) hands the engine origin + campaign through the PROCESS ENVIRONMENT.
        // Absent env vars ⇒ today's Inspector-set defaults, byte-identical to pre-#1322 behavior.
        string envUrl = System.Environment.GetEnvironmentVariable("WORLDOS_ENGINE_BASE_URL");
        if (!string.IsNullOrEmpty(envUrl)) ViewerUrl = envUrl;
        string envCampaign = System.Environment.GetEnvironmentVariable("WORLDOS_CAMPAIGN_ID");
        if (!string.IsNullOrEmpty(envCampaign)) CampaignId = envCampaign;

        _hero = Find("HeroFighter");
        _goblin = Find("MonsterGoblin");
        Debug.Log("[CSC] start: hero=" + (_hero != null) + " goblin=" + (_goblin != null) + " campaign=" + CampaignId + " url=" + ViewerUrl);
        StartCoroutine(PollLoop());
    }
    Transform Find(string n) { var go = GameObject.Find(n); return go ? go.transform : null; }

    IEnumerator PollLoop()
    {
        while (true)
        {
            if (!_busy) yield return Fetch();
            yield return new WaitForSeconds(PollInterval);
        }
    }

    static bool Ok(UnityWebRequest r)
    {
#if UNITY_2020_2_OR_NEWER
        return r.result == UnityWebRequest.Result.Success;
#else
        return !r.isNetworkError && !r.isHttpError;
#endif
    }

    IEnumerator Fetch()
    {
        using (var req = UnityWebRequest.Get(ViewerUrl + "/combat-surface?campaign=" + CampaignId))
        {
            req.timeout = 6;
            yield return req.SendWebRequest();
            if (!Ok(req)) { Debug.LogWarning("[CSC] GET failed: " + req.error); yield break; }
            ApplyJson(req.downloadHandler.text);
        }
    }

    void ApplyJson(string json)
    {
        Surf s = null;
        try { s = JsonUtility.FromJson<Surf>(json); }
        catch (System.Exception e) { Debug.LogWarning("[CSC] parse: " + e.Message); return; }
        ApplySurf(s);
    }

    void ApplySurf(Surf s)
    {
        if (s == null || s.tokens == null) return;
        _turnToken = s.turnToken;
        foreach (var t in s.tokens)
        {
            bool foe = (t.team == "foe");
            if (foe) { _goblinId = t.id; _goblinX = t.x; _goblinY = t.y; }
            Transform a = foe ? _goblin : _hero;
            if (a != null)
            {
                var w = CellToWorld(t.x, t.y);
                Vector3 old = a.position;
                a.position = new Vector3(w.x, a.position.y, w.z);  // keep the rig's Y (feet grounded)
                Vector3 d = new Vector3(w.x - old.x, 0f, w.z - old.z);
                MoveShadow(a.name, d);     // contact shadow follows the actor (orphaning fix)
                EnsureRing(a, foe);        // BG/PoE selection ring (gold ally / red enemy)
            }
        }
    }

    // The build-time contact shadow + AO are siblings of the actor; move them by the same delta so
    // they stay under the feet when the engine repositions the actor (else the shadow is orphaned).
    void MoveShadow(string actorName, Vector3 delta)
    {
        foreach (var nm in new[] { actorName + "_Shadow", actorName + "_Shadow_AO" })
        {
            var g = GameObject.Find(nm);
            if (g != null) g.transform.position += delta;
        }
    }

    // Find-or-create a BG/PoE floor selection ring under the actor (gold ally / red enemy), placed at the cell.
    void EnsureRing(Transform actor, bool foe)
    {
        string nm = actor.name + "_Ring";
        var ring = GameObject.Find(nm);
        if (ring == null)
        {
            var sh = Shader.Find("WorldOS/SelectionRing");
            if (sh == null) return;
            var q = GameObject.CreatePrimitive(PrimitiveType.Quad);
            q.name = nm;
            Destroy(q.GetComponent<Collider>());
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

    void Update()
    {
        if (_busy) return;
        if (Input.GetMouseButtonDown(0)) HandleClick();
    }

    void HandleClick()
    {
        var cam = Camera.main; if (cam == null) return;
        Ray ray = cam.ScreenPointToRay(Input.mousePosition);
        if (Mathf.Abs(ray.direction.y) < 1e-4f) return;
        float tt = (FloorY - ray.origin.y) / ray.direction.y; if (tt < 0) return;
        Vector3 hit = ray.origin + ray.direction * tt;
        if (!WorldToCell(hit, out int c, out int r)) return;
        if (c == _goblinX && r == _goblinY && _goblinId.Length > 0) StartCoroutine(PostAttack());
        else StartCoroutine(PostMove(c, r));
    }

    // ---- public, for headless/programmatic driving (the box has no mouse) ----
    public void DoMove(int x, int y) { if (!_busy) StartCoroutine(PostMove(x, y)); }
    public void DoAttack() { if (!_busy && _goblinId.Length > 0) StartCoroutine(PostAttack()); }

    IEnumerator PostMove(int x, int y)
    {
        _busy = true;
        yield return Post("{\"kind\":\"move_to_cell\",\"x\":" + x + ",\"y\":" + y + ",\"turn_token\":\"" + _turnToken + "\",\"campaign\":\"" + CampaignId + "\"}");
        _busy = false;
    }
    IEnumerator PostAttack()
    {
        _busy = true;
        yield return Post("{\"kind\":\"attack\",\"target_id\":\"" + _goblinId + "\",\"turn_token\":\"" + _turnToken + "\",\"campaign\":\"" + CampaignId + "\"}");
        _busy = false;
    }

    IEnumerator Post(string body)
    {
        using (var req = new UnityWebRequest(ViewerUrl + "/move", "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = 8;
            yield return req.SendWebRequest();
            if (!Ok(req)) { Debug.LogWarning("[CSC] /move failed: " + req.error + " body=" + req.downloadHandler.text); yield break; }
            MoveResp resp = null;
            try { resp = JsonUtility.FromJson<MoveResp>(req.downloadHandler.text); }
            catch (System.Exception e) { Debug.LogWarning("[CSC] move parse: " + e.Message); yield break; }
            if (resp != null && resp.ok && resp.combat != null) { Debug.Log("[CSC] move ok -> re-render"); ApplySurf(resp.combat); }
            else Debug.LogWarning("[CSC] move rejected: " + (resp != null ? resp.reason : "null"));
        }
    }
}
