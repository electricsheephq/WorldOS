/*
 * SurfaceClient — the thin-client transport for the M0 spike.
 *
 * CONTRACT ROLE: this is the ONLY thing that talks to the engine. It proves the
 * "renderer is a thin client; engine is sole writer" model:
 *   - READ:  GET the read-model surfaces (/atlas-surface, /combat-surface,
 *            /character-surface). Renderer owns NO game state — it re-fetches.
 *   - WRITE: the renderer's ONLY write is an INTENT via POST /move. It never
 *            mutates state; it asks the engine to.
 *
 * SWAPPABLE TRANSPORT: polls today (~3s). The M3 websocket/SSE upgrade slots in
 * behind this same interface with identical payload shapes — callers don't change.
 *
 * FIXTURE FALLBACK: if no live ?campaign= viewer is reachable, it loads the
 * bundled fixtures so the spike renders standalone. A banner shows which mode.
 */
"use strict";

class SurfaceClient {
  constructor({ baseUrl = "", campaign = "", pollMs = 3000, onMode = () => {} } = {}) {
    this.baseUrl = baseUrl;            // "" = same origin (live viewer); else fixtures
    this.campaign = campaign;
    this.pollMs = pollMs;
    this.onMode = onMode;
    this.mode = "unknown";             // "live" | "fixture"
    this._timer = null;
  }

  _q() { return this.campaign ? `?campaign=${encodeURIComponent(this.campaign)}` : ""; }

  // Try the live surface; on any failure fall back to the bundled fixture.
  async _get(surface) {
    if (this.mode !== "fixture") {
      try {
        const r = await fetch(`${this.baseUrl}/${surface}${this._q()}`, { cache: "no-store" });
        if (r.ok) {
          if (this.mode !== "live") { this.mode = "live"; this.onMode("live"); }
          return await r.json();
        }
      } catch (_e) { /* fall through to fixture */ }
    }
    if (this.mode !== "fixture") { this.mode = "fixture"; this.onMode("fixture"); }
    const fr = await fetch(`./fixtures/${surface}.json`, { cache: "no-store" });
    return await fr.json();
  }

  atlas() { return this._get("atlas-surface"); }
  combat() { return this._get("combat-surface"); }
  character() { return this._get("character-surface"); }

  // The renderer's ONLY write. A constrained INTENT — never a world-assertion.
  // CONTRACT-GAP DEMO: kind:"travel" is NOT yet in the engine's _MOVE_KINDS
  // allowlist (viewer/server.py:84) — a live engine REJECTS it today with
  // "unknown move kind". That rejection is the point: it proves the M0
  // move-vocabulary freeze must land before a graphical client ships.
  async move(intent) {
    if (this.mode === "fixture") {
      console.log("[fixture] would POST /move:", intent);
      return { ok: true, fixture: true };
    }
    try {
      const r = await fetch(`${this.baseUrl}/move`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...intent, campaign: this.campaign }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok || body.ok === false) {
        console.warn("[live] /move rejected:", body.reason || r.status,
          "(expected for kind:travel until the M0 move-vocab freeze lands)");
      }
      return body;
    } catch (e) {
      console.warn("[live] /move failed:", e);
      return { ok: false };
    }
  }

  startPolling(onTick) {
    const tick = async () => {
      try {
        const [atlas, combat, character] = await Promise.all([this.atlas(), this.combat(), this.character()]);
        onTick({ atlas, combat, character });
      } catch (e) { console.warn("poll failed", e); }
    };
    tick();
    this._timer = setInterval(tick, this.pollMs);
    return () => { if (this._timer) clearInterval(this._timer); };
  }
}

window.SurfaceClient = SurfaceClient;
