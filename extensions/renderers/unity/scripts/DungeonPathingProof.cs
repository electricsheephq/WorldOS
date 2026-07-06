using UnityEngine;
using UnityEditor;
using System.Collections.Generic;
using System.IO;

/// <summary>
/// DungeonPathingProof — WorldOS Unity spike (2026-06-24).
///
/// Editor-mode A* pathfinding proof on the dungeon SceneGrid.
/// No play mode required. Menu: Tools/WorldOS/Prove Dungeon Pathing (A*)
/// Logs the grid size, blocked-cell count, and 4 routed paths that dodge obstacles.
/// </summary>
public static class DungeonPathingProof
{
    const string FIXTURE = "/Volumes/LEXAR/WorldOS-Unity-spike/fixtures/dungeon.scenegrid.json";

    // ── A* INLINE (copy of GridPathController logic, editor-mode) ──────────
    static bool[,] _blocked;
    static int _cols, _rows;
    static float _cell;

    [MenuItem("Tools/WorldOS/Prove Dungeon Pathing (A*)")]
    public static void ProvePathing()
    {
        // 1. Load fixture + build blocked grid
        if (!File.Exists(FIXTURE)) { Debug.LogError("[DungeonProof] Fixture not found: " + FIXTURE); return; }
        string json = File.ReadAllText(FIXTURE);

        // Parse grid dimensions
        var root = MiniJson.Parse(json) as Dictionary<string, object>;
        if (root == null) { Debug.LogError("[DungeonProof] Parse failed."); return; }
        _cols = _rows = 0; _cell = 5f;
        if (root.TryGetValue("grid", out var go) && go is Dictionary<string, object> grid)
        {
            _cols = (int)System.Convert.ToDouble(grid.TryGetValue("cols", out var cv) ? cv : 14);
            _rows = (int)System.Convert.ToDouble(grid.TryGetValue("rows", out var rv) ? rv : 10);
            _cell = (float)System.Convert.ToDouble(grid.TryGetValue("cell_size_ft", out var csz) ? csz : 5.0);
        }

        _blocked = new bool[_cols, _rows];
        int blockedCount = 0;

        // Mark wall + prop cells
        if (root.TryGetValue("cells", out var co) && co is List<object> cellList)
        {
            foreach (var ce in cellList)
            {
                if (ce is Dictionary<string, object> cd)
                {
                    bool walkable = true;
                    if (cd.TryGetValue("walkable", out var wv))
                    {
                        if (wv is bool b) walkable = b;
                        else if (wv != null) bool.TryParse(wv.ToString(), out walkable);
                    }
                    if (!walkable)
                    {
                        int c = (int)System.Convert.ToDouble(cd.TryGetValue("c", out var ccv) ? ccv : 0);
                        int r = (int)System.Convert.ToDouble(cd.TryGetValue("r", out var rr) ? rr : 0);
                        if (c >= 0 && c < _cols && r >= 0 && r < _rows)
                        { _blocked[c, r] = true; blockedCount++; }
                    }
                }
            }
        }

        Debug.Log("[DungeonProof] Grid: " + _cols + "x" + _rows + " (cell " + _cell + "ft) — " +
                  blockedCount + " blocked cells, " + (_cols * _rows - blockedCount) + " walkable.");

        // 2. Four pathing tests that route around blocked cells
        var tests = new (int sc, int sr, int gc, int gr, string label)[]
        {
            (6, 9,  7, 6,  "center walk (south→mid)"),
            (7, 6,  11, 6, "right corridor (cross dungeon)"),
            (11, 6, 11, 3, "north approach near right pillar [12,2]"),
            (11, 3,  3, 3, "long cross — around both pillars + sarcophagus"),
        };

        bool allOk = true;
        foreach (var (sc, sr, gc, gr, label) in tests)
        {
            var path = AStar(sc, sr, gc, gr);
            if (path == null || path.Count == 0)
            {
                Debug.LogError("[DungeonProof] FAIL — no path for '" + label +
                               "' [" + sc + "," + sr + "] → [" + gc + "," + gr + "]");
                allOk = false;
                continue;
            }

            // Verify no blocked cell in path
            bool clean = true;
            foreach (var step in path)
                if (_blocked[step.x, step.y]) { clean = false; break; }

            string pathStr = "";
            foreach (var s in path) pathStr += "[" + s.x + "," + s.y + "]→";
            pathStr = pathStr.TrimEnd('→');

            if (clean)
                Debug.Log("[DungeonProof] OK  '" + label + "' — " + path.Count + " cells: " + pathStr);
            else
            {
                Debug.LogError("[DungeonProof] FAIL  '" + label + "' — path traverses a BLOCKED cell!");
                allOk = false;
            }
        }

        Debug.Log("[DungeonProof] === RESULT: " + (allOk ? "ALL 4 PATHS ROUTED CLEANLY ✓" : "FAILURES DETECTED ✗") + " ===");
    }

    // ── A* (Manhattan heuristic, 4-directional) ───────────────────────────
    static List<Vector2Int> AStar(int startC, int startR, int goalC, int goalR)
    {
        var open   = new SortedList<float, Vector2Int>();
        var gScore = new Dictionary<Vector2Int, float>();
        var parent = new Dictionary<Vector2Int, Vector2Int>();
        var closed = new HashSet<Vector2Int>();
        var start  = new Vector2Int(startC, startR);
        var goal   = new Vector2Int(goalC, goalR);

        gScore[start] = 0;
        float h0 = H(start, goal);
        while (open.ContainsKey(h0)) h0 += 0.0001f;
        open.Add(h0, start);

        int[] dc = {-1,1,0,0};
        int[] dr = {0,0,-1,1};

        while (open.Count > 0)
        {
            var cur = open.Values[0]; open.RemoveAt(0);
            if (cur == goal) return Reconstruct(parent, start, goal);
            if (closed.Contains(cur)) continue;
            closed.Add(cur);

            for (int d = 0; d < 4; d++)
            {
                int nc = cur.x + dc[d], nr = cur.y + dr[d];
                if (nc < 0 || nc >= _cols || nr < 0 || nr >= _rows) continue;
                var nb = new Vector2Int(nc, nr);
                if (closed.Contains(nb) || _blocked[nc, nr]) continue;
                float ng = (gScore.ContainsKey(cur) ? gScore[cur] : float.MaxValue) + 1f;
                if (!gScore.ContainsKey(nb) || ng < gScore[nb])
                {
                    gScore[nb] = ng; parent[nb] = cur;
                    float fv = ng + H(nb, goal);
                    while (open.ContainsKey(fv)) fv += 0.0001f;
                    open.Add(fv, nb);
                }
            }
        }
        return null;
    }

    static float H(Vector2Int a, Vector2Int b) => Mathf.Abs(a.x - b.x) + Mathf.Abs(a.y - b.y);

    static List<Vector2Int> Reconstruct(Dictionary<Vector2Int, Vector2Int> parent,
                                         Vector2Int start, Vector2Int goal)
    {
        var path = new List<Vector2Int>();
        var cur = goal;
        while (cur != start) { path.Add(cur); cur = parent[cur]; }
        path.Add(start); path.Reverse(); return path;
    }
}
