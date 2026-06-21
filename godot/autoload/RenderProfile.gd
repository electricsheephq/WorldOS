extends Node
## RenderProfile — loads + parses the render-profile (core + the `godot` block).
##
## CONTRACT ROLE (mirrors the render-profile consumed by
## viewer/openworlds/render/renderer-backdrop.js): the profile is the SOLE source
## of art scope-keys, named zones, zone→screen anchors, the dimetric projection,
## and per-actor sprite-sheet metadata. The engine never ships pixels or screen
## coordinates — it ships LOCATION + ZONE names, and this profile maps those names
## to presentation. (See ISO-PROJECTION.md: "Zone → screen placement is
## data-driven from renderer_profiles.godot.backdrop_layout[scope].zone_anchors".)
##
## SLICE SOURCE (#1053): for now the profile is loaded from a bundled fixture
## (res://fixtures/render-profile.json — a copy of
## viewer/openworlds/render/render-profile.godot.example.json). A live engine /
## per-game profile-injection path (window.WORLDOS_PROFILE-style) lands later; this
## API does not change when it does.
##
## DEFAULTABLE BY DESIGN: every accessor returns a sane empty/default when the key
## is absent, so a profile-less run (missing file, partial profile, unmapped
## location) still renders — WorldView falls back to atlas zones + procedural
## layout. Nothing here throws on a missing field.

## Fallback default facing when the profile omits `renderer_profiles.godot.default_facing`.
const DEFAULT_FACING := "S"
## The locked dimetric facing order (ISO-PROJECTION.md) — the row layout a served
## atlas is sliced by when the actor_sheet omits its own `facing_order`.
const DEFAULT_FACING_ORDER := ["S", "SE", "E", "NE", "N", "NW", "W", "SW"]
## Default dimetric projection (matches the ISO-PROJECTION.md lock) when the
## profile omits `renderer_profiles.godot.projection`.
const DEFAULT_PROJECTION := {"kind": "dimetric", "tile_ratio": "2:1", "angle_deg": 26.57}

const FIXTURE_PATH := "res://fixtures/render-profile.json"

## The whole parsed profile document (or {} if none could be loaded).
var _profile: Dictionary = {}


func _ready() -> void:
	_profile = _load_fixture()
	var loc_count := 0
	var core: Dictionary = _profile.get("core", {})
	if typeof(core) == TYPE_DICTIONARY:
		var locs: Variant = core.get("locations", [])
		if typeof(locs) == TYPE_ARRAY:
			loc_count = (locs as Array).size()
	print("[RenderProfile] loaded=%s locations=%d" % [str(not _profile.is_empty()), loc_count])


# ---------------------------------------------------------------------------
# Core accessors (provider-agnostic part of the profile).
# ---------------------------------------------------------------------------

## Return the core location entry for an engine location id, as
## {art:{scope_key}, zones:[...]} (the raw entry). Empty {} if absent — callers
## then fall back to the atlas surface's own current_location/zones.
func core_location(engine_location_id: String) -> Dictionary:
	if engine_location_id == "":
		return {}
	var locs := _core_array("locations")
	for entry in locs:
		if typeof(entry) == TYPE_DICTIONARY and String(entry.get("engine_location_id", "")) == engine_location_id:
			return entry
	return {}


## Convenience: the backdrop art scope-key for a location (e.g. "scene-lower-city"),
## or "" if the location/art is unmapped.
func core_location_scope(engine_location_id: String) -> String:
	var loc := core_location(engine_location_id)
	var art: Variant = loc.get("art", {})
	if typeof(art) == TYPE_DICTIONARY:
		return String((art as Dictionary).get("scope_key", ""))
	return ""


## Convenience: the named zones a location declares in the profile, or [] if absent.
func core_location_zones(engine_location_id: String) -> Array:
	var loc := core_location(engine_location_id)
	var zones: Variant = loc.get("zones", [])
	return zones if typeof(zones) == TYPE_ARRAY else []


# ---------------------------------------------------------------------------
# Godot renderer-profile accessors (renderer_profiles.godot.*).
# ---------------------------------------------------------------------------

## The per-backdrop layout block for an art scope-key:
## {zone_anchors:{<zone>:[x,y]}, walk_polygon_ref, depth_baseline_y}. Empty {} if
## the scope has no layout entry — WorldView then lays zones out procedurally and
## uses a default depth baseline.
func godot_backdrop_layout(scope_key: String) -> Dictionary:
	if scope_key == "":
		return {}
	var layouts: Variant = _godot_block().get("backdrop_layout", {})
	if typeof(layouts) != TYPE_DICTIONARY:
		return {}
	var entry: Variant = (layouts as Dictionary).get(scope_key, {})
	return entry if typeof(entry) == TYPE_DICTIONARY else {}


## The sprite-sheet metadata for an engine actor id (sheet_scope_key, facings,
## facing_order, cols/rows, cell_w/h, anchor, ...). Empty {} if unmapped. Consumed
## by #1054's CharacterToken — exposed here so the profile stays the single source.
func godot_actor_sheet(engine_actor_id: String) -> Dictionary:
	if engine_actor_id == "":
		return {}
	var sheets: Variant = _godot_block().get("actor_sheets", {})
	if typeof(sheets) != TYPE_DICTIONARY:
		return {}
	var entry: Variant = (sheets as Dictionary).get(engine_actor_id, {})
	return entry if typeof(entry) == TYPE_DICTIONARY else {}


## Build the FULL slicing manifest for an engine actor's SERVED atlas, in the EXACT
## shape CharacterToken.set_manifest() consumes (frame:{w,h}, facing_order, anchor:{x,y},
## fps, animations:{<name>:{start,count,loop}}, kind, projection). The SERVED atlas is
## ONLY a PNG (fetched via /image?scope=<sheet_scope_key>) — there is NO served sheet.json,
## so the slicing layout MUST come from the render-profile actor_sheet here (#1063 part 2).
## Returns {} when the actor is unmapped OR the actor_sheet carries no `animations` table
## (an incomplete profile) — WorldView then keeps the committed res:// placeholder.
func godot_served_manifest(engine_actor_id: String) -> Dictionary:
	var meta := godot_actor_sheet(engine_actor_id)
	if meta.is_empty():
		return {}
	var anims_v: Variant = meta.get("animations", {})
	if typeof(anims_v) != TYPE_DICTIONARY or (anims_v as Dictionary).is_empty():
		# Without an animations table we cannot slice — fall back to the committed sheet.
		return {}

	var cw := int(meta.get("cell_w", 128))
	var ch := int(meta.get("cell_h", 128))

	# facing_order: pass through if the profile declares it; else the locked default.
	var fo_v: Variant = meta.get("facing_order", DEFAULT_FACING_ORDER)
	var facing_order: Array = (fo_v as Array).duplicate() if typeof(fo_v) == TYPE_ARRAY and not (fo_v as Array).is_empty() else DEFAULT_FACING_ORDER.duplicate()

	# anchor: profile carries [x, y]; CharacterToken wants {x, y}.
	var ax := cw / 2.0
	var ay := float(ch)
	var anch_v: Variant = meta.get("anchor", null)
	if typeof(anch_v) == TYPE_ARRAY and (anch_v as Array).size() >= 2:
		var a: Array = anch_v
		ax = float(a[0])
		ay = float(a[1])

	# animations: pass the table through verbatim (already {name:{start,count,loop}}).
	var animations: Dictionary = anims_v

	return {
		"kind": String(meta.get("kind", "character")),
		"projection": String(meta.get("projection", "dimetric-2to1")),
		"image": "sheet.png",
		"frame": {"w": cw, "h": ch},
		"facing_order": facing_order,
		"anchor": {"x": ax, "y": ay},
		"fps": float(meta.get("fps", 10)),
		"animations": animations,
	}


## The dimetric projection block (kind/tile_ratio/angle_deg). Falls back to the
## ISO-PROJECTION.md lock if the profile omits it.
func projection() -> Dictionary:
	var proj: Variant = _godot_block().get("projection", {})
	if typeof(proj) == TYPE_DICTIONARY and not (proj as Dictionary).is_empty():
		return proj
	return DEFAULT_PROJECTION.duplicate(true)


## The default facing (rest/post-travel facing). Falls back to "S" (camera-facing).
func default_facing() -> String:
	var f: Variant = _godot_block().get("default_facing", "")
	if typeof(f) == TYPE_STRING and String(f) != "":
		return String(f)
	return DEFAULT_FACING


# ---------------------------------------------------------------------------
# Internals.
# ---------------------------------------------------------------------------

## The renderer_profiles.godot block, or {} if absent.
func _godot_block() -> Dictionary:
	var profiles: Variant = _profile.get("renderer_profiles", {})
	if typeof(profiles) != TYPE_DICTIONARY:
		return {}
	var godot: Variant = (profiles as Dictionary).get("godot", {})
	return godot if typeof(godot) == TYPE_DICTIONARY else {}


## A core sub-array (locations / actors), or [] if absent/malformed.
func _core_array(key: String) -> Array:
	var core: Variant = _profile.get("core", {})
	if typeof(core) != TYPE_DICTIONARY:
		return []
	var arr: Variant = (core as Dictionary).get(key, [])
	return arr if typeof(arr) == TYPE_ARRAY else []


## Inject an inline profile dict (e.g. from a --preview-scene spec) instead of the
## bundled fixture. Additive: the normal _ready() fixture load is UNCHANGED; this
## method replaces _profile for callers that supply their own profile dictionary so a
## preview can inject a profile without the bundled fixture. No-op if called with an
## empty dict (falls back to the already-loaded fixture).
func load_inline(profile: Dictionary) -> void:
	if profile.is_empty():
		return
	_profile = profile
	print("[RenderProfile] load_inline: profile injected (top-level keys=%d)" % _profile.size())


## Load + parse the bundled fixture. Returns {} on any missing/parse failure so a
## profile-less run still renders.
func _load_fixture() -> Dictionary:
	if not FileAccess.file_exists(FIXTURE_PATH):
		push_warning("[RenderProfile] missing fixture: " + FIXTURE_PATH)
		return {}
	var text := FileAccess.get_file_as_string(FIXTURE_PATH)
	var parsed: Variant = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		push_warning("[RenderProfile] unparseable fixture: " + FIXTURE_PATH)
		return {}
	return parsed
