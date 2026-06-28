using UnityEngine;
using System.Collections;
using System.Collections.Generic;
using System.IO;

/// <summary>
/// GridPathController — WorldOS Unity spike (2026-06-23).
///
/// Click-to-move A* pathing on the tavern SceneGrid.
/// Cell↔world transform mirrors ClosedLoopBuilder exactly:
///   CellX(col) = OriginX + (col + 0.5) * CELL_SIZE
///   CellZ(row) = OriginZ + (ROWS - row - 0.5) * CELL_SIZE
///   where OriginX = -(COLS * CELL_SIZE) / 2f,  OriginZ = 0f
///
/// Usage (Play mode):
///   Left-click anywhere on the floor → hero walks there via A*.
///   Blocked/unreachable cell → no-op.
///
/// Drop this MonoBehaviour onto any persistent GameObject (e.g. "Main Camera"
/// or a new empty "PathManager"). Set the Hero field in Inspector, or leave
/// blank for auto-detect ("HeroFighter" by name).
/// </summary>
public class GridPathController : MonoBehaviour
{
    // ── INSPECTOR FIELDS ─────────────────────────────────────────────────────
    [Header("Scene References")]
    [Tooltip("The hero to move. Auto-detected from name 'HeroFighter' if blank.")]
    public Transform Hero;

    [Header("Grid Config (mirrors ClosedLoopBuilder)")]
    public int   Cols     = 15;
    public int   Rows     = 12;
    public float CellSize = 5f;

    [Header("Movement")]
    [Tooltip("Walk speed in world units per second.")]
    public float WalkSpeed = 12f;
    [Tooltip("Rotation speed (degrees/sec) while turning to face movement dir.")]
    public float TurnSpeed = 360f;
    [Tooltip("Floor plane Y for grounding.")]
    public float FloorY = 0f;

    [Header("Animator Params (must match clips)")]
    public string AnimBoolWalk = "IsWalking";

    [Header("Fixture path (auto-loaded)")]
    public string FixturePath = "/home/unity/worldos-unity/fixtures/tavern.scenegrid.json";

    // ── RUNTIME STATE ─────────────────────────────────────────────────────────
    bool[,] _blocked;   // [col, row] — true = blocked
    bool    _gridLoaded;

    Animator _anim;
    bool     _moving;

    // Current cell (for spawn detection) — dungeon fixture party spawn: [6,9]
    int _heroCol = 6, _heroRow = 9;  // fixture party spawn default

    // ── PUBLIC API (for test driver) ──────────────────────────────────────────
    public bool IsMoving => _moving;
    public int  HeroCol  => _heroCol;
    public int  HeroRow  => _heroRow;

    /// <summary>Programmatic move — same as clicking a cell. Safe to call in Play mode.</summary>
    public void MoveToCell(int col, int row)
    {
        if (!_gridLoaded) { Debug.LogWarning("[GridPath] Grid not loaded yet."); return; }
        if (_moving)      { Debug.LogWarning("[GridPath] Already moving, ignoring MoveToCell."); return; }

        int targetCol = col, targetRow = row;
        if (targetCol < 0 || targetCol >= Cols || targetRow < 0 || targetRow >= Rows)
        { Debug.LogWarning("[GridPath] MoveToCell: out of bounds ["+col+","+row+"]"); return; }

        if (_blocked[targetCol, targetRow])
        {
            if (!FindNearestWalkable(targetCol, targetRow, out targetCol, out targetRow))
            { Debug.LogWarning("[GridPath] No walkable cell near ["+col+","+row+"]"); return; }
        }

        var path = AStar(_heroCol, _heroRow, targetCol, targetRow);
        if (path == null || path.Count == 0)
        { Debug.LogWarning("[GridPath] No path to ["+targetCol+","+targetRow+"]"); return; }

        Debug.Log("[GridPath] MoveToCell ["+col+","+row+"] — path "+path.Count+" cells.");
        StartCoroutine(WalkPath(path));
    }

    // ── CELL↔WORLD TRANSFORM (exact mirror of ClosedLoopBuilder) ─────────────
    float OriginX => -(Cols * CellSize) / 2f;   // = -35 for 14×5
    const float OriginZ = 0f;

    Vector3 CellToWorld(int col, int row)
    {
        float x = OriginX + (col + 0.5f) * CellSize;
        float z = OriginZ  + (Rows - row - 0.5f) * CellSize;
        return new Vector3(x, FloorY, z);
    }

    bool WorldToCell(Vector3 world, out int col, out int row)
    {
        // Inverse of CellToWorld
        float fx = (world.x - OriginX) / CellSize - 0.5f;
        float fz = Rows - (world.z - OriginZ) / CellSize - 0.5f;
        col = Mathf.RoundToInt(fx);
        row = Mathf.RoundToInt(fz);
        return (col >= 0 && col < Cols && row >= 0 && row < Rows);
    }

    // ── UNITY LIFECYCLE ───────────────────────────────────────────────────────
    void Start()
    {
        LoadGrid();
        FindHero();
    }

    void Update()
    {
        if (!_gridLoaded) return;
        if (_moving)      return;   // ignore clicks while walking

        // If there's an active CombatSurfaceClient in the scene, it is responsible for handling clicks/movement authoritative-sync.
        var csc = FindObjectOfType<CombatSurfaceClient>();
        if (csc != null && csc.enabled) return;

        if (Input.GetMouseButtonDown(0))
            HandleClick();
    }

    // ── GRID LOAD ─────────────────────────────────────────────────────────────
    void LoadGrid()
    {
        _blocked = new bool[Cols, Rows];

        string actualPath = FixturePath;
        if (!File.Exists(actualPath))
        {
            // Try relative path from project root
            string projectRoot = Path.GetDirectoryName(Application.dataPath);
            string relativePath = Path.Combine(projectRoot, "fixtures", "tavern.scenegrid.json");
            if (File.Exists(relativePath))
            {
                actualPath = relativePath;
            }
        }

        if (!File.Exists(actualPath))
        {
            Debug.LogError("[GridPath] Fixture not found: " + FixturePath + " — using prop-only blocking.");
            _gridLoaded = true;
            return;
        }

        string json = File.ReadAllText(actualPath);

        // Simple hand-parse: look for "walkable":false in each cell entry.
        // We rely on the pattern "c":N,"r":M,"type":"...","walkable":false
        // and "cells" prop entries to mark blocked.
        // Primary: parse "cells" array entries with walkable:false
        // Fallback: mark prop cells from "props"

        // Parse the full JSON via MiniJson if available, else regex-free manual parse.
        // We use a simple state-machine to extract c,r,walkable triples.
        ParseCellsFromJson(json);
        _gridLoaded = true;
        int blocked = 0;
        for (int c = 0; c < Cols; c++)
            for (int r = 0; r < Rows; r++)
                if (_blocked[c, r]) blocked++;
        Debug.Log("[GridPath] Grid loaded " + Cols + "x" + Rows +
                  " — " + blocked + " blocked cells.");
    }

    void ParseCellsFromJson(string json)
    {
        // Walk the "cells" array: each entry is {"c":N,"r":M,"type":"...","walkable":false,...}
        // We scan for entries containing "walkable":false (or walkable:false without space).
        int idx = json.IndexOf("\"cells\"");
        if (idx < 0) { Debug.LogWarning("[GridPath] No 'cells' key in fixture."); return; }
        int arrStart = json.IndexOf('[', idx);
        int arrEnd   = json.IndexOf(']', arrStart);
        if (arrStart < 0 || arrEnd < 0) return;

        string cellsBlock = json.Substring(arrStart, arrEnd - arrStart + 1);

        // Split by object boundaries: find each {...} within the array
        int pos = 0;
        while (pos < cellsBlock.Length)
        {
            int ob = cellsBlock.IndexOf('{', pos);
            if (ob < 0) break;
            int cb = cellsBlock.IndexOf('}', ob);
            if (cb < 0) break;
            string entry = cellsBlock.Substring(ob, cb - ob + 1);
            pos = cb + 1;

            // Check walkable — handle both "walkable":false and "walkable": false (with space)
            bool walkable = !entry.Contains("\"walkable\":false") && !entry.Contains("\"walkable\": false");
            if (walkable) continue;  // walkable=true is the default; skip

            int c = ExtractInt(entry, "\"c\":");
            int r = ExtractInt(entry, "\"r\":");
            if (c >= 0 && c < Cols && r >= 0 && r < Rows)
                _blocked[c, r] = true;
        }
    }

    int ExtractInt(string s, string key)
    {
        int ki = s.IndexOf(key);
        if (ki < 0) return -1;
        int vi = ki + key.Length;
        while (vi < s.Length && (s[vi] == ' ')) vi++;
        int end = vi;
        while (end < s.Length && (char.IsDigit(s[end]) || s[end] == '-')) end++;
        if (end == vi) return -1;
        return int.Parse(s.Substring(vi, end - vi));
    }

    // ── HERO FIND ─────────────────────────────────────────────────────────────
    void FindHero()
    {
        if (Hero == null)
        {
            var go = GameObject.Find("HeroFighter");
            if (go != null) Hero = go.transform;
        }
        if (Hero == null)
        {
            Debug.LogError("[GridPath] HeroFighter not found in scene! Drag it into Inspector.");
            return;
        }
        _anim = Hero.GetComponentInChildren<Animator>();
        if (_anim == null)
            Debug.LogWarning("[GridPath] No Animator found on HeroFighter or children.");

        // Snap to nearest grid cell
        WorldToCell(Hero.position, out _heroCol, out _heroRow);
        Debug.Log("[GridPath] Hero found at cell [" + _heroCol + "," + _heroRow +
                  "] world=" + Hero.position.ToString("F1"));
    }

    // ── MOUSE CLICK HANDLER ───────────────────────────────────────────────────
    void HandleClick()
    {
        var cam = Camera.main;
        if (cam == null) return;

        // Raycast to the floor plane (Y = FloorY)
        Ray ray = cam.ScreenPointToRay(Input.mousePosition);
        // Intersect with y = FloorY plane
        if (Mathf.Abs(ray.direction.y) < 0.0001f) return;
        float t = (FloorY - ray.origin.y) / ray.direction.y;
        if (t < 0) return;
        Vector3 hitWorld = ray.origin + ray.direction * t;

        if (!WorldToCell(hitWorld, out int targetCol, out int targetRow))
        {
            Debug.Log("[GridPath] Click out of grid bounds.");
            return;
        }

        if (_blocked[targetCol, targetRow])
        {
            Debug.Log("[GridPath] Click on blocked cell [" + targetCol + "," + targetRow + "] — finding nearest walkable.");
            if (!FindNearestWalkable(targetCol, targetRow, out targetCol, out targetRow))
            {
                Debug.Log("[GridPath] No walkable neighbor found.");
                return;
            }
        }

        List<Vector2Int> path = AStar(_heroCol, _heroRow, targetCol, targetRow);
        if (path == null || path.Count == 0)
        {
            Debug.Log("[GridPath] No path to [" + targetCol + "," + targetRow + "].");
            return;
        }

        Debug.Log("[GridPath] Path found: " + path.Count + " cells → target [" + targetCol + "," + targetRow + "]");
        StartCoroutine(WalkPath(path));
    }

    bool FindNearestWalkable(int col, int row, out int outCol, out int outRow)
    {
        // BFS outward from the blocked cell
        var visited = new HashSet<Vector2Int>();
        var queue = new Queue<Vector2Int>();
        queue.Enqueue(new Vector2Int(col, row));
        visited.Add(new Vector2Int(col, row));
        int[] dc = {-1,1,0,0};
        int[] dr = {0,0,-1,1};
        while (queue.Count > 0)
        {
            var cur = queue.Dequeue();
            for (int d = 0; d < 4; d++)
            {
                int nc = cur.x + dc[d], nr = cur.y + dr[d];
                var nv = new Vector2Int(nc, nr);
                if (nc < 0 || nc >= Cols || nr < 0 || nr >= Rows) continue;
                if (visited.Contains(nv)) continue;
                if (!_blocked[nc, nr]) { outCol = nc; outRow = nr; return true; }
                visited.Add(nv);
                queue.Enqueue(nv);
            }
        }
        outCol = col; outRow = row; return false;
    }

    // ── A* PATHFINDER ─────────────────────────────────────────────────────────
    List<Vector2Int> AStar(int startC, int startR, int goalC, int goalR)
    {
        var open   = new SortedList<float, Vector2Int>();
        var gScore = new Dictionary<Vector2Int, float>();
        var parent = new Dictionary<Vector2Int, Vector2Int>();
        var closed = new HashSet<Vector2Int>();

        var start = new Vector2Int(startC, startR);
        var goal  = new Vector2Int(goalC, goalR);

        gScore[start] = 0;
        float h0 = Heuristic(start, goal);
        // SortedList doesn't allow duplicate keys; append small epsilon
        float fStart = h0;
        while (open.ContainsKey(fStart)) fStart += 0.0001f;
        open.Add(fStart, start);

        int[] dc4 = {-1,1,0,0};
        int[] dr4 = {0,0,-1,1};

        while (open.Count > 0)
        {
            // Pop lowest f
            var first = open.Keys[0];
            var cur = open.Values[0];
            open.RemoveAt(0);

            if (cur == goal)
                return ReconstructPath(parent, start, goal);

            if (closed.Contains(cur)) continue;
            closed.Add(cur);

            for (int d = 0; d < 4; d++)
            {
                int nc = cur.x + dc4[d], nr = cur.y + dr4[d];
                if (nc < 0 || nc >= Cols || nr < 0 || nr >= Rows) continue;
                var nb = new Vector2Int(nc, nr);
                if (closed.Contains(nb)) continue;
                if (_blocked[nc, nr]) continue;

                float ng = (gScore.ContainsKey(cur) ? gScore[cur] : float.MaxValue) + 1f;
                if (!gScore.ContainsKey(nb) || ng < gScore[nb])
                {
                    gScore[nb] = ng;
                    parent[nb] = cur;
                    float fVal = ng + Heuristic(nb, goal);
                    while (open.ContainsKey(fVal)) fVal += 0.0001f;
                    open.Add(fVal, nb);
                }
            }
        }
        return null;  // no path
    }

    float Heuristic(Vector2Int a, Vector2Int b)
        => Mathf.Abs(a.x - b.x) + Mathf.Abs(a.y - b.y);

    List<Vector2Int> ReconstructPath(Dictionary<Vector2Int, Vector2Int> parent,
                                     Vector2Int start, Vector2Int goal)
    {
        var path = new List<Vector2Int>();
        var cur = goal;
        while (cur != start)
        {
            path.Add(cur);
            cur = parent[cur];
        }
        path.Add(start);
        path.Reverse();
        return path;
    }

    // ── WALK COROUTINE ────────────────────────────────────────────────────────
    IEnumerator WalkPath(List<Vector2Int> path)
    {
        _moving = true;
        SetWalking(true);

        float heroY = Hero.position.y;   // preserve original Y throughout (rig is above floor)

        // Skip the first cell (hero's current cell)
        for (int i = 1; i < path.Count; i++)
        {
            var cell = path[i];
            // Target on the XZ plane only; hero Y stays fixed (rig height above floor)
            Vector3 targetXZ = CellToWorld(cell.x, cell.y);
            targetXZ.y = heroY;

            // Face direction (XZ only)
            Vector3 dir = targetXZ - Hero.position;
            dir.y = 0;

            // Walk until we reach the target (XZ distance only)
            float xzDist = new Vector2(Hero.position.x - targetXZ.x,
                                       Hero.position.z - targetXZ.z).magnitude;
            while (xzDist > 0.08f)
            {
                // Smooth turn toward movement direction
                if (dir.sqrMagnitude > 0.001f)
                {
                    Quaternion targetRot = Quaternion.LookRotation(dir, Vector3.up);
                    Hero.rotation = Quaternion.RotateTowards(Hero.rotation, targetRot,
                                                             TurnSpeed * Time.deltaTime);
                }
                // Move with fixed Y
                var curXZ = new Vector3(Hero.position.x, heroY, Hero.position.z);
                var newPos = Vector3.MoveTowards(curXZ, targetXZ, WalkSpeed * Time.deltaTime);
                Hero.position = newPos;

                // Update direction and distance
                dir = targetXZ - Hero.position; dir.y = 0;
                xzDist = new Vector2(Hero.position.x - targetXZ.x,
                                     Hero.position.z - targetXZ.z).magnitude;
                yield return null;
            }

            // Snap to cell centre (keep Y)
            Hero.position = new Vector3(targetXZ.x, heroY, targetXZ.z);
            _heroCol = cell.x;
            _heroRow = cell.y;
        }

        SetWalking(false);
        _moving = false;
        Debug.Log("[GridPath] Arrived at cell [" + _heroCol + "," + _heroRow + "].");
    }

    void SetWalking(bool walking)
    {
        if (_anim == null) return;
        // Try bool parameter first, then try triggers
        if (_anim.parameters.Length > 0)
        {
            foreach (var p in _anim.parameters)
            {
                if (p.name == AnimBoolWalk && p.type == AnimatorControllerParameterType.Bool)
                {
                    _anim.SetBool(AnimBoolWalk, walking);
                    return;
                }
                if (p.name == AnimBoolWalk && p.type == AnimatorControllerParameterType.Trigger)
                {
                    if (walking) _anim.SetTrigger(AnimBoolWalk);
                    return;
                }
            }
        }
        // No matching param — log once
        if (walking)
            Debug.Log("[GridPath] Animator has no '" + AnimBoolWalk + "' param — walk anim not driven.");
    }

    // ── DEBUG GIZMOS ─────────────────────────────────────────────────────────
    void OnDrawGizmos()
    {
        if (!_gridLoaded) return;
        for (int c = 0; c < Cols; c++)
            for (int r = 0; r < Rows; r++)
            {
                Vector3 ctr = CellToWorld(c, r);
                ctr.y = FloorY + 0.05f;
                Gizmos.color = _blocked[c, r]
                    ? new Color(1f, 0.2f, 0.2f, 0.35f)
                    : new Color(0.2f, 1f, 0.4f, 0.18f);
                Gizmos.DrawCube(ctr, new Vector3(CellSize * 0.9f, 0.05f, CellSize * 0.9f));
            }
    }
}
