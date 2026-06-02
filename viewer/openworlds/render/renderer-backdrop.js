/*
 * WorldOS render/ — GT2 Pillars/BG backdrop-isometric scene (M2).
 *
 * The Tier-2 renderer: a painted full-screen BACKDROP with depth-sorted token actors (the
 * Pillars-of-Eternity / Baldur's-Gate look), driven entirely by the WorldOS surfaces and
 * consuming a `scene_kind: "backdrop"` render-profile. Like the M0 zone + M1 tilemap renderers
 * it is a pure CLIENT — it owns no game state:
 *   - reads  /atlas-surface (current location + travel options) + /character-surface (party)
 *            + /combat-surface (zones + tokens) + /events
 *   - writes ONLY constrained intents to /move: `move_to_zone` (click the walkable floor),
 *     `travel` (click an exit), and the combat intents (attack/cast/use_item/move_to_zone).
 *
 * WALKMASK IS RENDERER-OWNED (#444): the engine knows location ADJACENCY, not walkable pixels.
 * So this renderer owns a procedural floor polygon (a perspective trapezoid) purely for
 * presentation — but a click never asserts a coordinate: it resolves to the NEAREST engine
 * ZONE and emits `{kind:"move_to_zone", target:<zone>}` (or `travel` for an exit). The engine
 * owns where you end up; the renderer owns the path. No engine change.
 *
 * DEPTH SORT (#445): token actors are drawn back-to-front by their floor y (objects lower on
 * screen are nearer → drawn on top + slightly larger), so a foreground actor correctly occludes
 * one behind it. Backdrop swaps on location change.
 *
 * ASSET NOTE (#447, deliberately deferred like M1 #439): painted isometric backdrops + a
 * walkmask-authoring + vision-critic coherence gate need an image model + authoring tools — an
 * owner cost/dependency decision, so NOT made here. This renderer draws a PROCEDURAL painted-ish
 * backdrop (gradient sky + ground band + depth haze) + procedural tokens at runtime (zero
 * external asset), so the whole tier is demoable + testable now. When the pipeline lands, the
 * render-profile's location `art.scope_key` + actor scope-keys resolve through the existing
 * Img-scope -> /image bridge and swap in with no scene change. Flat-lit MVP — normal-map
 * lighting is M5/Branch B (#446 defers it explicitly).
 *
 * Phaser vendored at ../vendor/phaser-3.80.1.min.js (no runtime CDN). Self-contained sub-app at
 * /openworlds/render/backdrop.html — NO edits to the shared index.html / app.jsx.
 *
 * Combat = pure REPLAY of engine-decided /combat-surface + /events: tokens placed at their
 * zone's floor marker, depth-sorted, paused/turn presentation (BG/PoE-style). The engine
 * decides every outcome; the renderer never rolls. Derived positions are never authoritative
 * (#432) — they are re-derived from engine zones each poll.
 */
"use strict";

const BD_W = 720;            // backdrop viewport width
const BD_H = 600;            // backdrop viewport height
const BHUD_W = 280;          // right-hand party/combat HUD
const BSTAGE_W = BD_W + BHUD_W, BSTAGE_H = BD_H;

const BTEAM_COLOR = { ally: 0x5a8ac0, foe: 0xb05050, current: 0xf4d27b };
const DEFAULT_HORIZON = 0.45;          // fraction of height where ground meets sky
const DEFAULT_DEPTH_BANDS = [0.55, 0.7, 0.85];

// A renderer-owned perspective floor trapezoid (#444): narrow at the horizon, full-width at the
// bottom. Returned as the 4 screen-space corners + the horizon y. Purely presentation — clicks
// inside resolve to engine ZONES, never to these pixels.
function floorPolygon(horizonY) {
  const top = BD_H * horizonY;
  const inset = BD_W * 0.22;
  return {
    horizon: top,
    points: [
      { x: inset, y: top },              // back-left
      { x: BD_W - inset, y: top },       // back-right
      { x: BD_W - 8, y: BD_H - 8 },      // front-right
      { x: 8, y: BD_H - 8 },             // front-left
    ],
  };
}

// Deterministic floor marker (screen point) for a zone, by its index among the location's zones.
// Spreads zones left→right and steps them back→front along the depth bands so the scene reads as
// an isometric stage. Deterministic (no randomness) so tokens don't jump between polls.
function zoneMarker(index, count, horizonY, depthBands) {
  const bands = depthBands && depthBands.length ? depthBands : DEFAULT_DEPTH_BANDS;
  const band = bands[index % bands.length];
  const y = BD_H * band;
  // x spread shrinks toward the horizon for a perspective feel
  const t = count > 1 ? index / (count - 1) : 0.5;
  const depthT = (y - BD_H * horizonY) / (BD_H - BD_H * horizonY); // 0 at horizon, 1 at front
  const halfW = (0.30 + 0.18 * depthT) * BD_W;
  const x = BD_W / 2 + (t - 0.5) * 2 * halfW;
  return { x, y };
}

class BackdropScene extends Phaser.Scene {
  constructor() { super("backdrop"); }

  preload() {
    // A generated game (M3 build-loop #451) injects its profile via window.WORLDOS_PROFILE
    // (object) or WORLDOS_PROFILE_URL (path); the bundled demo falls back to the example. This
    // is what makes the renderer reusable by AI-built games with no per-game renderer fork.
    if (window.WORLDOS_PROFILE) return;
    this.load.json("profile", window.WORLDOS_PROFILE_URL || "./render-profile.backdrop.example.json");
  }

  create() {
    this.profile = window.WORLDOS_PROFILE || this.cache.json.get("profile");
    this.phaser = this.profile?.renderer_profiles?.phaser || {};

    this.bgLayer = this.add.container(0, 0);      // backdrop
    this.floorLayer = this.add.container(0, 0);   // walkmask + zone markers
    this.tokenLayer = this.add.container(0, 0);   // depth-sorted actors
    this.fxLayer = this.add.container(0, 0);      // exits + click flashes
    this.hud = this.add.container(BD_W, 0);

    // HUD backdrop
    const hb = this.add.graphics();
    hb.fillStyle(0x070b0e, 1); hb.fillRect(BD_W, 0, BHUD_W, BD_H);
    hb.lineStyle(1, 0x33506a, 0.5); hb.strokeRect(BD_W + 0.5, 0.5, BHUD_W - 1, BD_H - 1);
    this.hud.add(hb);
    this.add.text(BD_W + 12, 10, "GT2 · backdrop-isometric", { font: "13px monospace", color: "#cfe0ee" });
    this.modeText = this.add.text(BD_W + 12, 28, "transport: …", { font: "11px monospace", color: "#9ab" });

    this.base = new URLSearchParams(location.search).get("base") || "";
    this.campaign = new URLSearchParams(location.search).get("campaign") || "";
    // Art (location backdrop + actor sprites) resolves through the existing Img-scope -> /image
    // bridge (first-party imagegen + BG catalog; owner decision 2026-06-02). A miss is a 404, so
    // we lazy-load each scope once and fall back to the procedural backdrop/token on loaderror.
    // _art tracks per-scope load state: undefined=untried, "loading", "ok", "miss".
    this._art = {};

    this.client = new window.SurfaceClient({
      baseUrl: this.base,
      campaign: this.campaign,
      onMode: (m) => {
        this.modeText.setText(`transport: ${m.toUpperCase()}${m === "fixture" ? " (fixtures)" : " (live)"}`);
        this.modeText.setColor(m === "live" ? "#8c8" : "#caa");
      },
    });
    this.client.startPolling((snap) => this.render(snap));
  }

  // Resolve a render-profile art scope through GET /image?scope=… (first-party imagegen + BG
  // catalog). Lazy + idempotent per scope: on success the texture is cached and we trigger a
  // redraw so the art replaces its procedural placeholder; a 404 (miss) marks the scope "miss"
  // so we never retry and the placeholder stands. Never blocks render. (Same discipline as the
  // M1 tilemap renderer, including the keyed persistent loaderror listener so concurrent 404s
  // don't consume each other's handler.)
  _resolveArt(scope) {
    if (!scope) return "miss";
    const st = this._art[scope];
    if (st) return st;                              // "loading" | "ok" | "miss"
    this._art[scope] = "loading";
    const key = "art:" + scope;
    if (this.textures.exists(key)) { this._art[scope] = "ok"; return "ok"; }
    const url = `${this.base}/image?scope=${encodeURIComponent(scope)}`;
    this.load.image(key, url);
    const onErr = (file) => {
      if (!file || file.key !== key) return;
      this._art[scope] = "miss";
      this.load.off("loaderror", onErr);
    };
    this.load.on("loaderror", onErr);
    this.load.once(`filecomplete-image-${key}`, () => {
      this._art[scope] = "ok";
      this.load.off("loaderror", onErr);
      if (this._lastSnap) this.render(this._lastSnap);
    });
    this.load.start();
    return "loading";
  }

  _actorScope(actorId) {
    const actors = this.profile?.core?.actors || [];
    const a = actors.find((x) => x.engine_actor_id === actorId);
    return a?.art?.scope_key || "";
  }

  // location engine id -> backdrop scope key + its per-backdrop layout (horizon/tint), from the
  // render-profile (#443). Falls back to defaults so an unmapped location still renders.
  _locationArt(locId) {
    const locs = this.profile?.core?.locations || [];
    const l = locs.find((x) => x.engine_location_id === locId);
    const scope = l?.art?.scope_key || "";
    const layout = (this.phaser.backdrop_layout || {})[scope] || {};
    return { scope, horizonY: layout.horizon_y || DEFAULT_HORIZON, tint: layout.ground_tint || null };
  }

  render(snap) {
    const { atlas, combat, character } = snap;
    this._lastSnap = snap;
    this.bgLayer.removeAll(true);
    this.floorLayer.removeAll(true);
    this.tokenLayer.removeAll(true);
    this.fxLayer.removeAll(true);

    const inCombat = !!(combat && combat.active);
    const locId = atlas?.current_location?.id || atlas?.current_location?.engine_location_id || "";
    const { scope: bgScope, horizonY } = this._locationArt(locId);
    const floor = floorPolygon(horizonY);

    // --- backdrop (#445): real painted art if resolvable, else procedural painted-ish scene ---
    this._renderBackdrop(bgScope, horizonY);

    // --- renderer-owned walkmask floor (#444) ---
    this._renderFloor(floor);

    // location name banner
    const locName = atlas?.current_location?.name || "—";
    const banner = this.add.graphics();
    banner.fillStyle(0x000000, 0.5); banner.fillRect(0, 0, BD_W, 24);
    this.bgLayer.add(banner);
    this.bgLayer.add(this.add.text(8, 5, locName, { font: "14px monospace", color: "#e6eef5" }));

    // --- click-to-move on the floor (#445): resolve to nearest engine ZONE -> move_to_zone ---
    const zones = this._currentZones(atlas, combat, inCombat);
    const markers = zones.map((zn, i) => ({ zn, ...zoneMarker(i, zones.length, horizonY, this.phaser.depth_bands) }));
    this._renderZoneMarkers(markers);
    this._wireFloorClicks(floor, markers);

    // --- exits -> click-to-travel (#445) ---
    this._renderExits(atlas?.travel_options || [], horizonY);

    // --- party HUD (zero client rules) ---
    this._renderParty(character?.party || []);

    // --- actors: depth-sorted tokens (#445); combat = pure replay (#446) ---
    if (inCombat) {
      this._renderCombat(combat, markers);
    } else {
      this._renderExploration(character?.party || [], markers, floor);
    }
  }

  // Linear-interpolate two packed 0xRRGGBB ints by t in [0,1] -> a packed int. (Avoids the
  // Phaser.Display.Color constructor footgun, whose args are SEPARATE r,g,b,a channels, not a
  // packed hex — passing a hex collapses every endpoint to red.)
  _lerpColor(a, b, t) {
    const ar = (a >> 16) & 0xff, ag = (a >> 8) & 0xff, ab = a & 0xff;
    const br = (b >> 16) & 0xff, bg = (b >> 8) & 0xff, bb = b & 0xff;
    const r = Math.round(ar + (br - ar) * t);
    const gg = Math.round(ag + (bg - ag) * t);
    const bl = Math.round(ab + (bb - ab) * t);
    return (r << 16) | (gg << 8) | bl;
  }

  _renderBackdrop(scope, horizonY) {
    const artState = scope ? this._resolveArt(scope) : "miss";
    if (artState === "ok") {
      const img = this.add.image(BD_W / 2, BD_H / 2, "art:" + scope).setDisplaySize(BD_W, BD_H);
      this.bgLayer.add(img);
      return;
    }
    // Procedural painted-ish backdrop: sky gradient + ground band + depth haze (placeholder).
    const hy = BD_H * horizonY;
    const g = this.add.graphics();
    // sky: deep blue (top) -> dusk slate (horizon)
    const SKY = 8;
    for (let i = 0; i < SKY; i++) {
      g.fillStyle(this._lerpColor(0x1a2740, 0x47506a, i / (SKY - 1)), 1);
      g.fillRect(0, (i / SKY) * hy, BD_W, hy / SKY + 1);
    }
    // ground: lit near-horizon -> dark foreground
    const GND = 10;
    for (let i = 0; i < GND; i++) {
      g.fillStyle(this._lerpColor(0x2c2a22, 0x14160f, i / (GND - 1)), 1);
      const y = hy + (i / GND) * (BD_H - hy);
      g.fillRect(0, y, BD_W, (BD_H - hy) / GND + 1);
    }
    // horizon haze line
    g.fillStyle(0x6a7a8c, 0.25); g.fillRect(0, hy - 3, BD_W, 6);
    this.bgLayer.add(g);
  }

  _renderFloor(floor) {
    const g = this.add.graphics();
    g.fillStyle(0x3a4658, 0.16);
    g.beginPath();
    g.moveTo(floor.points[0].x, floor.points[0].y);
    floor.points.slice(1).forEach((p) => g.lineTo(p.x, p.y));
    g.closePath(); g.fillPath();
    g.lineStyle(1, 0x8fb4d8, 0.20);
    g.strokePoints(floor.points.concat([floor.points[0]]), true);
    this.floorLayer.add(g);
  }

  _renderZoneMarkers(markers) {
    markers.forEach(({ zn, x, y }) => {
      const g = this.add.graphics();
      g.lineStyle(1, 0x9fb6cc, 0.30); g.strokeEllipse(x, y, 84, 30);
      this.floorLayer.add(g);
      this.floorLayer.add(this.add.text(x - 40, y - 26, zn, {
        font: "10px monospace", color: "#aebfcf", wordWrap: { width: 86 } }));
    });
  }

  // Click anywhere on the walkable floor -> snap to nearest zone marker -> move_to_zone intent.
  // The renderer owns the path; the engine owns the destination zone. (#444/#445)
  _wireFloorClicks(floor, markers) {
    if (!markers.length) return;
    const poly = new Phaser.Geom.Polygon(floor.points.flatMap((p) => [p.x, p.y]));
    const hit = this.add.zone(0, 0, BD_W, BD_H).setOrigin(0, 0).setInteractive();
    hit.on("pointerdown", async (ptr) => {
      const px = ptr.x, py = ptr.y;
      if (!Phaser.Geom.Polygon.Contains(poly, px, py)) return; // off the walkmask -> ignore
      let best = markers[0], bd = Infinity;
      for (const m of markers) {
        const d = (m.x - px) ** 2 + (m.y - py) ** 2;
        if (d < bd) { bd = d; best = m; }
      }
      const res = await this.client.move({ kind: "move_to_zone", target: best.zn });
      const ok = !(res && res.ok === false);
      const flash = this.add.graphics();
      flash.fillStyle(ok ? 0x49c07a : 0xc05050, 0.55); flash.fillCircle(px, py, 10);
      this.fxLayer.add(flash);
      this.time.delayedCall(500, () => flash.destroy());
    });
    this.fxLayer.add(hit);
  }

  _renderExits(travelOptions, horizonY) {
    const hy = BD_H * horizonY;
    travelOptions.forEach((opt, i) => {
      const n = travelOptions.length;
      const x = (n > 1 ? (i + 0.5) / n : 0.5) * BD_W;
      const y = hy - 14;
      const g = this.add.graphics();
      g.fillStyle(0x2a5a3a, 0.7); g.fillRoundedRect(x - 54, y - 12, 108, 24, 5);
      g.lineStyle(1, 0x49c07a, 0.9); g.strokeRoundedRect(x - 54, y - 12, 108, 24, 5);
      this.fxLayer.add(g);
      this.fxLayer.add(this.add.text(x - 48, y - 7, "→ " + (opt.name || opt.to || "exit").slice(0, 14),
        { font: "10px monospace", color: "#cdeed8" }));
      const hit = this.add.zone(x - 54, y - 12, 108, 24).setOrigin(0, 0).setInteractive();
      hit.on("pointerdown", async () => {
        const intent = opt.move || { kind: "travel", target: opt.to };
        const res = await this.client.move(intent);
        const ok = !(res && res.ok === false);
        const flash = this.add.graphics();
        flash.fillStyle(ok ? 0x49c07a : 0xc05050, 0.5); flash.fillRoundedRect(x - 54, y - 12, 108, 24, 5);
        this.fxLayer.add(flash);
        this.time.delayedCall(500, () => flash.destroy());
      });
      this.fxLayer.add(hit);
    });
  }

  _currentZones(atlas, combat, inCombat) {
    if (inCombat) {
      const z = (combat.zones || []).map((x) => (typeof x === "string" ? x : x.name)).filter(Boolean);
      if (z.length) return z;
    }
    // exploration: zones from the render-profile for the current location, else a sane default.
    const locId = atlas?.current_location?.id || atlas?.current_location?.engine_location_id || "";
    const locs = this.profile?.core?.locations || [];
    const l = locs.find((x) => x.engine_location_id === locId);
    if (l?.zones?.length) return l.zones;
    return ["the foreground", "the mid-ground", "the rear"];
  }

  // Depth-sorted draw: paint tokens back-to-front (smaller/higher = farther), so a nearer actor
  // occludes one behind it. (#445)
  _drawTokensDepthSorted(specs) {
    specs.sort((a, b) => a.cy - b.cy);
    specs.forEach((s) => this._token(s));
  }

  _renderExploration(party, markers, floor) {
    // party stands in the foreground band, spread along the front of the floor.
    const baseY = BD_H - 70;
    const specs = party.map((p, i) => {
      const t = party.length > 1 ? i / (party.length - 1) : 0.5;
      const cx = BD_W * (0.30 + t * 0.40);
      return { cx, cy: baseY + (i % 2) * 18, team: "ally", glyph: (p.name || "?")[0], label: p.name, actorId: p.id };
    });
    this._drawTokensDepthSorted(specs);
  }

  _renderCombat(combat, markers) {
    const tokens = combat.initiative || combat.tokens || [];
    const byZone = {};
    markers.forEach((m) => { byZone[m.zn] = m; });
    const per = {};
    const specs = tokens.map((c) => {
      const isFoe = (c.team === "foe") || /^mon-/.test(c.id || "") || /cultist|foe|enemy/i.test(c.name || "");
      const m = byZone[c.zone];
      let cx, cy;
      if (m) {
        const k = (per[c.zone] = (per[c.zone] || 0) + 1);
        cx = m.x + (((k - 1) % 3) - 1) * 30;
        cy = m.y + Math.floor((k - 1) / 3) * 22;
      } else {
        const k = (per[isFoe ? "_f" : "_a"] = (per[isFoe ? "_f" : "_a"] || 0) + 1);
        cx = (isFoe ? 0.68 : 0.30) * BD_W;
        cy = (isFoe ? BD_H * 0.55 : BD_H - 80) + (k - 1) * 26;
      }
      const team = c.is_current || c.active ? "current" : (isFoe ? "foe" : "ally");
      return { cx, cy, team, glyph: (c.name || "?")[0], label: c.name, actorId: c.id };
    });
    this._drawTokensDepthSorted(specs);

    // initiative strip in the HUD
    this.hud.list.filter((o) => o._isInit).forEach((o) => o.destroy());
    const iy0 = BD_H - 170;
    const ih = this.add.text(BD_W + 12, iy0, `COMBAT · round ${combat.round ?? "?"}`,
      { font: "12px monospace", color: "#f4d27b" });
    ih._isInit = true; this.hud.add(ih);
    tokens.forEach((c, i) => {
      const cur = c.is_current || c.active;
      const t = this.add.text(BD_W + 12, iy0 + 18 + i * 14,
        `${cur ? "▸ " : "  "}${c.init ?? c.initiative ?? "?"} ${c.name}`,
        { font: "10px monospace", color: cur ? "#f4d27b" : "#aebfcf" });
      t._isInit = true; this.hud.add(t);
    });
  }

  _renderParty(party) {
    this.hud.list.filter((o) => o._isPartyRow).forEach((o) => o.destroy());
    let y = 50;
    const head = this.add.text(BD_W + 12, y, "PARTY", { font: "12px monospace", color: "#cfe0ee" });
    head._isPartyRow = true; this.hud.add(head); y += 18;
    party.forEach((p) => {
      const line = `${p.name}  ${p.race || ""} ${p.class || ""} L${p.level ?? "?"}`.trim();
      const hp = `HP ${p.hp ?? "?"}/${p.hpMax ?? "?"}  AC ${p.stats?.ac ?? "?"}`;
      const t1 = this.add.text(BD_W + 12, y, line, { font: "11px monospace", color: "#bcd" });
      const t2 = this.add.text(BD_W + 12, y + 13, hp, { font: "10px monospace", color: "#c99" });
      t1._isPartyRow = t2._isPartyRow = true;
      this.hud.add(t1); this.hud.add(t2);
      y += 34;
    });
  }

  // Draw one actor token. Depth scale: nearer (larger cy) => slightly bigger. Real sprite when
  // its render-profile scope resolves (#447 art bridge), else a procedural disc. (#445 occlusion)
  _token({ cx, cy, team, glyph, label, actorId }) {
    const color = BTEAM_COLOR[team] || BTEAM_COLOR.ally;
    const depthT = Phaser.Math.Clamp((cy - BD_H * 0.45) / (BD_H * 0.55), 0, 1);
    const scale = 0.8 + 0.5 * depthT;            // farther (small cy) -> smaller
    const r = 13 * scale;
    const scope = actorId ? this._actorScope(actorId) : "";
    const artState = scope ? this._resolveArt(scope) : "miss";
    if (artState === "ok") {
      const spr = this.add.image(cx, cy, "art:" + scope).setDisplaySize(30 * scale, 38 * scale);
      spr.setOrigin(0.5, 0.85);                   // feet at the floor point for correct occlusion
      this.tokenLayer.add(spr);
      if (team === "current") {
        const ring = this.add.graphics();
        ring.lineStyle(3, BTEAM_COLOR.current, 0.9); ring.strokeEllipse(cx, cy, 34 * scale, 14 * scale);
        this.tokenLayer.add(ring);
      }
    } else {
      // ground shadow (sells the isometric footing) + disc
      const sh = this.add.graphics();
      sh.fillStyle(0x000000, 0.28); sh.fillEllipse(cx, cy, r * 2.0, r * 0.8);
      this.tokenLayer.add(sh);
      const g = this.add.graphics();
      g.fillStyle(color, 1); g.fillCircle(cx, cy - r, r);
      if (team === "current") { g.lineStyle(3, BTEAM_COLOR.current, 0.9); g.strokeCircle(cx, cy - r, r + 3); }
      this.tokenLayer.add(g);
      this.tokenLayer.add(this.add.text(cx - 4 * scale, cy - r - 6 * scale, (glyph || "?").toUpperCase(),
        { font: `${Math.round(12 * scale)}px monospace`, color: "#0a0c0e" }));
    }
    if (label) this.tokenLayer.add(this.add.text(cx - 16, cy + 4, label.slice(0, 12),
      { font: "9px monospace", color: "#eef4fa" }));
  }
}

window.addEventListener("load", () => {
  new Phaser.Game({
    type: Phaser.AUTO, width: BSTAGE_W, height: BSTAGE_H, parent: "game",
    backgroundColor: "#0a0c0e", scene: [BackdropScene],
  });
});
