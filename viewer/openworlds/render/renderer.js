/*
 * WorldOS render/ — Phaser thin-client scene (M0).
 *
 * Promoted from spikes/m0-phaser-thin-client/ into the served viewer subtree
 * (viewer/openworlds/render/, reachable at /openworlds/render/). A pure CLIENT
 * of the WorldOS surfaces: it (1) reads /atlas-surface + /combat-surface +
 * /character-surface, (2) renders a ZONE-MODE scene — zone BANDS with tokens
 * grouped inside, NOT a VTT grid (the render-profile contract: positions are
 * presentation derived from engine zones; docs/roadmap/contracts/render-profile.md),
 * (3) re-derives token x,y from `zone` itself (renderer owns layout, engine owns
 * truth), (4) posts a `travel` INTENT to /move on click.
 *
 * Phaser is vendored at ../vendor/phaser-3.80.1.min.js (no runtime CDN).
 * Self-contained sub-app: requires NO edits to the shared index.html or app.jsx.
 * React-screen integration is a later, coordinated milestone step.
 */
"use strict";

const W = 960, H = 600;
const ZONE_COLORS = [0x3a4a6a, 0x6a4a3a, 0x3a6a4a, 0x5a3a6a]; // band tints, cycled

class SpikeScene extends Phaser.Scene {
  constructor() { super("spike"); }

  preload() {
    this.load.json("profile", "./render-profile.example.json");
  }

  async create() {
    this.profile = this.cache.json.get("profile");
    this.add.text(16, 12, "WorldOS M0 spike — Phaser thin-client", { font: "18px monospace", color: "#e8d8b0" });
    this.modeText = this.add.text(16, 36, "transport: connecting…", { font: "12px monospace", color: "#9ab" });
    this.add.text(16, H - 22, "zone-mode render: zone BANDS, not VTT cells · tokens grouped by engine zone · click a travel chip → POST /move {kind:travel}",
      { font: "11px monospace", color: "#778" });

    this.zoneLayer = this.add.container(0, 0);
    this.tokenLayer = this.add.container(0, 0);
    this.hudLayer = this.add.container(0, 0);

    this.client = new window.SurfaceClient({
      baseUrl: new URLSearchParams(location.search).get("base") || "",
      campaign: new URLSearchParams(location.search).get("campaign") || "",
      onMode: (m) => {
        this.modeText.setText(`transport: ${m.toUpperCase()}${m === "fixture" ? " (no live game — bundled fixtures)" : " (real engine surfaces)"}`);
        this.modeText.setColor(m === "live" ? "#8c8" : "#caa");
      },
    });

    this.client.startPolling((snap) => this.render(snap));
  }

  // ---- render the whole frame from a surface snapshot (renderer owns NO state) ----
  render({ atlas, combat, character }) {
    this.zoneLayer.removeAll(true);
    this.tokenLayer.removeAll(true);
    this.hudLayer.removeAll(true);

    const locName = atlas?.current_location?.name || "—";
    this.add.existing(this._txt(this.hudLayer, 16, 60, `location: ${locName}`, "#e8d8b0", 14));

    const zones = (combat?.zones || []).map(z => (typeof z === "string" ? z : z.name)).filter(Boolean);
    const combatants = combat?.initiative || [];

    // ZONE BANDS — discrete regions, NOT a measurement grid. This is the honest
    // zone-mode rendering the contract requires.
    const bandTop = 90, bandH = 150, bandGap = 12;
    const usableW = W - 32;
    const zoneRects = {};
    if (zones.length) {
      const bw = (usableW - bandGap * (zones.length - 1)) / zones.length;
      zones.forEach((zname, i) => {
        const x = 16 + i * (bw + bandGap);
        const g = this.add.graphics();
        g.fillStyle(ZONE_COLORS[i % ZONE_COLORS.length], 0.35);
        g.fillRoundedRect(x, bandTop, bw, bandH, 8);
        g.lineStyle(1, 0xffffff, 0.12); g.strokeRoundedRect(x, bandTop, bw, bandH, 8);
        this.zoneLayer.add(g);
        this.zoneLayer.add(this._txt(null, x + 8, bandTop + 6, zname, "#bcd", 11));
        zoneRects[zname] = { x, y: bandTop, w: bw, h: bandH };
      });
    } else {
      this.zoneLayer.add(this._txt(null, 16, bandTop, "theater-of-the-mind (no declared zones) — party left / foes right", "#9ab", 12));
    }

    // TOKENS — placed by ZONE (renderer re-derives layout; ignores surface x,y to
    // prove the contract: zone is authoritative-join, x,y is an ephemeral hint).
    const perZoneCount = {};
    combatants.forEach((c) => {
      const isFoe = /^mon-/.test(c.id) || /cultist|foe|enemy/i.test(c.name || "");
      let cx, cy;
      const zr = zoneRects[c.zone];
      if (zr) {
        const n = (perZoneCount[c.zone] = (perZoneCount[c.zone] || 0) + 1);
        cx = zr.x + 30 + ((n - 1) % 3) * 40;
        cy = zr.y + 50 + Math.floor((n - 1) / 3) * 44;
      } else {
        // theater fallback mirrors _combat_row_positions: party left, foes right
        const k = (perZoneCount[isFoe ? "_foe" : "_party"] = (perZoneCount[isFoe ? "_foe" : "_party"] || 0) + 1);
        cx = (isFoe ? 0.72 : 0.25) * W;
        cy = 110 + (k - 1) * 50;
      }
      const tk = this.add.container(cx, cy);
      const dot = this.add.graphics();
      const color = c.is_current ? 0xf4d27b : (isFoe ? 0xb05050 : 0x5a8ac0);
      dot.fillStyle(color, 1); dot.fillCircle(0, 0, 16);
      if (c.is_current) { dot.lineStyle(3, 0xf4d27b, 0.9); dot.strokeCircle(0, 0, 21); }
      tk.add(dot);
      tk.add(this._txt(null, -16, 18, c.name || c.id, "#eee", 10));
      const hp = (c.hp_current != null) ? `${c.hp_current}/${c.hp_max}` : "";
      if (hp) tk.add(this._txt(null, -16, 30, hp, "#c99", 9));
      (c.conditions || []).length && tk.add(this._txt(null, -16, -30, (c.conditions).join(","), "#fb8", 9));
      this.tokenLayer.add(tk);
    });

    // INITIATIVE HUD (from the already-shipped /combat-surface, #412)
    const hudY = bandTop + bandH + 18;
    this.hudLayer.add(this._txt(null, 16, hudY, combat?.active ? `COMBAT · round ${combat.round} · turn: ${this._curName(combat)}` : "exploration (no combat)", "#e8d8b0", 13));
    combatants.forEach((c, i) => {
      const t = this._txt(null, 16 + (i % 4) * 230, hudY + 22 + Math.floor(i / 4) * 18,
        `${c.is_current ? "▸ " : "  "}${c.initiative ?? "?"}  ${c.name}`, c.is_current ? "#f4d27b" : "#abc", 11);
      this.hudLayer.add(t);
    });

    // PARTY PANEL (from /character-surface — zero client rules)
    const party = character?.party || [];
    const pY = hudY + 70;
    this.hudLayer.add(this._txt(null, 16, pY, "PARTY", "#e8d8b0", 12));
    party.forEach((p, i) => {
      this.hudLayer.add(this._txt(null, 16, pY + 18 + i * 16,
        `${p.name}  ${p.race} ${p.class} L${p.level}  HP ${p.hp}/${p.hpMax}  AC ${p.stats?.ac}`, "#bcd", 11));
    });

    // TRAVEL CHIPS — the renderer's only WRITE path: a constrained intent.
    const opts = atlas?.travel_options || [];
    const tX = 560;
    this.hudLayer.add(this._txt(null, tX, pY, "TRAVEL → POST /move {kind:travel}", "#e8d8b0", 12));
    opts.forEach((o, i) => {
      const y = pY + 20 + i * 30;
      const g = this.add.graphics();
      g.fillStyle(0x2a3a2a, 0.8); g.fillRoundedRect(tX, y, 360, 24, 6);
      g.lineStyle(1, 0x6a8a6a, 0.6); g.strokeRoundedRect(tX, y, 360, 24, 6);
      this.hudLayer.add(g);
      const label = this._txt(null, tX + 8, y + 5, `→ ${o.name}`, "#cec", 11);
      this.hudLayer.add(label);
      const zone = this.add.zone(tX, y, 360, 24).setOrigin(0, 0).setInteractive();
      zone.on("pointerdown", async () => {
        const res = await this.client.move(o.move || { kind: "travel", target: o.to });
        label.setText(`→ ${o.name}   [intent sent: ${JSON.stringify(o.move || { kind: "travel", target: o.to })}]`);
        label.setColor(res && res.ok === false ? "#f88" : "#8f8");
      });
      this.hudLayer.add(zone);
    });
  }

  _curName(combat) {
    const c = (combat?.initiative || []).find(x => x.is_current);
    return c ? c.name : "—";
  }
  _txt(parent, x, y, s, color, size) {
    const t = this.add.text(x, y, s, { font: `${size || 12}px monospace`, color: color || "#ccc" });
    if (parent) parent.add(t);
    return t;
  }
}

window.addEventListener("load", () => {
  new Phaser.Game({
    type: Phaser.AUTO, width: W, height: H, parent: "game",
    backgroundColor: "#14110a", scene: [SpikeScene],
  });
});
