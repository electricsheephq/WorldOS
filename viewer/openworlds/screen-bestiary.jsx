/* Screen: Bestiary / Codex — encounters, lore, NPCs */

// Slug a creature name into the /image scope segment, EXACTLY as screen-inventory's
// slug() does (lowercase, [^a-z0-9]+ -> "-", trimmed). Ingested creature art is keyed
// "creature:<slug>"; the viewer's _scope_key normalises that and "creature-<slug>" to the
// same key ("creature" is NOT a stripped prefix), so the art resolves regardless of
// separator. Mirrors the id derivation below so the scope tracks the entry.
//
// CONTRACT WITH THE ENGINE SLUG-ALIAS (UI audit P1, screen-bestiary):* the viewer emits the
// CANONICAL slug of the creature's display name — e.g. "gnoll", "bugbear", "mind-flayer",
// "aboleth". Several ingested dirs use a VARIANT suffix the marquee name doesn't carry
// ("gnoll-warrior", "bugbear-warrior"), so those 404 on the clean slug. Resolving that drift
// is the ENGINE's job (server.py _scope_key / _portrait_by_name fuzzy-match or an alias map):
// it should fold "gnoll" -> "gnoll-warrior", "bugbear" -> "bugbear-warrior", etc. The viewer
// deliberately keeps emitting the clean canonical slug (not a variant guess) so the alias has
// a single, stable key to map from. Do NOT special-case variant names here.
function creatureSlug(name) {
  return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

// Map one live /bestiary-surface item (player_bestiary_preview shape:
// { name, size, type, cr, content_origin, known_actions[], source?, license?, provenance? })
// onto the codex entry shape this screen renders. Fields the player-safe preview does NOT
// expose (stats / hd / ac / body / tactics / loot) are simply absent, so BestiaryEntry hides
// them — never a fake stat block.
function liveBestiaryEntry(item) {
  const name = String(item?.name || "").trim();
  const size = String(item?.size || "").trim();
  const type = String(item?.type || "").trim();
  const descriptor = [size, type].filter(Boolean).join(" · ");
  const cslug = creatureSlug(name);
  return {
    id: "live:" + (name.toLowerCase().replace(/[^a-z0-9]+/g, "-") || Math.random().toString(36).slice(2)),
    name: name || "Unknown",
    short: name.slice(0, 6).toLowerCase() || "?",
    // /image scope for the creature plate; "" → graceful placeholder (no fetch / 404).
    imageScope: cslug ? "creature-" + cslug : "",
    short_descriptor: descriptor,
    subtitle: "",
    size,
    kind: type,
    alignment: "",            // preview is player-safe — no alignment leaked; eyebrow shows size·kind
    cr: (item?.cr !== undefined && item?.cr !== null && String(item.cr) !== "") ? String(item.cr) : "",
    knownActions: Array.isArray(item?.known_actions) ? item.known_actions.filter((a) => String(a).trim()) : [],
    contentOrigin: String(item?.content_origin || "srd"),
    source: item?.source ? String(item.source) : "",
    license: item?.license ? String(item.license) : "",
    provenance: item?.provenance ? String(item.provenance) : "",
  };
}

function ScreenBestiary({ onNavigate, state, setState }) {
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  const [tab, setTab] = React.useState("creatures");
  const [selected, setSelected] = React.useState(null);
  const [filter, setFilter] = React.useState("");
  // Live codex from /bestiary-surface; null until the first successful fetch.
  const [liveCreatures, setLiveCreatures] = React.useState(null);
  // World/region label for the codex eyebrow. Data-driven when the surface carries a label
  // (so the header tracks whatever world is loaded); defaults to the Sword Coast for this
  // post-BG3 Baldur's Gate setting — never the old Pathfinder "Marches" demo leak (UI audit BE-02).
  const [worldLabel, setWorldLabel] = React.useState("");
  const wired = liveCreatures !== null;

  // Fetch the live bestiary, driving the search box straight to ?q=<term>. The route reads
  // `q`; we also carry the campaign surfaceQuery (mirrors screen-relations.jsx) — harmless
  // params the read model ignores. Re-runs (debounced) whenever the query term changes.
  const loadSurface = React.useCallback(async (q, isCancelled = () => false) => {
    try {
      const params = new URLSearchParams(surfaceQuery.replace(/^\?/, ""));
      if (q) params.set("q", q); else params.delete("q");
      const qs = params.toString();
      const response = await fetch("/bestiary-surface" + (qs ? "?" + qs : ""), { cache: "no-store" });
      if (!response.ok) throw new Error(`bestiary surface ${response.status}`);
      const payload = await response.json();
      if (isCancelled()) return;
      const items = Array.isArray(payload?.items) ? payload.items : [];
      setLiveCreatures(items.map(liveBestiaryEntry));
      // Adopt a server-provided region label when present (forward-compatible with a future
      // surface.world_label); otherwise the Sword Coast default holds.
      const label = String(payload?.world_label || payload?.region || "").trim();
      if (label) setWorldLabel(label);
    } catch (error) {
      if (isCancelled()) return;
      /* keep the last good surface; the empty-state shows until the first success */
    }
  }, [surfaceQuery]);

  React.useEffect(() => {
    let cancelled = false;
    // Debounce search-driven refetches so each keystroke doesn't fire a request.
    const handle = window.setTimeout(() => { if (!cancelled) loadSurface(filter, () => cancelled); }, 200);
    return () => { cancelled = true; window.clearTimeout(handle); };
  }, [loadSurface, filter]);

  // Creatures come from the live surface once loaded (the server already applied ?q, so we
  // don't re-filter those); empty until the first fetch. Persons & lore have no live
  // read-model yet, so they stay empty and the panel shows an honest empty-state — never
  // a hardcoded demo fallback.
  const creatureEntries = wired ? liveCreatures : [];
  const entries = tab === "creatures" ? creatureEntries : [];
  const filtered = (tab === "creatures" && wired)
    ? entries
    : entries.filter((e) => !filter || e.name.toLowerCase().includes(filter.toLowerCase()));

  // Honest empty-state copy per tab (no live read-model behind persons/lore).
  const emptyStateText = tab === "creatures"
    ? "Creatures are recorded here as you encounter them in play."
    : "Lore and figures are recorded here as your chronicle unfolds.";

  React.useEffect(() => {
    if (filtered.length === 0) {
      if (selected) setSelected(null);
      return;
    }
    if (!filtered.find((e) => e.id === selected?.id)) {
      setSelected(filtered[0]);
    }
  }, [filtered, selected?.id]);

  return (
    <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", gap: 8, padding: 14 }}>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "280px 1fr", gap: 14, minHeight: 0 }}>

      {/* LEFT — index */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>Encyclopaedia of</div>
        <h2 className="h1" style={{ fontSize: 22 }}>{worldLabel || "the Sword Coast"}</h2>
        <Divider />

        <div style={{ display: "flex", gap: 4, marginBottom: 12 }}>
          {[
            { id: "creatures", label: "Creatures" },
            { id: "people", label: "Persons" },
            { id: "lore", label: "Lore" },
          ].map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)} className="pill" style={{
              cursor: "pointer", flex: 1, textAlign: "center",
              background: tab === t.id ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.08)",
              color: tab === t.id ? "var(--w-300)" : "var(--ink-700)",
              boxShadow: tab === t.id ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            }}>{t.label}</button>
          ))}
        </div>

        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search the index…"
          style={{ ...window.inkInput, fontSize: 14, padding: "8px 12px" }}
        />

        <div style={{ flex: 1, overflow: "auto", marginTop: 12, display: "flex", flexDirection: "column", gap: 4 }}>
          {filtered.length === 0 && (
            <div className="body-sm muted" style={{ padding: "8px 2px" }}>{emptyStateText}</div>
          )}
          {filtered.map((e) => (
            <button key={e.id} onClick={() => setSelected(e)} style={{
              display: "grid", gridTemplateColumns: "36px 1fr auto", gap: 8, alignItems: "center",
              padding: "6px 10px",
              background: selected?.id === e.id ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
              boxShadow: selected?.id === e.id
                ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
                : "inset 0 -1px 0 rgba(140,100,60,0.15)",
              cursor: "pointer",
              textAlign: "left",
            }}>
              <Img scope={e.unknown ? "" : e.imageScope} label={e.short} w={36} h={44} framed />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.06em", color: e.unknown ? "var(--ink-600)" : "var(--ink-900)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontStyle: e.unknown ? "italic" : "normal" }}>
                  {e.unknown ? "?????" : e.name}
                </div>
                <div className="hand muted" style={{ fontSize: 11 }}>{e.short_descriptor}</div>
              </div>
              {e.cr && <span className="pill" style={{ background: "rgba(176,141,87,0.18)", boxShadow: "inset 0 0 0 1px var(--b-500)" }}>CR {e.cr}</span>}
            </button>
          ))}
        </div>

        <div className="muted body-sm" style={{ marginTop: 8, textAlign: "center" }}>
          {filtered.length} known{(() => {
            const rumoured = creatureEntries.filter((e) => e.unknown).length;
            return rumoured ? ` · ${rumoured} rumoured` : "";
          })()}
        </div>
      </Panel>

      {/* RIGHT — entry (or honest empty-state when nothing has been recorded yet) */}
      {selected ? <BestiaryEntry entry={selected} tab={tab} /> : (
        <Panel framed style={{ padding: 40, display: "grid", placeItems: "center" }}>
          <div style={{ textAlign: "center", maxWidth: 400 }}>
            <h2 className="h1" style={{ fontSize: 22 }}>Nothing recorded yet</h2>
            <p className="body" style={{ marginTop: 12 }}>{emptyStateText}</p>
          </div>
        </Panel>
      )}
      </div>
    </div>
  );
}

function BestiaryEntry({ entry, tab }) {
  if (entry.unknown) {
    return (
      <Panel framed style={{ padding: 40, display: "grid", placeItems: "center" }}>
        <div style={{ textAlign: "center", maxWidth: 400 }}>
          <div style={{ fontSize: 48, color: "var(--crimson)", fontFamily: "var(--f-display)" }}>?</div>
          <h2 className="h1" style={{ fontSize: 22 }}>Not yet known</h2>
          <p className="body dropcap" style={{ marginTop: 12, textAlign: "left" }}>
            The chronicle has heard rumour of this, but has not yet seen it with its own eyes. Investigate, encounter, or be told to fill this page.
          </p>
        </div>
      </Panel>
    );
  }
  return (
    <Panel framed style={{ padding: 28, overflow: "auto" }}>
      <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 22, alignItems: "start" }}>
        <div>
          {/* Full-width within the 200px detail column (like screen-inventory's hero plate):
              w="100%" keeps the art inside its column; the placeholder fallback was already
              column-width, so this matches its footprint while letting real art fill the frame. */}
          <Img scope={tab === "creatures" ? entry.imageScope : ""} label={`${entry.short} · plate`} w="100%" h={240} framed />
          {entry.cr && (
            <div style={{ marginTop: 8, padding: 8, background: "rgba(176,141,87,0.1)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)" }}>
              <div className="eyebrow text-center" style={{ textAlign: "center" }}>Challenge</div>
              <div style={{ fontFamily: "var(--f-display)", fontSize: 28, textAlign: "center", color: "var(--crimson)", letterSpacing: "0.06em" }}>{entry.cr}</div>
            </div>
          )}
        </div>

        <div>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>
            {tab === "creatures"
              ? [entry.alignment, [entry.size, entry.kind].filter(Boolean).join(" ")].filter(Boolean).join(" · ") :
             tab === "people" ? entry.role :
             "Lore entry"}
          </div>
          <h1 className="h1" style={{ marginTop: 2 }}>{entry.name}</h1>
          {entry.subtitle && <div className="hand" style={{ fontSize: 15, color: "var(--ink-700)" }}>{entry.subtitle}</div>}

          <Divider />

          {tab === "creatures" && entry.stats && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 6, marginBottom: 16 }}>
              {Object.entries(entry.stats).map(([k, v]) => (
                <div key={k} style={{
                  padding: "8px 0", textAlign: "center",
                  background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
                  boxShadow: "inset 0 0 0 1px var(--b-500)",
                }}>
                  <div className="eyebrow" style={{ fontSize: 9 }}>{k.toUpperCase()}</div>
                  <div style={{ fontFamily: "var(--f-display)", fontSize: 18, color: "var(--ink-900)", marginTop: 2 }}>{v}</div>
                </div>
              ))}
            </div>
          )}

          {/* Hide-when-blank (UI audit BE-03/BE-05): the player-safe bestiary preview does
              not expose HD/AC/Speed/Senses/Save/Encountered, so showing them as naked empty
              labels reads broken. Render only the stat lines that actually carry a value, and
              drop the grid entirely when none do — never a row of empty labels. */}
          {tab === "creatures" && (() => {
            const statLines = [
              { k: "HD", v: entry.hd },
              { k: "AC", v: entry.ac },
              { k: "Speed", v: entry.speed },
              { k: "Senses", v: entry.senses },
              { k: "Save", v: entry.save },
              { k: "Encountered", v: entry.encounteredAt },
            ].filter((s) => s.v !== undefined && s.v !== null && String(s.v).trim() !== "");
            if (statLines.length === 0) return null;
            return (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginBottom: 16 }}>
                {statLines.map((s) => <StatLine key={s.k} k={s.k} v={s.v} />)}
              </div>
            );
          })()}

          {entry.body && (
            <p className="body dropcap" style={{ marginTop: 12 }}>
              {entry.body}
            </p>
          )}

          {/* Known abilities — player-safe action names from the live bestiary preview
              (knownActions). Hidden when the entry carries none. */}
          {Array.isArray(entry.knownActions) && entry.knownActions.length > 0 && (
            <>
              <Divider />
              <SectionTitle>Known abilities</SectionTitle>
              <div className="tag-row" style={{ marginTop: 6 }}>
                {entry.knownActions.map((a) => <Pill key={a}>{a}</Pill>)}
              </div>
            </>
          )}

          {/* Provenance — authored (non-SRD) content credits its source/license. */}
          {entry.contentOrigin === "authored" && (entry.source || entry.license || entry.provenance) && (
            <>
              <Divider />
              <div className="eyebrow">Source</div>
              <div className="hand muted" style={{ fontSize: 12, marginTop: 4 }}>
                {[entry.source, entry.license, entry.provenance].filter(Boolean).join(" · ")}
              </div>
            </>
          )}

          {entry.tactics && (
            <>
              <Divider />
              <SectionTitle>{tab === "creatures" ? "Tactics" : "What is known"}</SectionTitle>
              <p className="body">{entry.tactics}</p>
            </>
          )}

          {entry.loot && (
            <>
              <Divider />
              <SectionTitle>Spoils</SectionTitle>
              <div className="tag-row" style={{ marginTop: 6 }}>
                {entry.loot.map((l) => <Pill key={l}>{l}</Pill>)}
              </div>
            </>
          )}

          {entry.marginalia && (
            <>
              <Divider />
              <div className="eyebrow">Marginalia</div>
              <div className="hand" style={{ fontSize: 14, marginTop: 6, color: "var(--ink-700)" }}>
                "{entry.marginalia}"
                <div className="muted" style={{ fontFamily: "var(--f-body)", fontStyle: "normal", fontSize: 12, marginTop: 4 }}>
                  — {entry.marginaliaBy || "the chronicle"}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </Panel>
  );
}

Object.assign(window, { ScreenBestiary, BestiaryEntry });
