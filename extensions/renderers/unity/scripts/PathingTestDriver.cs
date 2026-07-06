using UnityEngine;
using System.Collections;
using System.Collections.Generic;

/// <summary>
/// PathingTestDriver — WorldOS Unity spike (2026-06-23).
///
/// Programmatically exercises GridPathController to prove click-to-move pathing
/// without requiring mouse input. Issues 4 sequential move commands that
/// exercise the grid and route around blocked cells.
///
/// Attach to any persistent GO in the scene (or "PathManager").
/// Starts automatically in Play mode via Start().
/// </summary>
public class PathingTestDriver : MonoBehaviour
{
    [Header("Test sequence — cell targets [col, row]")]
    public bool AutoRun = true;
    public float DelayBetweenMoves = 0.5f;
    public string CaptureBaseName = "Captures/dungeon_path_";

    GridPathController _gpc;

    // DUNGEON pathing test moves (15×12 grid, hero starts at [6,9]):
    // Blocked cells of note:
    //   Perimeter walls: row 0, row 11, col 0, col 14 (all blocked)
    //   Pillars: [2,2] and [12,2]    Rubble: [2,6]
    //   Sarcophagus: [6,1] and [7,1]  Braziers: [4,1] and [9,1]
    //
    // 1. [6,9]  → [7,6]  : walk toward center (open floor, proves basic move)
    // 2. [7,6]  → [11,6] : walk right across dungeon (avoids rubble [2,6])
    // 3. [11,6] → [11,3] : walk toward N-E pillar at [12,2], A* must route clear
    // 4. [11,3] → [3,3]  : long cross-dungeon walk — routes around both pillars
    //                       ([2,2] and [12,2]), sarcophagus [6,1][7,1], braziers
    static readonly (int c, int r)[] TestTargets = {
        (7,  6),   // center floor (open)
        (11, 6),   // right side open corridor
        (11, 3),   // near right pillar at [12,2] — A* routes around it
        (3,  3),   // long left-crossing — around both pillars + sarcophagus
    };

    void Start()
    {
        _gpc = GetComponent<GridPathController>();
        if (_gpc == null) _gpc = FindObjectOfType<GridPathController>();
        if (_gpc == null) { Debug.LogError("[TestDriver] No GridPathController in scene!"); return; }

        if (AutoRun)
            StartCoroutine(RunTestSequence());
    }

    IEnumerator RunTestSequence()
    {
        Debug.Log("[TestDriver] Starting DUNGEON pathing test sequence — " + TestTargets.Length + " moves.");
        yield return new WaitForSeconds(0.8f); // let Start() settle

        int captureIdx = 1;

        for (int i = 0; i < TestTargets.Length; i++)
        {
            var (tc, tr) = TestTargets[i];
            Debug.Log("[TestDriver] Move " + (i+1) + "/" + TestTargets.Length +
                      " → target cell [" + tc + "," + tr + "]");

            // Issue the move by calling GridPathController's public API
            _gpc.MoveToCell(tc, tr);

            // Wait 3 frames so the WalkPath coroutine has time to start + set _moving=true
            yield return null;
            yield return null;
            yield return null;

            // Wait until movement completes (or timeout)
            float timeout = 60f;
            float elapsed = 0f;
            while (_gpc.IsMoving && elapsed < timeout)
            {
                elapsed += Time.deltaTime;
                yield return null;
            }
            // Additional wait if not moving (coroutine may have completed very fast or not started)
            if (!_gpc.IsMoving && elapsed < 1f)
                yield return new WaitForSeconds(0.5f);  // give time for async move

            if (elapsed >= timeout)
                Debug.LogWarning("[TestDriver] Move " + (i+1) + " timed out!");

            // Capture a screenshot at arrival
            yield return new WaitForSeconds(0.2f);
            string capPath = CaptureBaseName + captureIdx.ToString("D2") + ".png";
            CaptureScreenshot(capPath);
            Debug.Log("[TestDriver] Captured: " + capPath);
            captureIdx++;

            yield return new WaitForSeconds(DelayBetweenMoves);
        }

        Debug.Log("[TestDriver] Test sequence COMPLETE. Hero at cell [" +
                  _gpc.HeroCol + "," + _gpc.HeroRow + "].");
    }

    void CaptureScreenshot(string path)
    {
        // Ensure Captures dir exists
        string dir = System.IO.Path.GetDirectoryName(path);
        if (!System.IO.Directory.Exists(dir))
            System.IO.Directory.CreateDirectory(dir);

        // High-res capture: superSize=4 for ~5120px wide (4× the game view)
        ScreenCapture.CaptureScreenshot(path, 4);
    }
}
