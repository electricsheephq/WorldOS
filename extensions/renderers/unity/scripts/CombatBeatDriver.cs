using UnityEngine;
using System.Collections;

/// <summary>
/// CombatBeatDriver — WorldOS Unity spike (2026-06-24).
///
/// Plays a scripted 3-phase combat beat entirely as PRESENTATION:
///   Phase 1 — Hero paths (A*) to a cell adjacent to the goblin (walk-animated).
///   Phase 2 — Hero faces goblin, plays Attack animation (doAttack trigger on HeroAnim_CL).
///   Phase 3 — Goblin reacts: plays its own Attack clip as a "hit-flinch" (doAttack trigger on
///              GoblinAnim_CL), then a brief tilt/knockback impulse, then goblin topples.
/// Optional second strike: hero attacks again → goblin death-topple (scale-squish fade).
///
/// Captures a hi-res screenshot at: approach mid-walk, attack-pose, goblin-react.
///
/// Attach to any persistent GO in the scene (e.g. PathManager).
/// Requires GridPathController on the same or another GO.
/// </summary>
public class CombatBeatDriver : MonoBehaviour
{
    [Header("References (auto-detected if blank)")]
    public Transform Hero;
    public Transform Goblin;
    public GridPathController GPC;

    [Header("Beat Config")]
    [Tooltip("Hero stops at this many cells away from goblin (adjacent).")]
    public float StopDistance = 1.5f;   // grid cells

    [Tooltip("Wait after arrival before attack.")]
    public float PreAttackPause = 0.4f;

    [Tooltip("Duration of the attack animation before goblin reacts.")]
    public float AttackAnimDuration = 0.8f;

    [Tooltip("Duration of goblin's flinch reaction.")]
    public float FlinchDuration = 0.7f;

    [Tooltip("How far goblin knocks back during flinch (world units).")]
    public float KnockbackDist = 1.2f;

    [Tooltip("Enable second hero attack + goblin death topple.")]
    public bool DoSecondAttack = true;

    [Tooltip("Duration between first and second attack.")]
    public float SecondAttackDelay = 0.6f;

    [Header("Capture")]
    public string CaptureFolder = "Captures";
    public string CapturePrefix  = "combat_fix_";
    public int    SuperSize       = 6;   // 6× game-view resolution (~3840px at 640 base)

    [Header("Auto-run")]
    public bool AutoRun = true;
    public float StartDelay = 1.2f;

    // ── private state ─────────────────────────────────────────────────────────
    Animator _heroAnim;
    Animator _goblinAnim;
    Vector3  _goblinStartPos;
    Quaternion _goblinStartRot;
    int      _captureIdx = 1;

    // ── Grid config (mirrors GridPathController) ──────────────────────────────
    const int   COLS      = 15;
    const int   ROWS      = 12;
    const float CELL_SIZE = 5f;
    float OriginX => -(COLS * CELL_SIZE) / 2f;

    Vector3 CellToWorld(int col, int row)
    {
        float x = OriginX + (col + 0.5f) * CELL_SIZE;
        float z = (ROWS - row - 0.5f) * CELL_SIZE;
        return new Vector3(x, 0f, z);
    }

    bool WorldToCell(Vector3 w, out int col, out int row)
    {
        float fx = (w.x - OriginX) / CELL_SIZE - 0.5f;
        float fz = ROWS - w.z / CELL_SIZE - 0.5f;
        col = Mathf.RoundToInt(fx);
        row = Mathf.RoundToInt(fz);
        return col >= 0 && col < COLS && row >= 0 && row < ROWS;
    }

    // ── Unity lifecycle ───────────────────────────────────────────────────────
    void Start()
    {
        Resolve();
        if (AutoRun && _heroAnim != null && _goblinAnim != null && GPC != null)
            StartCoroutine(RunBeat());
        else
            Debug.LogWarning("[CombatBeat] Missing refs — hero=" + (_heroAnim!=null) +
                             " goblin=" + (_goblinAnim!=null) + " GPC=" + (GPC!=null));
    }

    void Resolve()
    {
        if (Hero == null)
        {
            var go = GameObject.Find("HeroFighter");
            if (go != null) Hero = go.transform;
        }
        if (Goblin == null)
        {
            var go = GameObject.Find("MonsterGoblin");
            if (go != null) Goblin = go.transform;
        }
        if (GPC == null)
        {
            GPC = GetComponent<GridPathController>();
            if (GPC == null) GPC = FindObjectOfType<GridPathController>();
        }

        if (Hero   != null) _heroAnim   = Hero.GetComponentInChildren<Animator>(true);
        if (Goblin != null) _goblinAnim = Goblin.GetComponentInChildren<Animator>(true);

        if (_heroAnim   == null && Hero   != null) Debug.LogWarning("[CombatBeat] No Animator on Hero!");
        if (_goblinAnim == null && Goblin != null) Debug.LogWarning("[CombatBeat] No Animator on Goblin!");

        if (Goblin != null)
        {
            _goblinStartPos = Goblin.position;
            _goblinStartRot = Goblin.rotation;
        }
    }

    // ── MAIN BEAT COROUTINE ───────────────────────────────────────────────────
    IEnumerator RunBeat()
    {
        Debug.Log("[CombatBeat] Beat starting in " + StartDelay + "s…");
        yield return new WaitForSeconds(StartDelay);

        // ── Phase 0: compute adjacent cell to goblin ──────────────────────────
        WorldToCell(Goblin.position, out int goblinCol, out int goblinRow);
        // Hero approaches from the side (offset column) so both characters are
        // clearly visible as two distinct combatants with a visible gap.
        // Goblin is at [8,4] (center floor). Hero paths to [9,5] (1 col right,
        // 1 row south) — diagonal adjacent, both fully visible, no overlap.
        int targetCol = Mathf.Clamp(goblinCol + 1, 1, COLS - 2);  // 1 col to the right
        int targetRow = Mathf.Clamp(goblinRow + 1, 1, ROWS - 2);  // 1 row south

        Debug.Log("[CombatBeat] Goblin cell=[" + goblinCol + "," + goblinRow +
                  "] Hero target=[" + targetCol + "," + targetRow + "]");

        // ── Phase 1: HERO APPROACHES ──────────────────────────────────────────
        Debug.Log("[CombatBeat] Phase 1: Hero approaching.");
        GPC.MoveToCell(targetCol, targetRow);

        // Wait a frame so _moving flips
        yield return null; yield return null; yield return null;

        // Mid-walk capture (a few seconds in)
        float walkTimer = 0f;
        bool capturedMidWalk = false;
        while (GPC.IsMoving)
        {
            walkTimer += Time.deltaTime;
            if (!capturedMidWalk && walkTimer > 1.2f)
            {
                Capture("mid_walk");
                capturedMidWalk = true;
            }
            yield return null;
        }
        if (!capturedMidWalk) Capture("mid_walk");

        // ── Phase 2: HERO ATTACKS ─────────────────────────────────────────────
        Debug.Log("[CombatBeat] Phase 2: Attack.");
        yield return new WaitForSeconds(PreAttackPause);

        // Face goblin
        FaceTarget(Hero, Goblin.position);
        // Have goblin face hero so both look at each other
        FaceTarget(Goblin, Hero.position);

        // Trigger hero attack animation
        _heroAnim.SetTrigger("doAttack");
        _heroAnim.SetBool("IsWalking", false);

        yield return new WaitForSeconds(AttackAnimDuration * 0.4f);  // capture at swing
        Capture("hero_attack");

        yield return new WaitForSeconds(AttackAnimDuration * 0.6f);  // finish swing

        // ── Phase 3: GOBLIN REACTS (flinch) ──────────────────────────────────
        Debug.Log("[CombatBeat] Phase 3: Goblin flinches.");
        // Trigger goblin's attack clip as flinch + apply knockback tween
        _goblinAnim.SetTrigger("doAttack");
        StartCoroutine(GoblinKnockback(FlinchDuration, KnockbackDist));

        yield return new WaitForSeconds(FlinchDuration * 0.5f);
        Capture("goblin_react");

        yield return new WaitForSeconds(FlinchDuration * 0.5f);

        // ── Optional Phase 4: SECOND ATTACK + DEATH ──────────────────────────
        if (DoSecondAttack)
        {
            Debug.Log("[CombatBeat] Phase 4: Second strike.");
            yield return new WaitForSeconds(SecondAttackDelay);
            FaceTarget(Hero, Goblin.position);
            _heroAnim.SetTrigger("doAttack");
            yield return new WaitForSeconds(AttackAnimDuration * 0.5f);
            StartCoroutine(GoblinDeath(0.9f));
            yield return new WaitForSeconds(AttackAnimDuration * 0.5f);
            yield return new WaitForSeconds(0.3f);
            Capture("goblin_death");
        }

        Debug.Log("[CombatBeat] BEAT COMPLETE. Captures saved to " + CaptureFolder + "/");
    }

    // ── GOBLIN KNOCKBACK COROUTINE ────────────────────────────────────────────
    IEnumerator GoblinKnockback(float duration, float dist)
    {
        // Direction away from hero (XZ only)
        Vector3 dir = Goblin.position - Hero.position;
        dir.y = 0f;
        if (dir.sqrMagnitude < 0.001f) dir = Vector3.back;
        dir = dir.normalized;

        Vector3 startPos = Goblin.position;
        Vector3 endPos   = startPos + dir * dist;
        endPos.y = startPos.y;

        // Also add a tilt (rotate goblin around Z axis briefly, then back)
        Quaternion startRot = Goblin.rotation;
        Quaternion tiltRot  = startRot * Quaternion.Euler(0f, 0f, 25f);

        float elapsed = 0f;
        while (elapsed < duration)
        {
            float t = elapsed / duration;
            // Knockback: quick forward then settle
            float posT = Mathf.SmoothStep(0f, 1f, Mathf.Clamp01(t * 2f));
            Goblin.position = Vector3.Lerp(startPos, endPos, posT);

            // Tilt: spike at t=0.2 then revert
            float tiltT = Mathf.Clamp01(Mathf.PingPong(t * 3f, 1f));
            Goblin.rotation = Quaternion.Slerp(startRot, tiltRot, tiltT);

            elapsed += Time.deltaTime;
            yield return null;
        }
        Goblin.position = endPos;
        Goblin.rotation = startRot;
    }

    // ── GOBLIN DEATH TOPPLE ───────────────────────────────────────────────────
    IEnumerator GoblinDeath(float duration)
    {
        Vector3    startPos   = Goblin.position;
        Quaternion startRot   = Goblin.rotation;
        // Topple: rotate 90° forward (pitch), drift down slightly
        Vector3 toppleAxis = Vector3.Cross(Vector3.up, (Goblin.position - Hero.position).normalized);
        if (toppleAxis.sqrMagnitude < 0.001f) toppleAxis = Vector3.right;
        Quaternion deathRot = startRot * Quaternion.AngleAxis(90f, toppleAxis);

        // Also shrink on Y slightly (squish)
        Vector3 startScale = Goblin.localScale;
        Vector3 deadScale  = new Vector3(startScale.x, startScale.y * 0.1f, startScale.z);

        float elapsed = 0f;
        while (elapsed < duration)
        {
            float t = Mathf.SmoothStep(0f, 1f, elapsed / duration);
            Goblin.rotation   = Quaternion.Slerp(startRot, deathRot, t);
            Goblin.localScale = Vector3.Lerp(startScale, deadScale, t);
            Goblin.position   = Vector3.Lerp(startPos,
                                             startPos + Vector3.down * 0.5f, t);
            elapsed += Time.deltaTime;
            yield return null;
        }
        Goblin.rotation   = deathRot;
        Goblin.localScale = deadScale;

        // Fade out the mesh
        StartCoroutine(FadeOut(Goblin, 0.4f));
    }

    IEnumerator FadeOut(Transform root, float duration)
    {
        var renderers = root.GetComponentsInChildren<Renderer>(true);
        // Cache original colors / enable alpha
        float elapsed = 0f;
        while (elapsed < duration)
        {
            float alpha = 1f - elapsed / duration;
            foreach (var r in renderers)
            {
                foreach (var mat in r.materials)
                {
                    if (mat.HasProperty("_Color"))
                    {
                        var c = mat.color; c.a = alpha; mat.color = c;
                    }
                    else if (mat.HasProperty("_BaseColor"))
                    {
                        var c = mat.GetColor("_BaseColor"); c.a = alpha;
                        mat.SetColor("_BaseColor", c);
                    }
                }
            }
            elapsed += Time.deltaTime;
            yield return null;
        }
        root.gameObject.SetActive(false);
    }

    // ── HELPERS ───────────────────────────────────────────────────────────────
    void FaceTarget(Transform self, Vector3 target)
    {
        Vector3 dir = target - self.position;
        dir.y = 0f;
        if (dir.sqrMagnitude > 0.001f)
            self.rotation = Quaternion.LookRotation(dir, Vector3.up);
    }

    void Capture(string label)
    {
        // Ensure capture folder exists
        if (!System.IO.Directory.Exists(CaptureFolder))
            System.IO.Directory.CreateDirectory(CaptureFolder);

        string path = CaptureFolder + "/" + CapturePrefix + _captureIdx.ToString("D2")
                      + "_" + label + ".png";
        ScreenCapture.CaptureScreenshot(path, SuperSize);
        Debug.Log("[CombatBeat] Captured: " + path);
        _captureIdx++;
    }
}
