using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// CombatSurfaceClient (S2/A4; W5b #1433 wiring) — the RUNTIME consumer that renders the ENGINE's
/// combat surface on the built painterly scene. It is the player-build analogue of the editor-only
/// paint_combat_v1.cs capture flow: paint_combat_v1 composes + names the actors in the editor and
/// saves the scene, but does NOT run in the standalone player (it's UnityEditor code). This
/// MonoBehaviour is what keeps the shipped scene LIVE — it polls /combat-surface, repositions the
/// already-placed actors at the engine's authoritative cells, and POSTs the player's click as a
/// /move intent. Engine stays the SOLE WRITER; this renderer is a pure consumer (it only animates
/// engine-confirmed paths).
///
/// #1433: actor resolution now matches the CURRENT asset-registry naming — paint_combat_v1 spawns
/// each token as GameObject "Actor_" + token.id (e.g. Actor_char_f50d226067d4), so we resolve per
/// token by that name instead of the stale, pre-registry GameObject.Find("HeroFighter") lookups.
/// Cell<->world mirrors paint_combat_v1's cellToWorld EXACTLY (grid from the surface, cell 2.0,
/// origin (n-1)/2, row-flipped) so a repositioned token lands on the same painted floor cell the
/// editor placed it on — no jump on the first poll.
/// </summary>
public class CombatSurfaceClient : MonoBehaviour
{
    [Header("Viewer (reverse-tunnel to the Mac engine)")]
    public string ViewerUrl = "http://127.0.0.1:8765";
    public string CampaignId = "";
    public float PollInterval = 1.5f;

    [Header("Grid — mirror paint_combat_v1 (14x11, cell 2.0); overridden by the surface `grid` block")]
    public int Cols = 14;
    public int Rows = 11;
    public float CellSize = 2f;
    public float FloorY = 0f;

    string _turnToken = "";
    string _foeId = "";
    int _foeX = -1, _foeY = -1;
    bool _busy = false;

    [System.Serializable] public class Tok { public string id; public string name; public string team; public int x; public int y; public bool isCurrent; }
    [System.Serializable] public class Grid { public int cols; public int rows; }
    [System.Serializable] public class Surf { public string turnToken; public bool can_act; public Grid grid; public Tok[] tokens; }
    [System.Serializable] public class MoveResp { public bool ok; public string reason; public Surf combat; }

    // cellToWorld mirrors paint_combat_v1.cs EXACTLY:
    //   new Vector3((cx-(cols-1)/2)*2.0, 0, ((rows-1)/2-cy)*2.0)
    Vector3 CellToWorld(int c, int r)
    {
        return new Vector3((c - (Cols - 1f) / 2f) * CellSize, FloorY, ((Rows - 1f) / 2f - r) * CellSize);
    }
    bool WorldToCell(Vector3 w, out int c, out int r)
    {
        c = Mathf.RoundToInt(w.x / CellSize + (Cols - 1f) / 2f);
        r = Mathf.RoundToInt((Rows - 1f) / 2f - w.z / CellSize);
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

        Debug.Log("[CSC] start: campaign=" + CampaignId + " url=" + ViewerUrl);
        StartCoroutine(PollLoop());
    }

    // Resolve the token's already-placed actor by the registry naming (Actor_ + token.id).
    Transform FindActor(string id)
    {
        if (string.IsNullOrEmpty(id)) return null;
        var go = GameObject.Find("Actor_" + id);
        return go ? go.transform : null;
    }

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
        // #1318/#1433: honor the surface's own grid extents (rest-mode rooms can be non-14x11) so
        // cellToWorld stays aligned to what paint_combat_v1 baked. Absent ⇒ the 14x11 default.
        if (s.grid != null && s.grid.cols > 0 && s.grid.rows > 0) { Cols = s.grid.cols; Rows = s.grid.rows; }
        foreach (var t in s.tokens)
        {
            bool foe = (t.team == "foe");
            if (foe) { _foeId = t.id; _foeX = t.x; _foeY = t.y; }
            Transform a = FindActor(t.id);
            if (a != null) PlaceActor(a, t.x, t.y);
        }
    }

    // Position the actor so its VISUAL bounds-center lands on the engine cell (mirrors
    // paint_combat_v1's centering), keeping the rig's Y (feet already grounded in the baked scene).
    // The build-time contact shadow / AO / ring siblings (Actor_<id>_AO/_Core/_Ring) follow by the
    // same delta so they stay under the feet when the engine repositions the actor.
    void PlaceActor(Transform a, int cx, int cy)
    {
        Vector3 target = CellToWorld(cx, cy);
        var rs = a.GetComponentsInChildren<Renderer>();
        Vector3 ctr = a.position;
        if (rs.Length > 0) { var b = rs[0].bounds; foreach (var rn in rs) b.Encapsulate(rn.bounds); ctr = b.center; }
        Vector3 old = a.position;
        a.position = new Vector3(a.position.x + (target.x - ctr.x), a.position.y, a.position.z + (target.z - ctr.z));
        Vector3 delta = a.position - old;
        foreach (var suf in new[] { "_AO", "_Core", "_Ring" })
        {
            var g = GameObject.Find(a.name + suf);
            if (g != null) g.transform.position += delta;
        }
    }

    void Update()
    {
        if (_busy) return;
        if (Input.GetMouseButtonDown(0)) HandleClick();
    }

    // Minimal input: raycast the click onto the floor plane -> cell -> POST the existing /move kinds
    // ONLY (move_to_cell, or an on-turn attack when the clicked cell holds the foe). Payload mirrors
    // the viewer driver (qa/drive_gfx_combat.py) exactly.
    void HandleClick()
    {
        var cam = Camera.main; if (cam == null) return;
        Ray ray = cam.ScreenPointToRay(Input.mousePosition);
        if (Mathf.Abs(ray.direction.y) < 1e-4f) return;
        float tt = (FloorY - ray.origin.y) / ray.direction.y; if (tt < 0) return;
        Vector3 hit = ray.origin + ray.direction * tt;
        if (!WorldToCell(hit, out int c, out int r)) return;
        if (c == _foeX && r == _foeY && _foeId.Length > 0) StartCoroutine(PostAttack());
        else StartCoroutine(PostMove(c, r));
    }

    // ---- public, for headless/programmatic driving (the box has no mouse) ----
    public void DoMove(int x, int y) { if (!_busy) StartCoroutine(PostMove(x, y)); }
    public void DoAttack() { if (!_busy && _foeId.Length > 0) StartCoroutine(PostAttack()); }

    IEnumerator PostMove(int x, int y)
    {
        _busy = true;
        yield return Post("{\"kind\":\"move_to_cell\",\"x\":" + x + ",\"y\":" + y + ",\"turn_token\":\"" + _turnToken + "\",\"campaign\":\"" + CampaignId + "\"}");
        _busy = false;
    }
    IEnumerator PostAttack()
    {
        _busy = true;
        yield return Post("{\"kind\":\"attack\",\"target_id\":\"" + _foeId + "\",\"turn_token\":\"" + _turnToken + "\",\"campaign\":\"" + CampaignId + "\"}");
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
