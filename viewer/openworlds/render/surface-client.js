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

  // The transport entry point. PREFERS the SSE push channel (#455) for low latency and falls
  // back to polling automatically — all behind this one interface, so renderers don't change.
  startPolling(onTick) {
    this._onTick = onTick;
    if (this._trySSE(onTick)) return () => this.stop();
    return this._startPoll(onTick);
  }

  // SSE transport (#455): subscribe to /surface-stream, which pushes the SAME {atlas, combat,
  // character} payloads the GET surfaces return. Auto-reconnects (the server caps each stream);
  // if it never delivers a single event (no live server / unsupported), we fall back to polling.
  _trySSE(onTick) {
    if (typeof EventSource === "undefined") return false;
    if (this.mode === "fixture") return false;            // standalone fixtures -> just poll
    try {
      const sep = this._q() ? "&" : "?";
      const url = `${this.baseUrl}/surface-stream${this._q()}${sep}max_seconds=300`;
      const es = new EventSource(url);
      this._es = es;
      this._sseGotData = false;
      es.addEventListener("surfaces", (ev) => {
        try {
          const b = JSON.parse(ev.data);
          this._sseGotData = true;
          if (this.mode !== "live") { this.mode = "live"; this.onMode("live"); }
          onTick({ atlas: b.atlas, combat: b.combat, character: b.character });
        } catch (_e) { /* malformed frame — ignore, next frame recovers */ }
      });
      es.onerror = () => {
        // If we've received data, this is the server's capped-stream close (or a blip) —
        // let EventSource auto-reconnect. If we NEVER got data, SSE isn't viable here: close
        // and fall back to polling (which also drives the fixture fallback).
        if (this._sseGotData) return;
        es.close();
        this._es = null;
        if (!this._timer) this._startPoll(onTick);
      };
      return true;
    } catch (_e) {
      return false;
    }
  }

  _startPoll(onTick) {
    const tick = async () => {
      try {
        const [atlas, combat, character] = await Promise.all([this.atlas(), this.combat(), this.character()]);
        onTick({ atlas, combat, character });
      } catch (e) { console.warn("poll failed", e); }
    };
    tick();
    this._timer = setInterval(tick, this.pollMs);
    return () => this.stop();
  }

  stop() {
    if (this._es) { this._es.close(); this._es = null; }
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  }
}

window.SurfaceClient = SurfaceClient;
