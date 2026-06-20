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
