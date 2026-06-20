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
## SCOPE (#1053): backdrop + walkmask floor polygon + deterministic zone markers.
## SCOPE (#1054, this layer): also spawn ONE directional CharacterToken for
## character.party[0] + ONE static PropActor (pillar) into the YSortLayer, both
## foot-anchored so Y-sort occlusion just works in #1055. Click-to-move / the
## FacingResolver derivation / the occlusion *test* is #1055 — the token already
## supports set_facing()/set_zone_target() so that issue only wires input.
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
##   └─ YSortLayer (Node2D, y_sort_enabled) — CharacterToken(s) + PropActor(s) (#1054),
##                                            depth-sorted by foot-y.

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

## #1054 actor/prop scenes. CharacterToken builds its SpriteFrames from a manifest;
## PropActor is a static foot-anchored occluder.
const CharacterTokenScene := preload("res://scenes/CharacterToken.tscn")
const PropActorScript := preload("res://scenes/PropActor.gd")

## Committed CC0 placeholder asset roots (res://). The slice loads sheet.png +
## sheet.json directly from here when no live engine /image serves the sprite scope —
## so the standalone fixture run shows a real directional token, not just markers.
const CHAR_ASSET_ROOT := "res://assets/characters/"
const PILLAR_PROP_DIR := "res://assets/props/pillar/"

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

## #1054 — spawned tokens reconciled by engine_actor_id across ticks (no leaks).
## actor_id -> CharacterToken. Today we only spawn party[0], but the dictionary
## keeps the reconcile contract right for when the party grows.
var _tokens: Dictionary = {}
## #1063 part 2 — sprite-atlas scope_key -> actor_id, so when ImageResolver emits
## texture_ready for a SERVED sprite atlas (/image?scope=sprite-<name>) we know which
## token to re-set_manifest. Distinct namespace from the backdrop scope (scene-*), so
## the shared texture_ready handler dispatches by which map the scope is in.
var _sheet_scope_actor: Dictionary = {}
## The single static pillar prop (spawned once, repositioned per tick).
var _pillar: PropActor = null

## #1055 — the FACING-derivation locked order (ISO-PROJECTION.md). Passed to
## FacingResolver.octant() when a token's zone changes between snapshots / on a
## zone_move beat. Facing is 100% renderer-derived (the engine has no facing field).
## Typed PackedStringArray so it slots straight into octant(order: PackedStringArray).
const FACING_ORDER: PackedStringArray = ["S", "SE", "E", "NE", "N", "NW", "W", "SW"]

## #1055 — last KNOWN screen position per token (actor_id -> Vector2), captured on
## each placement, so the NEXT placement can derive the move vector for facing.
## Distinct from the live node `position` (which the tween animates mid-move).
var _token_prev_pos: Dictionary = {}
## #1055 — last engine location id we projected, to distinguish a `travel` (location
## change → reset facing to default) from an in-scene `move_to_zone` (derive facing).
var _prev_location_id: String = ""
## #1055 — the raw atlas dict from the latest snapshot, so InputController can read
## travel_options / the live-writable guard (can_act / is_live_view) WITHOUT the
## renderer caching any game state of its own.
var _atlas_cache: Dictionary = {}


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

	# Cache the raw atlas so InputController can read travel_options + the
	# live-writable guard without the renderer holding any game state of its own.
	_atlas_cache = atlas

	# --- current location (id + display name) from the read-only atlas ---
	_prev_location_id = _location_id
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

	# --- #1054: spawn/reconcile the lead party token + the static pillar prop into
	# the Y-sorted layer (foot-anchored, so #1055's occlusion sorts by foot-y) ---
	_reconcile_actors(character)
	_place_pillar()

	# DIAGNOSTIC (validation proof): location, zone-marker count, backdrop status.
	# Reports the RESOLVED art scope only when a real /image texture is in use;
	# otherwise "fallback" (the procedural gradient — also the standalone case
	# where the scope is mapped but no live engine serves the art).
	var backdrop_status := "fallback"
	if _backdrop_is_resolved and scope != "":
		backdrop_status = scope
	print("[WorldView] location=%s zones=%d markers placed; backdrop=%s" % [
		_location_name, _zone_count, backdrop_status])

	# DIAGNOSTIC (#1054 validation proof): per spawned token, its SpriteFrames anim
	# count (expect 32 for the 4-anim x 8-facing placeholder), active anim+facing, a
	# sliced-frame sanity (walk_S → 8 frames), and that it is a child of YSortLayer.
	for actor_id in _tokens.keys():
		var tok: CharacterToken = _tokens[actor_id]
		if not is_instance_valid(tok):
			continue
		var in_ysort := tok.get_parent() == _ysort
		print("[CharacterToken] actor=%s anims=%d facing=%s anim=%s walk_S_frames=%d (%s)" % [
			tok.engine_actor_id, tok.animation_count(), tok.facing(), tok.anim(),
			tok.clip_frame_count("walk_S"),
			"in YSortLayer" if in_ysort else "NOT in YSortLayer"])
	if _pillar != null and is_instance_valid(_pillar):
		var pillar_in_ysort := _pillar.get_parent() == _ysort
		print("[PropActor] prop=%s y=%.1f (%s)" % [
			_pillar.prop_id, _pillar.position.y,
			"in YSortLayer" if pillar_in_ysort else "NOT in YSortLayer"])


# ---------------------------------------------------------------------------
# Replay stub (#1055 / combat). Connected to SurfaceClient.events_appended by
# Main. Real Action-Replay (combat token motion + facing from target_fk) is a
# later issue — for now we accept and ignore so the signal interface is wired.
# ---------------------------------------------------------------------------
func enqueue_replay(records: Array) -> void:
	# #1055: honor `zone_move` beats for facing derivation (renderer-derived). Full
	# Action-Replay (combat token motion + facing from target_fk) is still a later
	# issue; everything else is accepted-and-ignored so the wiring stays verifiable.
	for rec in records:
		if typeof(rec) != TYPE_DICTIONARY:
			continue
		var r: Dictionary = rec
		if String(r.get("kind", "")) != "zone_move":
			continue
		var actor_id := String(r.get("actor", r.get("actor_id", "")))
		var zone_name := String(r.get("zone", r.get("target", "")))
		if actor_id != "" and zone_name != "":
			apply_zone_move(actor_id, zone_name)


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


## The spawned CharacterToken for an engine actor id (null if none). #1055 uses this
## to drive set_zone_target / set_facing on click. Exposed for validation too.
func token_for(engine_actor_id: String) -> CharacterToken:
	return _tokens.get(engine_actor_id, null)


## The static pillar prop (null until spawned). Exposed for #1055's occlusion test.
func pillar_prop() -> PropActor:
	return _pillar


# ---------------------------------------------------------------------------
# #1055 — input-facing query surface (consumed by InputController). All of these
# are PURE reads of the already-projected scene; none mutate or assert state.
# ---------------------------------------------------------------------------

## True if `world_pos` (WorldView-local px) is inside the walkmask floor polygon.
## InputController NEVER asserts a coordinate outside this region.
func is_walkable(world_pos: Vector2) -> bool:
	if _floor_poly == null:
		return false
	var poly := _floor_poly.polygon
	if poly.size() < 3:
		return false
	# FloorPolygon is positioned at the WalkmaskLayer origin (untransformed in the
	# scene), so its `polygon` points are already in WorldView-local space.
	return Geometry2D.is_point_in_polygon(world_pos, poly)


## The NEAREST named zone to `world_pos` by squared distance to its anchor, or ""
## if there are no zones this tick. The #1055 click→zone snap.
func nearest_zone(world_pos: Vector2) -> String:
	var best := ""
	var best_d := INF
	for zn in _zone_screen.keys():
		var p: Vector2 = _zone_screen[zn]
		var d := world_pos.distance_squared_to(p)
		if d < best_d:
			best_d = d
			best = zn
	return best


## The atlas `travel_options` array from the latest snapshot (or [] if absent).
## Each option is a Dictionary; it MAY carry a verbatim `move` intent the renderer
## must emit unchanged, else {to/target} the renderer wraps into a `travel` intent.
func travel_options() -> Array:
	var t: Variant = _atlas_cache.get("travel_options", [])
	return t if typeof(t) == TYPE_ARRAY else []


## True when the current view is LIVE-WRITABLE per the atlas (`can_act` /
## `is_live_view`). If neither field is present, treat absent as writable (the
## FIXTURE/standalone case so the demo works) — InputController separately knows it
## did not reach a real engine (SurfaceClient is in FIXTURE mode).
func is_live_writable() -> bool:
	if _atlas_cache.has("can_act"):
		return bool(_atlas_cache["can_act"])
	if _atlas_cache.has("is_live_view"):
		return bool(_atlas_cache["is_live_view"])
	return true


## The id of the actor/prop whose pick body contains `world_pos`, or "" if none.
## Front-most (largest foot-y) wins so a click on overlapping bodies selects the
## one nearest the camera. Used for the #1055 inspect-click.
func pick_actor_at(world_pos: Vector2) -> String:
	var hit_id := ""
	var hit_y := -INF
	for actor_id in _tokens.keys():
		var tok: CharacterToken = _tokens[actor_id]
		if not is_instance_valid(tok):
			continue
		if _token_contains(tok, world_pos) and tok.position.y > hit_y:
			hit_id = actor_id
			hit_y = tok.position.y
	if _pillar != null and is_instance_valid(_pillar):
		if _prop_contains(_pillar, world_pos) and _pillar.position.y > hit_y:
			hit_id = _pillar.prop_id
			hit_y = _pillar.position.y
	return hit_id


## #1055 — apply a `zone_move` events beat for ONE actor (facing-derive + walk to the
## named zone's anchor). Renderer-derived facing; no engine facing. Called from the
## events-replay path; a no-op if the actor/zone is unknown this tick.
func apply_zone_move(actor_id: String, zone_name: String) -> void:
	var tok: CharacterToken = _tokens.get(actor_id, null)
	if tok == null or not is_instance_valid(tok):
		return
	var dest: Vector2 = _zone_screen.get(zone_name, Vector2.ZERO)
	if dest == Vector2.ZERO and not _zone_screen.has(zone_name):
		return
	var from_pos: Vector2 = _token_prev_pos.get(actor_id, tok.position)
	_walk_token_to(tok, actor_id, from_pos, dest)


## Hit-test a CharacterToken's Area2D pick body against a WorldView-local point.
func _token_contains(tok: CharacterToken, world_pos: Vector2) -> bool:
	var picker := tok.get_node_or_null("Picker") as Area2D
	if picker == null:
		return false
	var shape_node := picker.get_node_or_null("PickerShape") as CollisionShape2D
	if shape_node == null or shape_node.shape == null:
		return false
	var rect := shape_node.shape as RectangleShape2D
	if rect == null:
		return false
	# Convert the world point into the shape's local frame (token pos + shape pos).
	var local := world_pos - tok.position - shape_node.position
	var half := rect.size * 0.5
	return absf(local.x) <= half.x and absf(local.y) <= half.y


## Hit-test the pillar prop's body cell against a WorldView-local point. The prop
## origin is its foot; the body cell spans -anchor..(-anchor+frame) from origin.
func _prop_contains(prop: PropActor, world_pos: Vector2) -> bool:
	var sprite := prop.get_node_or_null("Sprite") as Sprite2D
	if sprite == null or sprite.texture == null:
		return false
	var sz := sprite.texture.get_size()
	# sprite.offset == -anchor, so the cell top-left (relative to origin) is offset.
	var top_left := prop.position + sprite.offset
	return world_pos.x >= top_left.x and world_pos.x <= top_left.x + sz.x \
		and world_pos.y >= top_left.y and world_pos.y <= top_left.y + sz.y


# ---------------------------------------------------------------------------
# #1054 — actor tokens + the static pillar prop, in the Y-sorted layer.
# ---------------------------------------------------------------------------

## Spawn/reconcile ONE CharacterToken for character.party[0] (the lead actor),
## keyed by engine_actor_id so it is built ONCE and only repositioned thereafter.
## Tokens for actors no longer present are freed (reconcile = no leaks across ticks).
## Placed at a FOREGROUND zone (the front-most marker) so it reads near the camera.
func _reconcile_actors(character: Dictionary) -> void:
	var lead := _lead_actor(character)
	var wanted: Dictionary = {}  # actor_id -> true (actors that should exist this tick)
	# A location change since the last snapshot is a `travel` — reset facing to the
	# rest/default facing rather than deriving a walk direction (ISO-PROJECTION.md).
	var traveled := _prev_location_id != "" and _prev_location_id != _location_id

	if not lead.is_empty():
		var actor_id := String(lead.get("id", ""))
		if actor_id != "":
			wanted[actor_id] = true
			var tok: CharacterToken = _tokens.get(actor_id, null)
			var is_new := tok == null
			if is_new:
				tok = _spawn_token(actor_id)
				if tok != null:
					_tokens[actor_id] = tok
			if tok != null:
				var target_pos := _foreground_pos()
				_reconcile_token_motion(tok, actor_id, target_pos, is_new, traveled)

	# Free tokens whose actor left the party (no leaks).
	for existing_id in _tokens.keys():
		if not wanted.has(existing_id):
			var stale: CharacterToken = _tokens[existing_id]
			if is_instance_valid(stale):
				stale.queue_free()
			_tokens.erase(existing_id)
			_token_prev_pos.erase(existing_id)
			# Drop any served-atlas scope mapping pointing at the freed token (#1063).
			for sc in _sheet_scope_actor.keys():
				if String(_sheet_scope_actor[sc]) == String(existing_id):
					_sheet_scope_actor.erase(sc)


## #1055 — drive ONE token to its new screen position with renderer-derived facing.
## Decision table (facing is 100% renderer-derived; the engine has no facing field):
##   - new token        → place instantly at default facing, idle (no walk-in).
##   - traveled (loc Δ) → place instantly, reset to default_facing, idle.
##   - same position    → hold last facing, stay idle (static).
##   - zone moved       → derive facing = octant(prev→new) via FacingResolver, play
##                        `walk` during the set_zone_target tween, return to `idle`
##                        on arrival. Y updates progressively so Y-sort re-orders.
func _reconcile_token_motion(tok: CharacterToken, actor_id: String, target_pos: Vector2, is_new: bool, traveled: bool) -> void:
	var prev_pos: Vector2 = _token_prev_pos.get(actor_id, target_pos)

	if is_new:
		tok.place_at(target_pos)
		tok.set_facing(FacingResolver.default_facing)
		tok.set_anim("idle")
		_token_prev_pos[actor_id] = target_pos
		return

	if traveled:
		# Location change → snap into the new scene at the rest facing.
		tok.place_at(target_pos)
		tok.set_facing(FacingResolver.default_facing)
		tok.set_anim("idle")
		_token_prev_pos[actor_id] = target_pos
		return

	if prev_pos.distance_squared_to(target_pos) < 0.5:
		# No zone change — hold the last facing, stay idle (static).
		_token_prev_pos[actor_id] = target_pos
		return

	# Zone changed within the scene → derive an 8-way facing and walk there.
	_walk_token_to(tok, actor_id, prev_pos, target_pos)


## Derive facing from prev→new, play `walk` for the tween, return to `idle` on
## arrival. Shared by the snapshot reconcile and the `zone_move` events beat.
func _walk_token_to(tok: CharacterToken, actor_id: String, from_pos: Vector2, to_pos: Vector2) -> void:
	var facing := FacingResolver.octant(from_pos, to_pos, FACING_ORDER)
	tok.set_facing(facing)
	tok.set_anim("walk")
	tok.set_zone_target(to_pos)
	# Return to idle when the move-tween finishes (tween length == MOVE_TWEEN_SEC).
	var idle_timer := get_tree().create_timer(CharacterToken.MOVE_TWEEN_SEC)
	idle_timer.timeout.connect(func():
		if is_instance_valid(tok):
			tok.set_anim("idle"))
	_token_prev_pos[actor_id] = to_pos
	print("[Facing] move %s => %s" % [actor_id, facing])


## Build a CharacterToken for an actor: resolve its committed sheet (sheet.png +
## sheet.json), build it, add it to YSortLayer. Returns null if no sheet resolves.
##
## #1063 part 2 — ADDITIVE + FALLBACK-SAFE served-atlas layer: the token is ALWAYS
## built from the committed res:// placeholder first (so a missing served atlas is
## EXACTLY today's behavior). We THEN try the live engine's SERVED final atlas via
## /image?scope=<sheet_scope_key>: if it is already cached, swap it in now; if it has
## not been tried (and is not known-404), kick an async resolve() — _on_texture_ready
## swaps it in later. The slicing layout for the served PNG comes from the
## render-profile actor_sheet (there is NO served sheet.json), mirroring _swap_backdrop.
func _spawn_token(actor_id: String) -> CharacterToken:
	var resolved := _resolve_character_sheet(actor_id)
	if resolved.is_empty():
		push_warning("[WorldView] no committed sprite sheet for actor=%s" % actor_id)
		return null
	var tok: CharacterToken = CharacterTokenScene.instantiate()
	tok.engine_actor_id = actor_id
	tok.name = "Token_" + actor_id
	_ysort.add_child(tok)
	tok.set_manifest(resolved["manifest"], resolved["texture"])
	# Try to upgrade to the SERVED final atlas (no-op when none is served).
	_try_serve_sprite_atlas(tok, actor_id)
	return tok


## #1063 part 2 — attempt to swap a token's committed placeholder for the SERVED final
## atlas. Mirrors _swap_backdrop's cached/missing/resolve structure. The committed
## token is already built; this only UPGRADES it when a served atlas exists. No-op when
## the actor is unmapped, the profile lacks a slicing layout, or the scope is absent.
func _try_serve_sprite_atlas(tok: CharacterToken, actor_id: String) -> void:
	var meta := RenderProfile.godot_actor_sheet(actor_id)
	var scope := String(meta.get("sheet_scope_key", ""))
	if scope == "":
		return
	# The served PNG has no sheet.json — build the slicing manifest from the profile.
	var served_manifest := RenderProfile.godot_served_manifest(actor_id)
	if served_manifest.is_empty():
		# Incomplete profile (no animations table) — keep the committed placeholder.
		return
	# Remember scope -> actor so _on_texture_ready can find the token to re-slice.
	_sheet_scope_actor[scope] = actor_id

	var cached := ImageResolver.get_cached(scope)
	if cached != null:
		# Already fetched — swap the served atlas in now (re-call set_manifest).
		tok.set_manifest(served_manifest, cached)
		return
	if ImageResolver.is_missing(scope):
		# Definitively absent (404) — keep the committed placeholder (today's behavior).
		return
	# Untried/loading: keep the placeholder now; _on_texture_ready upgrades it later.
	ImageResolver.resolve(scope)


## Spawn (once) + position the static pillar prop at a MID-depth zone marker so
## #1055 can prove occlusion both ways (token in front of / behind the pillar).
func _place_pillar() -> void:
	if _pillar == null:
		var resolved := _load_sheet_dir(PILLAR_PROP_DIR)
		if resolved.is_empty():
			return
		_pillar = PropActorScript.new()
		_pillar.prop_id = "pillar"
		_pillar.name = "Prop_pillar"
		_ysort.add_child(_pillar)
		_pillar.set_manifest(resolved["manifest"], resolved["texture"])
	_pillar.place_at(_mid_depth_pos())


## The foreground (near-camera) screen position: the largest-Y zone anchor (front
## marker), else a sensible bottom-center point. Tokens placed here read up-front.
func _foreground_pos() -> Vector2:
	var best := Vector2.ZERO
	var found := false
	for zn in _zone_screen.keys():
		var p: Vector2 = _zone_screen[zn]
		if not found or p.y > best.y:
			best = p
			found = true
	if found:
		return best
	var vp := _viewport_size()
	return Vector2(vp.x * 0.5, vp.y * 0.82)


## A MID-depth screen position for the pillar: the median-Y zone anchor (so a token
## at the foreground sits in FRONT of it and one further back sits BEHIND it). Falls
## back to viewport mid if no anchors exist.
func _mid_depth_pos() -> Vector2:
	var ys: Array = []
	var pts: Array = []
	for zn in _zone_screen.keys():
		var p: Vector2 = _zone_screen[zn]
		pts.append(p)
		ys.append(p.y)
	if pts.size() >= 1:
		# Pick the anchor whose Y is the median (stable mid-depth choice).
		pts.sort_custom(func(a, b): return a.y < b.y)
		var mid_idx := pts.size() / 2
		# Nudge it left a touch so it doesn't perfectly overlap the foreground token.
		var p: Vector2 = pts[mid_idx]
		return p + Vector2(-70.0, 0.0)
	var vp := _viewport_size()
	return Vector2(vp.x * 0.42, vp.y * 0.65)


## character.party[0] as a Dictionary, or {} if the party is empty/malformed.
func _lead_actor(character: Dictionary) -> Dictionary:
	var party: Variant = character.get("party", [])
	if typeof(party) != TYPE_ARRAY or (party as Array).is_empty():
		return {}
	var first: Variant = (party as Array)[0]
	return first if typeof(first) == TYPE_DICTIONARY else {}


# ---------------------------------------------------------------------------
# Committed sprite-sheet resolution. PREFER the committed CC0 placeholder under
# res://assets/characters/<slug>/ (load sheet.png + sheet.json directly) so the
# standalone fixture run renders a real directional token. The live engine /image
# path (keyed by the RenderProfile actor_sheets sheet_scope_key) layers in later
# without changing this API.
# ---------------------------------------------------------------------------

## Resolve a character actor's committed sheet to {manifest, texture}, or {} if none.
## Candidate dirs (first hit wins): the actor's RenderProfile sheet_scope_key (e.g.
## "sprite-aubree-iso8" → "aubree"), then the actor id with a "char-" prefix
## stripped (e.g. "char-aubree" → "aubree"), under res://assets/characters/.
func _resolve_character_sheet(actor_id: String) -> Dictionary:
	for slug in _character_slug_candidates(actor_id):
		var dir: String = CHAR_ASSET_ROOT + String(slug) + "/"
		var resolved := _load_sheet_dir(dir)
		if not resolved.is_empty():
			return resolved
	return {}


## Ordered, de-duplicated slug candidates for an actor's committed asset dir.
func _character_slug_candidates(actor_id: String) -> Array:
	var out: Array = []
	var meta := RenderProfile.godot_actor_sheet(actor_id)
	var scope := String(meta.get("sheet_scope_key", ""))
	if scope != "":
		# "sprite-aubree-iso8" → "aubree" (strip a leading "sprite-" and a trailing
		# "-iso8"/"-isoN" projection suffix; keep the middle as the slug).
		var s := scope
		if s.begins_with("sprite-"):
			s = s.substr("sprite-".length())
		var dash := s.rfind("-")
		if dash > 0 and s.substr(dash + 1).begins_with("iso"):
			s = s.substr(0, dash)
		if s != "":
			out.append(s)
	# Actor-id derived slug: "char-aubree" → "aubree".
	var aid := actor_id
	if aid.begins_with("char-"):
		aid = aid.substr("char-".length())
	if aid != "" and not out.has(aid):
		out.append(aid)
	return out


## Load {manifest, texture} from a committed asset dir holding sheet.json + the PNG
## it names (default sheet.png). Returns {} if either is missing/unparseable.
func _load_sheet_dir(dir: String) -> Dictionary:
	var json_path := dir + "sheet.json"
	if not FileAccess.file_exists(json_path):
		return {}
	var text := FileAccess.get_file_as_string(json_path)
	var parsed: Variant = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		push_warning("[WorldView] unparseable sheet manifest: " + json_path)
		return {}
	var manifest: Dictionary = parsed
	var image_name := String(manifest.get("image", "sheet.png"))
	var png_path := dir + image_name
	if not ResourceLoader.exists(png_path) and not FileAccess.file_exists(png_path):
		push_warning("[WorldView] sheet image missing: " + png_path)
		return {}
	var tex: Texture2D = load(png_path)
	if tex == null:
		push_warning("[WorldView] sheet image failed to load: " + png_path)
		return {}
	return {"manifest": manifest, "texture": tex}


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
	if texture == null:
		return
	# #1063 part 2 — a SERVED sprite atlas resolved: re-slice the token that asked for
	# it from the render-profile layout (distinct scope namespace from the backdrop, so
	# this branch and the backdrop branch never both fire for one scope).
	if _sheet_scope_actor.has(scope):
		_apply_served_sprite(scope, texture)
		return
	# Only swap if this is still the location we want (the snapshot may have moved on).
	if scope == _pending_backdrop_scope:
		_apply_texture_backdrop(texture)


## #1063 part 2 — apply a resolved served sprite atlas to its token by re-building the
## SpriteFrames from the render-profile slicing layout (set_manifest is re-callable: it
## rebuilds _frames from scratch). No-op if the token was freed (actor left the party)
## or the profile lost its layout — the committed placeholder simply stays.
func _apply_served_sprite(scope: String, texture: Texture2D) -> void:
	var actor_id := String(_sheet_scope_actor.get(scope, ""))
	if actor_id == "":
		return
	var tok: CharacterToken = _tokens.get(actor_id, null)
	if tok == null or not is_instance_valid(tok):
		return
	var served_manifest := RenderProfile.godot_served_manifest(actor_id)
	if served_manifest.is_empty():
		return
	# Capture the current render state so the atlas swap is seamless (set_manifest
	# resets to idle@default_facing otherwise).
	var keep_facing := tok.facing()
	var keep_anim := tok.anim()
	tok.set_manifest(served_manifest, texture)
	tok.set_facing(keep_facing)
	tok.set_anim(keep_anim)
	print("[CharacterToken] actor=%s SERVED atlas applied scope=%s anims=%d" % [
		actor_id, scope, tok.animation_count()])


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
