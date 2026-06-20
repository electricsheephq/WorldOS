extends Node2D
## WorldView — the snapshot → scene projection (#1053).
##
## CONTRACT ROLE (mirrors viewer/openworlds/render/renderer-backdrop.js): a PURE
## projection of ONE snapshot. Every tick it rebuilds the visible scene from the
## read-only surfaces; it owns ZERO game state and persists nothing across ticks.
## It IGNORES every surface `position.x/y` — all screen positions are DERIVED from
## named ZONES via the render-profile's zone_anchors (or a deterministic procedural
## fallback). This keeps the engine the sole writer of WHERE things are (zones),
## and the renderer the sole owner of HOW that maps to pixels (the walkmask + the
## projection — see ISO-PROJECTION.md and #444 walkmask-is-renderer-owned).
##
## SCOPE (#1053): backdrop + walkmask floor polygon + deterministic zone markers
## ONLY. CharacterToken / sprite art is #1054 (it will populate the empty
## YSortLayer created here); click-to-move / facing / Y-sort occlusion is #1055.
##
## NODE TREE (built in _ready):
##   WorldView (Node2D)
##   ├─ BackdropPlane (Sprite2D, z=-100, not y-sorted) — painted location art or a
##   │                                                    procedural gradient fallback.
##   ├─ WalkmaskLayer (Node2D, z=-50)
##   │   ├─ FloorPolygon (Polygon2D) — the procedural perspective trapezoid (the
##   │   │                             clickable walkable region in #1055).
##   │   └─ ZoneMarkers (Node2D)      — one Marker2D + faint Label per named zone,
##   │                                  laid out DETERMINISTICALLY.
##   └─ YSortLayer (Node2D, y_sort_enabled) — EMPTY now; CharacterToken lands here (#1054).

## Backdrop trapezoid geometry — mirrors renderer-backdrop.js floorPolygon():
## the floor is inset at the horizon (narrow/far) and near-full-width at the bottom
## (wide/near), reading as a dimetric stage. These are the same proportions as the
## reference renderer (inset 0.22 of width; ~horizonY of height for the back edge).
const FLOOR_INSET_FRAC := 0.22       ## horizontal inset of the back (far) edge
const FLOOR_FRONT_MARGIN := 8.0      ## px margin so the front edge isn't flush to the viewport edge
## Default depth baseline (fraction of viewport height where the floor's back edge
## sits) when the profile omits `depth_baseline_y`. Matches renderer-backdrop.js
## DEFAULT_HORIZON ≈ 0.45 used for the floor's top edge.
const DEFAULT_DEPTH_BASELINE := 0.45
## Deterministic depth bands zones step back→front along, mirroring
## renderer-backdrop.js DEFAULT_DEPTH_BANDS. Used ONLY for the procedural fallback
## (when the profile has no zone_anchors). Fractions of viewport height.
const PROCEDURAL_DEPTH_BANDS := [0.55, 0.7, 0.85]

## Cosmetic colors (kept faint — this is a presentation underlay, not chrome).
const FLOOR_FILL := Color(0.23, 0.27, 0.345, 0.16)
const FLOOR_OUTLINE := Color(0.56, 0.71, 0.85, 0.20)
const ZONE_RING := Color(0.62, 0.71, 0.80, 0.30)
const ZONE_LABEL := Color(0.68, 0.75, 0.81, 0.85)

@onready var _backdrop: Sprite2D = $BackdropPlane
@onready var _floor_poly: Polygon2D = $WalkmaskLayer/FloorPolygon
@onready var _zone_markers: Node2D = $WalkmaskLayer/ZoneMarkers
## Bound (though unused in #1053) to assert the empty YSortLayer exists — it is the
## home CharacterToken lands in for #1054. Do not remove the node.
@onready var _ysort: Node2D = $YSortLayer

## Last-resolved facts, exposed for #1054/#1055.
var _location_id: String = ""
var _location_name: String = "<unknown>"
var _zone_count: int = 0
## zone name -> screen Vector2 (the deterministic anchor), for #1054 token placement
## and #1055 click→zone snapping. Rebuilt every apply_snapshot.
var _zone_screen: Dictionary = {}

## The art scope we last asked the ImageResolver for, so the texture_ready signal
## only swaps the backdrop when it is still the current location's art.
var _pending_backdrop_scope: String = ""
## True when the BackdropPlane currently shows a real resolved /image texture;
## false when it shows the procedural gradient fallback. (For the diagnostic.)
var _backdrop_is_resolved: bool = false


func _ready() -> void:
	# Art arrives asynchronously through the /image bridge; swap the backdrop the
	# moment its scope resolves (if it is still the one we want).
	ImageResolver.texture_ready.connect(_on_texture_ready)
	# Draw an initial procedural backdrop so there is never an empty frame before
	# the first snapshot lands.
	_apply_procedural_backdrop()


# ---------------------------------------------------------------------------
# The one entry point: project a snapshot into the scene. Connected to
# SurfaceClient.snapshot_updated by Main. Rebuilds deterministically by diff so no
# nodes leak across ticks. IGNORES any position.x/y on the surfaces.
# ---------------------------------------------------------------------------
func apply_snapshot(atlas: Dictionary, combat: Dictionary, character: Dictionary) -> void:
	var in_combat := bool(combat.get("active", false))

	# --- current location (id + display name) from the read-only atlas ---
	_location_id = _resolve_location_id(atlas)
	_location_name = _resolve_location_name(atlas)

	# --- backdrop: prefer profile art scope for this location, else atlas-implied ---
	var scope := RenderProfile.core_location_scope(_location_id)
	_swap_backdrop(scope)

	# --- floor trapezoid (the walkmask), sized from the viewport + profile baseline ---
	var vp := _viewport_size()
	var baseline := _depth_baseline(scope)
	_rebuild_floor(vp, baseline)

	# --- deterministic zone markers ---
	var zones := _current_zones(atlas, combat, in_combat)
	_rebuild_zone_markers(zones, scope, vp, baseline)
	_zone_count = zones.size()

	# DIAGNOSTIC (validation proof): location, zone-marker count, backdrop status.
	# Reports the RESOLVED art scope only when a real /image texture is in use;
	# otherwise "fallback" (the procedural gradient — also the standalone case
	# where the scope is mapped but no live engine serves the art).
	var backdrop_status := "fallback"
	if _backdrop_is_resolved and scope != "":
		backdrop_status = scope
	print("[WorldView] location=%s zones=%d markers placed; backdrop=%s" % [
		_location_name, _zone_count, backdrop_status])


# ---------------------------------------------------------------------------
# Replay stub (#1055 / combat). Connected to SurfaceClient.events_appended by
# Main. Real Action-Replay (combat token motion + facing from target_fk) is a
# later issue — for now we accept and ignore so the signal interface is wired.
# ---------------------------------------------------------------------------
func enqueue_replay(records: Array) -> void:
	# Intentionally a no-op for #1053. Kept so events_appended has a destination and
	# the wiring is verifiable now (the count print stays in Main).
	pass


# ---------------------------------------------------------------------------
# Public accessors for later issues / validation.
# ---------------------------------------------------------------------------
func current_location_name() -> String:
	return _location_name


func zone_marker_count() -> int:
	return _zone_count


## Screen position of a named zone's anchor (Vector2.ZERO if unknown). #1054 places
## tokens here; #1055 snaps a floor click to the nearest of these.
func zone_screen_pos(zone_name: String) -> Vector2:
	return _zone_screen.get(zone_name, Vector2.ZERO)


# ---------------------------------------------------------------------------
# Backdrop.
# ---------------------------------------------------------------------------

## Ask the resolver for `scope`'s texture; if cached, swap now; if missing/empty,
## draw the procedural gradient fallback so there is ALWAYS a backdrop.
func _swap_backdrop(scope: String) -> void:
	_pending_backdrop_scope = scope
	if scope == "":
		_apply_procedural_backdrop()
		return
	var cached := ImageResolver.get_cached(scope)
	if cached != null:
		_apply_texture_backdrop(cached)
		return
	if ImageResolver.is_missing(scope):
		# Definitively absent (404) — commit to the procedural fallback.
		_apply_procedural_backdrop()
		return
	# Untried/loading: show the fallback now; _on_texture_ready swaps it in later.
	_apply_procedural_backdrop()
	ImageResolver.resolve(scope)


func _on_texture_ready(scope: String, texture: Texture2D) -> void:
	# Only swap if this is still the location we want (the snapshot may have moved on).
	if scope == _pending_backdrop_scope and texture != null:
		_apply_texture_backdrop(texture)


## Put a real texture on the BackdropPlane and scale/center it to fill the viewport.
func _apply_texture_backdrop(texture: Texture2D) -> void:
	_backdrop_is_resolved = true
	_backdrop.texture = texture
	_backdrop.centered = true
	var vp := _viewport_size()
	_backdrop.position = vp * 0.5
	var tex_size := texture.get_size()
	if tex_size.x > 0.0 and tex_size.y > 0.0:
		# Fill (cover) the viewport: scale up to the larger axis ratio.
		var sx := vp.x / tex_size.x
		var sy := vp.y / tex_size.y
		var s := maxf(sx, sy)
		_backdrop.scale = Vector2(s, s)


## Procedural painted-ish fallback backdrop: a vertical sky→ground GradientTexture2D
## (mirrors renderer-backdrop.js _renderBackdrop's gradient path) so a missing/404
## art scope still yields a coherent stage. Deterministic — no randomness.
func _apply_procedural_backdrop() -> void:
	_backdrop_is_resolved = false
	var vp := _viewport_size()
	var grad := Gradient.new()
	# Deep dusk-blue sky (top) → dusk slate (horizon) → dark foreground (bottom),
	# offsets roughly matching the reference sky/ground split at ~0.45.
	grad.offsets = PackedFloat32Array([0.0, 0.42, 0.46, 1.0])
	grad.colors = PackedColorArray([
		Color(0.102, 0.153, 0.251),  # 0x1a2740 deep sky
		Color(0.278, 0.314, 0.416),  # 0x47506a dusk horizon
		Color(0.173, 0.165, 0.133),  # 0x2c2a22 lit ground band
		Color(0.078, 0.086, 0.059),  # 0x14160f dark foreground
	])
	var tex := GradientTexture2D.new()
	tex.gradient = grad
	tex.fill = GradientTexture2D.FILL_LINEAR
	tex.fill_from = Vector2(0.5, 0.0)  # top
	tex.fill_to = Vector2(0.5, 1.0)    # bottom (vertical)
	tex.width = maxi(int(vp.x), 1)
	tex.height = maxi(int(vp.y), 1)
	_backdrop.texture = tex
	_backdrop.centered = true
	_backdrop.position = vp * 0.5
	_backdrop.scale = Vector2.ONE


# ---------------------------------------------------------------------------
# Walkmask floor polygon (the perspective trapezoid). Mirrors renderer-backdrop.js
# floorPolygon(): narrow/inset at the back (far) edge, near-full-width at the front
# (near) edge. The back edge sits at `baseline` (fraction of height); the front
# edge is a small margin off the bottom. This is the walkable region (#1055 mask).
# ---------------------------------------------------------------------------
func _rebuild_floor(vp: Vector2, baseline: float) -> void:
	var top := vp.y * baseline
	var inset := vp.x * FLOOR_INSET_FRAC
	# Quad corners in screen space, clockwise from back-left.
	var pts := PackedVector2Array([
		Vector2(inset, top),                                   # back-left  (far)
		Vector2(vp.x - inset, top),                            # back-right (far)
		Vector2(vp.x - FLOOR_FRONT_MARGIN, vp.y - FLOOR_FRONT_MARGIN),  # front-right (near)
		Vector2(FLOOR_FRONT_MARGIN, vp.y - FLOOR_FRONT_MARGIN),         # front-left  (near)
	])
	_floor_poly.polygon = pts
	_floor_poly.color = FLOOR_FILL


# ---------------------------------------------------------------------------
# Deterministic zone markers. Rebuilt by diff each tick (no node leaks, no
# randomness/jitter between ticks). Placement precedence:
#   1. profile backdrop_layout[scope].zone_anchors{<zone>:[x,y]} (normalized 0..1) →
#      multiply by the viewport. This is the AUTHORED, projection-correct anchor.
#   2. ELSE spread zones evenly across the floor trapezoid by a STABLE function of
#      their sorted index (mirrors renderer-backdrop.js zoneMarker(): step back→front
#      along depth bands, x-spread shrinks toward the horizon for perspective).
# ---------------------------------------------------------------------------
func _rebuild_zone_markers(zones: Array, scope: String, vp: Vector2, baseline: float) -> void:
	# Clear previous tick's markers (diff = full rebuild; the set is tiny and the
	# layout is a pure function of the inputs, so a clean rebuild can't leak/drift).
	for child in _zone_markers.get_children():
		child.queue_free()
	_zone_screen.clear()

	var layout := RenderProfile.godot_backdrop_layout(scope)
	var anchors: Dictionary = layout.get("zone_anchors", {}) if typeof(layout.get("zone_anchors", {})) == TYPE_DICTIONARY else {}

	# Stable ordering: sort by name so the procedural index is deterministic across
	# ticks regardless of surface array order.
	var names: Array = []
	for z in zones:
		var zn := _zone_name(z)
		if zn != "":
			names.append(zn)
	names.sort()

	var count := names.size()
	for i in range(count):
		var zn: String = names[i]
		var pos: Vector2
		if anchors.has(zn) and _is_xy(anchors[zn]):
			# (1) authored anchor — normalized [x,y] of the backdrop → screen px.
			var a: Array = anchors[zn]
			pos = Vector2(float(a[0]) * vp.x, float(a[1]) * vp.y)
		else:
			# (2) procedural fallback — deterministic by sorted index.
			pos = _procedural_zone_pos(i, count, vp, baseline)
		_zone_screen[zn] = pos

		var marker := Marker2D.new()
		marker.name = "Zone_%d" % i
		marker.position = pos
		var label := Label.new()
		label.text = zn
		label.add_theme_color_override("font_color", ZONE_LABEL)
		# Offset the label up-left of the anchor so it reads above the foot point.
		label.position = Vector2(-40.0, -26.0)
		marker.add_child(label)
		_zone_markers.add_child(marker)


## Deterministic procedural zone screen-position by sorted index, mirroring
## renderer-backdrop.js zoneMarker(): zones step back→front along depth bands, and
## the horizontal spread widens toward the front (perspective). `baseline` is the
## floor's back-edge fraction (= "horizonY" in the reference).
func _procedural_zone_pos(index: int, count: int, vp: Vector2, baseline: float) -> Vector2:
	var band: float = PROCEDURAL_DEPTH_BANDS[index % PROCEDURAL_DEPTH_BANDS.size()]
	var y := vp.y * band
	# Horizontal position t in [0,1] across the zones, centered when only one.
	var t := float(index) / float(count - 1) if count > 1 else 0.5
	# Depth t: 0 at the floor's back edge (baseline), 1 at the front (bottom).
	var span := maxf(vp.y - vp.y * baseline, 1.0)
	var depth_t := clampf((y - vp.y * baseline) / span, 0.0, 1.0)
	# Half-width of the spread grows with depth (wider near the camera).
	var half_w := (0.30 + 0.18 * depth_t) * vp.x
	var x := vp.x * 0.5 + (t - 0.5) * 2.0 * half_w
	return Vector2(x, y)


# ---------------------------------------------------------------------------
# Read-only surface extraction (shapes mirror viewer/server.py surfaces). The
# renderer derives EVERYTHING from names — never from any position.x/y field.
# ---------------------------------------------------------------------------

## Zone source: combat → combat.zones[]; exploration → atlas current_location zones,
## else the RenderProfile core location zones, else a sane 3-zone default so the
## stage is never empty.
func _current_zones(atlas: Dictionary, combat: Dictionary, in_combat: bool) -> Array:
	if in_combat:
		var cz: Variant = combat.get("zones", [])
		if typeof(cz) == TYPE_ARRAY and not (cz as Array).is_empty():
			return cz
	# Exploration: prefer the atlas's own zones for the current location.
	var az: Variant = atlas.get("zones", [])
	if typeof(az) == TYPE_ARRAY and not (az as Array).is_empty():
		return az
	# Else the render-profile's declared zones for this location.
	var pz := RenderProfile.core_location_zones(_location_id)
	if not pz.is_empty():
		return pz
	return ["the foreground", "the mid-ground", "the rear"]


func _resolve_location_id(atlas: Dictionary) -> String:
	var cur: Variant = atlas.get("current_location", null)
	if typeof(cur) == TYPE_DICTIONARY:
		var c: Dictionary = cur
		if c.has("id"):
			return String(c["id"])
		if c.has("engine_location_id"):
			return String(c["engine_location_id"])
	if atlas.has("current_location_id"):
		return String(atlas["current_location_id"])
	return ""


func _resolve_location_name(atlas: Dictionary) -> String:
	var cur: Variant = atlas.get("current_location", null)
	if typeof(cur) == TYPE_DICTIONARY and (cur as Dictionary).has("name"):
		return String((cur as Dictionary)["name"])
	if atlas.has("current_location_id"):
		return String(atlas["current_location_id"])
	return "<unknown>"


## Normalize a zone entry (string OR {name:...}) to its name; "" if neither.
func _zone_name(z: Variant) -> String:
	if typeof(z) == TYPE_STRING:
		return String(z)
	if typeof(z) == TYPE_DICTIONARY and (z as Dictionary).has("name"):
		return String((z as Dictionary)["name"])
	return ""


## True if v is a [x,y]-shaped array of two numbers.
func _is_xy(v: Variant) -> bool:
	if typeof(v) != TYPE_ARRAY:
		return false
	var a: Array = v
	if a.size() < 2:
		return false
	var t0 := typeof(a[0])
	var t1 := typeof(a[1])
	return (t0 == TYPE_FLOAT or t0 == TYPE_INT) and (t1 == TYPE_FLOAT or t1 == TYPE_INT)


## The floor's back-edge depth baseline (fraction of viewport height) for a
## backdrop scope: the profile's `depth_baseline_y` if present, else the default.
func _depth_baseline(scope: String) -> float:
	var layout := RenderProfile.godot_backdrop_layout(scope)
	var v: Variant = layout.get("depth_baseline_y", null)
	if typeof(v) == TYPE_FLOAT or typeof(v) == TYPE_INT:
		return clampf(float(v), 0.05, 0.95)
	return DEFAULT_DEPTH_BASELINE


## Current viewport size (px). Falls back to the project's window size if the
## viewport isn't ready (e.g. very early headless boot).
func _viewport_size() -> Vector2:
	var vp := get_viewport()
	if vp != null:
		var r := vp.get_visible_rect().size
		if r.x > 0.0 and r.y > 0.0:
			return r
	# Project default window size (headless: a sane non-zero stage).
	return Vector2(
		float(ProjectSettings.get_setting("display/window/size/viewport_width", 1152)),
		float(ProjectSettings.get_setting("display/window/size/viewport_height", 648)))
