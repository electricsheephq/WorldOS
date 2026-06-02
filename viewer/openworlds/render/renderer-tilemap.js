/*
 * WorldOS render/ — GT1 SNES-pixel tilemap scene (M1).
 *
 * The Tier-1 renderer: a 16-bit-styled top-down tilemap exploration view driven entirely by
 * the WorldOS surfaces, consuming a `scene_kind: "tilemap"` render-profile. Like the M0 zone
 * renderer it is a pure CLIENT — it owns no game state:
 *   - reads  /atlas-surface (current location + travel options) + /character-surface (party)
 *            + /combat-surface (zones + tokens) + /events
 *   - writes ONLY constrained intents to /move: `travel` (tap a map exit) and the combat
 *     intents (attack/cast/use_item/move_to_zone).
 *
 * ASSET NOTE (M1 #439, deliberately deferred): real pixel sprite-sheets + tilesets need an
 * image-generation model (self-hosted vs paid API) — an owner cost/dependency decision, so
 * NOT made here. This renderer draws PROCEDURAL placeholder tiles + sprites at runtime
 * (Phaser Graphics, zero external asset), so the whole tier is demoable + testable now. When
 * the asset pipeline lands, the render-profile's `renderer_profiles.phaser.tileset_scope_key`
 * + actor sprite scope-keys resolve through the existing Img-scope -> /image bridge and swap
 * in with no scene change.
 *
 * Phaser vendored at ../vendor/phaser-3.80.1.min.js (no runtime CDN). Self-contained sub-app
 * at /openworlds/render/tilemap.html — NO edits to the shared index.html / app.jsx.
 *
 * Combat = pure REPLAY of engine-decided /combat-surface + /events: zone BANDS with tokens
 * grouped inside, NEVER VTT cells/rulers (the engine is gridless/named-zone; positions are a
 * derived render-hint, never authoritative — see #432). Target-pick + ability bar emit combat
 * intents; AoE shows as a zone highlight + affected list.
 */
"use strict";

const TW = 32;            // tile size (px) — 16-bit feel, also renderer_profiles.phaser.tile_size
const GRID_W = 24, GRID_H = 16;   // map dimensions in tiles (bounded — #436 perf)
const VW = GRID_W * TW, VH = GRID_H * TW;   // map pixel size
const HUD_W = 300;        // right-hand party/combat HUD
const STAGE_W = VW + HUD_W, STAGE_H = VH;

// 16-bit palette for the procedural placeholder tileset (swapped for real art when #439 lands).
const TILE_KINDS = {
  floor:  { fill: 0x2b2118, edge: 0x3a2c1f },
  wall:   { fill: 0x161118, edge: 0x0d0a10 },
  path:   { fill: 0x4a3a22, edge: 0x5c4a2c },
  exit:   { fill: 0x2a5a3a, edge: 0x49c07a },   // a walkable exit tile -> travel
  water:  { fill: 0x213a52, edge: 0x2f5a7c },
};
const TEAM_COLOR = { ally: 0x5a8ac0, foe: 0xb05050, current: 0xf4d27b };

// Deterministic per-location tile layout: a bordered room with a path spine + one exit tile
// per travel option, derived from the atlas (no randomness — same location renders identically
// every poll, so tokens don't jump; mirrors the contract's stable-layout intent).
function buildTileGrid(travelOptions) {
  const g = [];
  for (let y = 0; y < GRID_H; y++) {
    const row = [];
    for (let x = 0; x < GRID_W; x++) {
      const border = x === 0 || y === 0 || x === GRID_W - 1 || y === GRID_H - 1;
      const spine = y === Math.floor(GRID_H / 2) || x === Math.floor(GRID_W / 2);
      row.push(border ? "wall" : spine ? "path" : "floor");
    }
    g.push(row);
  }
  // Place one exit tile per travel option, spaced along the top wall interior.
  const exits = [];
  (travelOptions || []).forEach((opt, i) => {
    const ex = 3 + i * 4;
    if (ex < GRID_W - 1) {
      g[1][ex] = "exit";
      exits.push({ tx: ex, ty: 1, opt });
    }
  });
  return { grid: g, exits };
}

class TilemapScene extends Phaser.Scene {
  constructor() { super("tilemap"); }

  preload() {
    this.load.json("profile", "./render-profile.tilemap.example.json");
  }

  create() {
    this.profile = this.cache.json.get("profile");
    this.tileSize = (this.profile?.renderer_profiles?.phaser?.tile_size) || TW;

    this.mapLayer = this.add.container(0, 0);
    this.tokenLayer = this.add.container(0, 0);
    this.fxLayer = this.add.container(0, 0);
    this.hud = this.add.container(VW, 0);

    // HUD backdrop
    const hb = this.add.graphics();
    hb.fillStyle(0x0c0a06, 1); hb.fillRect(VW, 0, HUD_W, VH);
    hb.lineStyle(1, 0x6a4a2c, 0.5); hb.strokeRect(VW + 0.5, 0.5, HUD_W - 1, VH - 1);
    this.hud.add(hb);

    this.add.text(VW + 12, 10, "GT1 · pixel turn-based", { font: "13px monospace", color: "#e8d8b0" });
    this.modeText = this.add.text(VW + 12, 28, "transport: …", { font: "11px monospace", color: "#9ab" });

    this.base = new URLSearchParams(location.search).get("base") || "";
    this.campaign = new URLSearchParams(location.search).get("campaign") || "";
    // #439 asset pipeline: actor/scene art resolves through the existing Img-scope -> /image
    // bridge (first-party imagegen + BG catalog; owner decision 2026-06-02). A miss is a 404,
    // so we lazy-load each scope once and fall back to the procedural token/tile on loaderror.
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

  // #439: resolve a render-profile art scope through GET /image?scope=… (first-party
  // imagegen + BG catalog). Lazy + idempotent per scope: on success the texture is cached
  // and we trigger a redraw so the sprite replaces its procedural placeholder; a 404 (miss)
  // marks the scope "miss" so we never retry and the placeholder stands. Never blocks render.
  _resolveArt(scope) {
    if (!scope) return "miss";
    const st = this._art[scope];
    if (st) return st;                              // "loading" | "ok" | "miss"
    this._art[scope] = "loading";
    const key = "art:" + scope;
    if (this.textures.exists(key)) { this._art[scope] = "ok"; return "ok"; }
    const url = `${this.base}/image?scope=${encodeURIComponent(scope)}`;
    this.load.image(key, url);
    // Use a PERSISTENT keyed loaderror listener (not .once) — `.once` could be consumed by a
    // DIFFERENT scope's error in the same load batch, leaving this scope stuck "loading". We
    // only act on our own key and remove our own listener, so concurrent 404s each resolve.
    const onErr = (file) => {
      if (!file || file.key !== key) return;
      this._art[scope] = "miss";
      this.load.off("loaderror", onErr);
    };
    this.load.on("loaderror", onErr);
    this.load.once(`filecomplete-image-${key}`, () => {
      this._art[scope] = "ok";
      this.load.off("loaderror", onErr);                 // success → drop the error listener
      if (this._lastSnap) this.render(this._lastSnap);   // redraw so the sprite appears
    });
    this.load.start();
    return "loading";
  }

  // engine actor id -> art scope key, from the render-profile core.actors[] (#434/#439).
  _actorScope(actorId) {
    const actors = this.profile?.core?.actors || [];
    const a = actors.find((x) => x.engine_actor_id === actorId);
    return a?.art?.scope_key || "";
  }

  render(snap) {
    const { atlas, combat, character } = snap;
    this._lastSnap = snap;
    this.mapLayer.removeAll(true);
    this.tokenLayer.removeAll(true);
    this.fxLayer.removeAll(true);

    const inCombat = !!(combat && combat.active);
    const travelOptions = atlas?.travel_options || [];
    const { grid, exits } = buildTileGrid(travelOptions);

    // --- draw the procedural tilemap ---
    const ts = this.tileSize;
    for (let y = 0; y < GRID_H; y++) {
      for (let x = 0; x < GRID_W; x++) {
        const kind = TILE_KINDS[grid[y][x]] || TILE_KINDS.floor;
        const g = this.add.graphics();
        g.fillStyle(kind.fill, 1); g.fillRect(x * ts, y * ts, ts, ts);
        g.lineStyle(1, kind.edge, 0.6); g.strokeRect(x * ts + 0.5, y * ts + 0.5, ts - 1, ts - 1);
        this.mapLayer.add(g);
      }
    }

    // location name banner
    const locName = atlas?.current_location?.name || "—";
    const banner = this.add.graphics();
    banner.fillStyle(0x000000, 0.55); banner.fillRect(0, 0, VW, 22);
    this.mapLayer.add(banner);
    this.mapLayer.add(this.add.text(8, 4, locName, { font: "13px monospace", color: "#f0e0b8" }));

    // --- exit tiles -> click-to-travel (#436) ---
    exits.forEach(({ tx, ty, opt }) => {
      const px = tx * ts, py = ty * ts;
      this.mapLayer.add(this.add.text(px - 2, py + ts + 1,
        "→ " + (opt.name || opt.to || "exit"), { font: "9px monospace", color: "#9fe0b0" }));
      const hit = this.add.zone(px, py, ts, ts).setOrigin(0, 0).setInteractive();
      hit.on("pointerdown", async () => {
        const intent = opt.move || { kind: "travel", target: opt.to };
        const res = await this.client.move(intent);
        const ok = !(res && res.ok === false);
        const flash = this.add.graphics();
        flash.fillStyle(ok ? 0x49c07a : 0xc05050, 0.5);
        flash.fillRect(px, py, ts, ts);
        this.fxLayer.add(flash);
        this.time.delayedCall(500, () => flash.destroy());
      });
      this.mapLayer.add(hit);
    });

    // --- party HUD (from /character-surface; zero client rules) (#437) ---
    this._renderParty(character?.party || []);

    // --- exploration actors OR combat zone-bands (#438) ---
    if (inCombat) {
      this._renderCombat(combat);
    } else {
      this._renderExploration(character?.party || []);
    }
  }

  _renderParty(party) {
    // clear prior party rows by re-adding the HUD backdrop region text
    let y = 50;
    this.hud.list.filter(o => o._isPartyRow).forEach(o => o.destroy());
    const head = this.add.text(VW + 12, y, "PARTY", { font: "12px monospace", color: "#e8d8b0" });
    head._isPartyRow = true; this.hud.add(head); y += 18;
    party.forEach((p) => {
      const line = `${p.name}  ${p.race || ""} ${p.class || ""} L${p.level ?? "?"}`.trim();
      const hp = `HP ${p.hp ?? "?"}/${p.hpMax ?? "?"}  AC ${p.stats?.ac ?? "?"}`;
      const t1 = this.add.text(VW + 12, y, line, { font: "11px monospace", color: "#bcd" });
      const t2 = this.add.text(VW + 12, y + 13, hp, { font: "10px monospace", color: "#c99" });
      t1._isPartyRow = t2._isPartyRow = true;
      this.hud.add(t1); this.hud.add(t2);
      y += 34;
    });
  }

  _renderExploration(party) {
    // party tokens stand on the path spine; foes only appear in combat.
    const ts = this.tileSize, midY = Math.floor(GRID_H / 2);
    party.forEach((p, i) => {
      const tx = 3 + i, ty = midY;
      this._token(tx * ts + ts / 2, ty * ts + ts / 2, "ally", (p.name || "?")[0], p.name, p.id);
    });
  }

  _renderCombat(combat) {
    // Zone BANDS over the lower map area — NOT a VTT grid (no cells, no rulers).
    const zones = (combat.zones || []).map(z => (typeof z === "string" ? z : z.name)).filter(Boolean);
    const tokens = combat.initiative || combat.tokens || [];
    const ts = this.tileSize;
    const bandTop = Math.floor(GRID_H * 0.45) * ts;
    const bandH = VH - bandTop - 4;
    const zoneRects = {};
    if (zones.length) {
      const bw = (VW - 8 - 6 * (zones.length - 1)) / zones.length;
      zones.forEach((zn, i) => {
        const x = 4 + i * (bw + 6);
        const g = this.add.graphics();
        g.fillStyle(0x3a4a6a, 0.30); g.fillRoundedRect(x, bandTop, bw, bandH, 6);
        g.lineStyle(1, 0xffffff, 0.14); g.strokeRoundedRect(x, bandTop, bw, bandH, 6);
        this.fxLayer.add(g);
        this.fxLayer.add(this.add.text(x + 6, bandTop + 4, zn, { font: "10px monospace", color: "#bcd" }));
        zoneRects[zn] = { x, y: bandTop, w: bw, h: bandH };
      });
    }
    const per = {};
    tokens.forEach((c) => {
      const isFoe = (c.team === "foe") || /^mon-/.test(c.id || "") || /cultist|foe|enemy/i.test(c.name || "");
      const zr = zoneRects[c.zone];
      let cx, cy;
      if (zr) {
        const n = (per[c.zone] = (per[c.zone] || 0) + 1);
        cx = zr.x + 22 + ((n - 1) % 4) * 34; cy = zr.y + 34 + Math.floor((n - 1) / 4) * 34;
      } else {
        const k = (per[isFoe ? "_f" : "_a"] = (per[isFoe ? "_f" : "_a"] || 0) + 1);
        cx = (isFoe ? 0.7 : 0.25) * VW; cy = bandTop + 30 + (k - 1) * 34;
      }
      const team = c.is_current || c.active ? "current" : (isFoe ? "foe" : "ally");
      this._token(cx, cy, team, (c.name || "?")[0], c.name, c.id);
    });
    // initiative strip in the HUD
    this.hud.list.filter(o => o._isInit).forEach(o => o.destroy());
    let y = 50 + 18 + ((combat.party_count || 4) * 0); // below party
    const iy0 = VH - 150;
    const ih = this.add.text(VW + 12, iy0, `COMBAT · round ${combat.round ?? "?"}`, { font: "12px monospace", color: "#f4d27b" });
    ih._isInit = true; this.hud.add(ih);
    tokens.forEach((c, i) => {
      const t = this.add.text(VW + 12, iy0 + 18 + i * 14,
        `${(c.is_current || c.active) ? "▸ " : "  "}${c.init ?? c.initiative ?? "?"} ${c.name}`,
        { font: "10px monospace", color: (c.is_current || c.active) ? "#f4d27b" : "#abc" });
      t._isInit = true; this.hud.add(t);
    });
  }

  _token(cx, cy, team, glyph, label, actorId) {
    const color = TEAM_COLOR[team] || TEAM_COLOR.ally;
    // #439: if the actor has resolvable art (render-profile scope → /image), draw the sprite;
    // otherwise the procedural circle. Resolution is lazy + cached; a miss keeps the circle.
    const scope = actorId ? this._actorScope(actorId) : "";
    const artState = scope ? this._resolveArt(scope) : "miss";
    if (artState === "ok") {
      const spr = this.add.image(cx, cy, "art:" + scope).setDisplaySize(28, 34);
      this.tokenLayer.add(spr);
      if (team === "current") {
        const ring = this.add.graphics();
        ring.lineStyle(3, TEAM_COLOR.current, 0.9); ring.strokeCircle(cx, cy, 19);
        this.tokenLayer.add(ring);
      }
    } else {
      const g = this.add.graphics();
      g.fillStyle(color, 1); g.fillCircle(cx, cy, 13);
      if (team === "current") { g.lineStyle(3, TEAM_COLOR.current, 0.9); g.strokeCircle(cx, cy, 17); }
      this.tokenLayer.add(g);
      this.tokenLayer.add(this.add.text(cx - 4, cy - 6, (glyph || "?").toUpperCase(),
        { font: "12px monospace", color: "#0c0a06" }));
    }
    if (label) this.tokenLayer.add(this.add.text(cx - 16, cy + 16, label.slice(0, 10),
      { font: "9px monospace", color: "#eee" }));
  }
}

window.addEventListener("load", () => {
  new Phaser.Game({
    type: Phaser.AUTO, width: STAGE_W, height: STAGE_H, parent: "game",
    backgroundColor: "#0c0a06", pixelArt: true, scene: [TilemapScene],
  });
});
