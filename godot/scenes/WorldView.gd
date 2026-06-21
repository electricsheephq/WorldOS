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
## #1060 — the DEFAULT committed placeholder slug a combatant without its OWN committed
## asset dir falls back to. Enemies/NPCs have no per-actor sheet (and no served atlas),
## so they render with this real directional placeholder + a team tint (below). Always
## present in the committed tree, so a combat token can ALWAYS be built.
const DEFAULT_CHAR_SLUG := "aubree"

## #1060 — renderer-owned TEAM tint (modulate) so foe vs ally reads at a glance. This is
## pure presentation the renderer owns (the engine ships only the `team` string); it does
## NOT touch state. Allies are left neutral (white = no tint); foes get a hostile red wash.
const TEAM_TINT_ALLY := Color(1.0, 1.0, 1.0, 1.0)        ## neutral — committed sprite as-is
const TEAM_TINT_FOE := Color(1.0, 0.55, 0.5, 1.0)        ## hostile red wash
const TEAM_TINT_NEUTRAL := Color(0.92, 0.92, 1.0, 1.0)   ## faint cool for unknown/other teams

@onready var _backdrop: Sprite2D = $BackdropPlane
@onready var _floor_poly: Polygon2D = $WalkmaskLayer/FloorPolygon
@onready var _zone_markers: Node2D = $WalkmaskLayer/ZoneMarkers
## Bound (though unused in #1053) to assert the empty YSortLayer exists — it is the
## home CharacterToken lands in for #1054. Do not remove the node.
@onready var _ysort: Node2D = $YSortLayer
## #iso-camera — Camera2D for drag-to-pan / scroll-to-zoom / recenter.
@onready var _camera: Camera2D = $SceneCamera

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
## actor_id -> CharacterToken. In exploration we spawn party[0]; in combat (#1060) we
## spawn one token per combat token. Either way the dictionary keeps the reconcile
## contract right (build once, reposition thereafter, free departed actors).
var _tokens: Dictionary = {}
## #1060 — last applied team modulate per spawned token (actor_id -> Color), so a
## re-spawn / re-tint is idempotent and the conformance can read back the tint.
var _token_team_tint: Dictionary = {}
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

## #1060 — Action-Replay beat queue. Beats arrive from /events (via Main →
## enqueue_replay) and must play in `seq` order, ONE AT A TIME, each frame-bounded
## (no overlapping unbounded tweens). We append parsed beats here, sort by seq, and
## drain them serially in _drain_replay (await each beat's bounded duration). The
## renderer NEVER computes a result — it only chooses how to SHOW the engine-decided
## beat. Owns no game state: this is a transient presentation queue, cleared as it drains.
var _replay_queue: Array = []
## True while _drain_replay is running, so a second enqueue_replay just appends to the
## queue rather than starting a second concurrent drain (keeps beats totally ordered).
var _draining: bool = false
## #1060 — the last `seq` we have already enqueued, so re-fetched/duplicate beats (the
## envelope's idempotent-replay guarantee) are dropped rather than animated twice.
var _last_replayed_seq: int = -1


func _ready() -> void:
	# Art arrives asynchronously through the /image bridge; swap the backdrop the
	# moment its scope resolves (if it is still the one we want).
	ImageResolver.texture_ready.connect(_on_texture_ready)
	# Draw an initial procedural backdrop so there is never an empty frame before
	# the first snapshot lands.
	_apply_procedural_backdrop()
	# #iso-camera: place the camera at the viewport centre so it starts centred on the
	# backdrop. position_smoothing is enabled in the .tscn for gentle drift.
	if _camera != null:
		var vp := _viewport_size()
		_camera.position = vp * 0.5


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

	# --- #1054/#1060: spawn/reconcile actor tokens + the static pillar prop into the
	# Y-sorted layer (foot-anchored, so #1055's occlusion sorts by foot-y). In COMBAT
	# we render one token per combat token (team-tinted) at its zone; in exploration we
	# render the lead party token — exactly today's behavior. ---
	_reconcile_actors(character, combat, in_combat)
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
# Action-Replay (#1060 / the #645 envelope). Connected to SurfaceClient.events_appended
# by Main. Each /events record is one animated COMBAT beat in the Action-Replay-envelope
# shape `{ seq, actor_fk, verb, target_fk, result, anim_hint }`
# (docs/roadmap/contracts/action-replay-envelope.md). The renderer plays each beat in
# `seq` order, ONE AT A TIME, frame-bounded — it never decides WHAT happened, only HOW
# to show it. It also stays backward-compatible with the #1055 `zone_move` stub shape
# (`{kind, actor, zone}`) so the earlier wiring keeps working.
#
# CONTRACT (the one invariant): the engine is the sole writer; this is a pure projection
# of committed engine truth. The renderer reads `result` to choose numbers/states to show
# and NEVER recomputes them. Unknown verbs are accepted-and-ignored (a generic no-op) so a
# new envelope verb never crashes an old renderer (additive/back-compat, §Versioning).
# ---------------------------------------------------------------------------
func enqueue_replay(records: Array) -> void:
	# Parse + append every recognizable beat, then (re)start the serial drain. We sort
	# by seq so beats animate in the engine's authoritative order regardless of arrival
	# order (the envelope's total-order-via-seq guarantee).
	for rec in records:
		if typeof(rec) != TYPE_DICTIONARY:
			continue
		var beat := _parse_replay_beat(rec)
		if beat.is_empty():
			continue
		# Drop duplicates (idempotent re-fetch): a beat we've already enqueued by seq is
		# not animated twice. Beats with no seq (the #1055 stub) always pass (seq == -1).
		var seq := int(beat.get("seq", -1))
		if seq >= 0 and seq <= _last_replayed_seq:
			continue
		if seq >= 0:
			_last_replayed_seq = seq
		_replay_queue.append(beat)

	if _replay_queue.is_empty():
		return
	# Stable sort by seq (beats without a seq keep arrival order at the front).
	_replay_queue.sort_custom(func(a, b): return int(a.get("seq", -1)) < int(b.get("seq", -1)))

	if not _draining:
		_drain_replay()


## Normalize one /events record into a beat Dictionary the dispatcher understands, or {}
## if it carries no actor/verb we can animate. Accepts BOTH:
##   - the Action-Replay envelope: {seq, actor_fk, verb, target_fk, result, anim_hint}
##   - the #1055 stub:             {kind, actor/actor_id, zone/target}
## so the new combat path and the old zone_move wiring share one entry point.
func _parse_replay_beat(r: Dictionary) -> Dictionary:
	# verb (envelope) takes precedence; fall back to the stub `kind`.
	var verb := String(r.get("verb", r.get("kind", "")))
	if verb == "":
		return {}
	var actor := String(r.get("actor_fk", r.get("actor", r.get("actor_id", ""))))
	var target := String(r.get("target_fk", r.get("target", r.get("zone", ""))))
	var result: Dictionary = r.get("result", {}) if typeof(r.get("result", {})) == TYPE_DICTIONARY else {}
	var hint := String(r.get("anim_hint", ""))
	var seq := int(r.get("seq", -1)) if (typeof(r.get("seq", null)) == TYPE_FLOAT or typeof(r.get("seq", null)) == TYPE_INT) else -1
	return {"seq": seq, "verb": verb, "actor": actor, "target": target, "result": result, "anim_hint": hint}


## Drain the replay queue serially: pop the front beat, play it, await its bounded
## duration, repeat until empty. Each beat returns its own frame-bounded length, so the
## whole exchange is deterministic and never blocks forever. A new enqueue_replay while
## draining just appends (the guard keeps a single ordered drain).
func _drain_replay() -> void:
	_draining = true
	while not _replay_queue.is_empty():
		var beat: Dictionary = _replay_queue.pop_front()
		var dur := _play_beat(beat)
		print("[Replay] beat seq=%d verb=%s actor=%s target=%s dur=%.2f" % [
			int(beat.get("seq", -1)), String(beat.get("verb", "")),
			String(beat.get("actor", "")), String(beat.get("target", "")), dur])
		if dur > 0.0:
			await get_tree().create_timer(dur).timeout
	_draining = false


## Dispatch ONE beat to its per-verb handler and return the beat's bounded duration
## (seconds the drain waits before the next beat). Unknown verbs are accepted-and-ignored
## (return 0 — a generic no-op). The verb vocabulary mirrors the envelope contract's
## closed `verb` set: attack/cast/damage/condition/death/save/check/move_to_zone/travel/narrate.
func _play_beat(beat: Dictionary) -> float:
	var verb := String(beat.get("verb", ""))
	match verb:
		"attack":
			return _beat_attack(beat)
		"cast":
			return _beat_cast(beat)
		"damage":
			return _beat_damage(beat)
		"heal":
			# Not an envelope verb on its own (heals ride a `cast`/`condition` result),
			# but accepted for fixtures/forward-compat: pulse the target green.
			return _beat_heal(beat)
		"condition":
			return _beat_condition(beat)
		"death":
			return _beat_death(beat)
		"move_to_zone", "zone_move":
			return _beat_move(beat)
		_:
			# Unknown / non-animated verb (save/check/travel/narrate or a future verb):
			# accept-and-ignore so the wiring never crashes (envelope §Versioning).
			return 0.0


# --- per-verb beat handlers (#1060). Each is RENDERER-OWNED presentation of the
# engine-decided beat; none touch game state. ----------------------------------

## attack: the actor faces its target (facing derived from actor→target zone anchors,
## ISO-PROJECTION.md combat rule) and plays the one-shot attack clip, then auto-returns
## to idle. A following `damage` beat (its own seq) flashes/floats on the target.
func _beat_attack(beat: Dictionary) -> float:
	var tok := _replay_token(String(beat.get("actor", "")))
	if tok == null:
		return 0.0
	_face_token_at_target(tok, String(beat.get("actor", "")), String(beat.get("target", "")))
	return tok.play_oneshot("attack")


## cast: the actor plays the cast clip. If the result is a heal (anim_hint heal_pulse or
## result.outcome == "heal"), the TARGET gets a green pulse + (if hp surfaced) an HP-bar
## update + a floating "+N" — the heal is shown on the target in the same beat.
func _beat_cast(beat: Dictionary) -> float:
	var tok := _replay_token(String(beat.get("actor", "")))
	if tok == null:
		return 0.0
	_face_token_at_target(tok, String(beat.get("actor", "")), String(beat.get("target", "")))
	var dur := tok.play_oneshot("cast")
	# A cast that resolves to a heal also pulses the target (the result is engine-decided).
	var result: Dictionary = beat.get("result", {})
	var hint := String(beat.get("anim_hint", ""))
	if hint == "heal_pulse" or String(result.get("outcome", "")) == "heal":
		_apply_heal_to_target(beat)
	return dur


## damage: HP applied to the target — flash the target red + float the damage number +
## (if hp surfaced) update its HP bar. The amount/hp come straight from the engine result.
func _beat_damage(beat: Dictionary) -> float:
	var target_tok := _replay_token(String(beat.get("target", "")))
	if target_tok == null:
		return 0.0
	var result: Dictionary = beat.get("result", {})
	var amount := _result_amount(result, ["damage", "amount", "total"])
	if amount != "":
		_float_on_token(target_tok, amount, false)
	_apply_hp_from_result(target_tok, result)
	return target_tok.flash(CharacterToken.DAMAGE_FLASH_COLOR)


## heal (explicit verb / fixture): pulse the target green + float "+N" + update HP bar.
func _beat_heal(beat: Dictionary) -> float:
	return _apply_heal_to_target(beat)


## condition: a status changed (bloodied, prone, downed, blessed…). Brief violet flash on
## the target + (if hp surfaced, e.g. bloodied carries hp_after) an HP-bar update.
func _beat_condition(beat: Dictionary) -> float:
	var target_tok := _replay_token(String(beat.get("target", "")))
	if target_tok == null:
		return 0.0
	var result: Dictionary = beat.get("result", {})
	_apply_hp_from_result(target_tok, result)
	return target_tok.flash(CharacterToken.CONDITION_FLASH_COLOR)


## death: the target drops — fade it out and free the token (it leaves the stage). The
## actor_fk on a death beat is usually the dier; we resolve target first, then actor.
func _beat_death(beat: Dictionary) -> float:
	var who := String(beat.get("target", ""))
	if who == "" or _tokens.get(who, null) == null:
		who = String(beat.get("actor", ""))
	var tok := _replay_token(who)
	if tok == null:
		return 0.0
	return tok.fade_out_and_free()


## move_to_zone / zone_move: the actor walks to a named zone (renderer-derived facing).
## Reuses the existing #1055 walk path exactly.
func _beat_move(beat: Dictionary) -> float:
	var actor := String(beat.get("actor", ""))
	# Envelope move_to_zone carries the zone in target_fk; the stub carried it in zone.
	var zone := String(beat.get("target", ""))
	if actor == "" or zone == "":
		return 0.0
	apply_zone_move(actor, zone)
	# The walk tween is MOVE_TWEEN_SEC long; bound the beat to it so the next beat waits.
	return CharacterToken.MOVE_TWEEN_SEC


# --- replay helpers ---------------------------------------------------------

## The spawned CharacterToken for an actor id, or null if not on stage / already dead.
func _replay_token(actor_id: String) -> CharacterToken:
	if actor_id == "":
		return null
	var tok: CharacterToken = _tokens.get(actor_id, null)
	if tok == null or not is_instance_valid(tok) or tok.is_dead():
		return null
	return tok


## Face `tok` toward `target_id`'s zone anchor (combat facing = actor-zone → target-zone,
## ISO-PROJECTION.md). Uses each token's CURRENT screen position (their zone anchor) so the
## facing is derived, never engine-supplied. A no-op if the target isn't on stage.
func _face_token_at_target(tok: CharacterToken, actor_id: String, target_id: String) -> void:
	var target_tok: CharacterToken = _tokens.get(target_id, null)
	if target_tok == null or not is_instance_valid(target_tok):
		return
	var from_pos: Vector2 = _token_prev_pos.get(actor_id, tok.position)
	var to_pos: Vector2 = target_tok.position
	var facing := FacingResolver.octant(from_pos, to_pos, FACING_ORDER)
	tok.set_facing(facing)


## Apply a heal beat's result to its TARGET: green pulse + float "+N" + HP-bar update.
## Shared by `cast→heal` and the explicit `heal` verb. Returns the pulse's bounded length.
func _apply_heal_to_target(beat: Dictionary) -> float:
	var target_tok := _replay_token(String(beat.get("target", "")))
	if target_tok == null:
		return 0.0
	var result: Dictionary = beat.get("result", {})
	var amount := _result_amount(result, ["amount", "heal", "total"])
	if amount != "":
		_float_on_token(target_tok, "+" + amount, true)
	_apply_hp_from_result(target_tok, result)
	return target_tok.heal_pulse()


## If the engine result surfaced hp (hp_after + an hp_max, or an explicit fraction),
## push it to the token's HP bar. Pure projection — the renderer never recomputes hp.
func _apply_hp_from_result(tok: CharacterToken, result: Dictionary) -> void:
	if result.is_empty():
		return
	# Prefer hp_after + a max (hpMax / hp_max / max); else an explicit hp_frac.
	var has_after := result.has("hp_after") or result.has("hp")
	if has_after:
		var hp := float(result.get("hp_after", result.get("hp", 0)))
		var hp_max := float(result.get("hp_max", result.get("hpMax", result.get("max", 0))))
		if hp_max > 0.0:
			tok.set_hp_fraction(hp / hp_max)
			return
	if result.has("hp_frac"):
		tok.set_hp_fraction(float(result["hp_frac"]))


## Stringify the first present numeric field in `keys` from the result (or a nested
## {damage:{total}} / {amount} shape). "" if none — the renderer floats nothing then.
func _result_amount(result: Dictionary, keys: Array) -> String:
	for k in keys:
		if result.has(k):
			var v: Variant = result[k]
			# {damage:{total:8}} nested shape (the envelope example).
			if typeof(v) == TYPE_DICTIONARY and (v as Dictionary).has("total"):
				return str(int((v as Dictionary)["total"]))
			if typeof(v) == TYPE_FLOAT or typeof(v) == TYPE_INT:
				return str(int(v))
	return ""


## Float a combat number above a token via the FxLayer (Main parents it to WorldView as
## the child "FxLayer"). Positioned over the token's head. A no-op if no FxLayer exists
## (e.g. a bare conformance harness that drives apply-only without the input/fx layer).
func _float_on_token(tok: CharacterToken, text: String, is_heal: bool) -> void:
	var fx := get_node_or_null("FxLayer")
	if fx == null:
		return
	# Above the head: the token origin is its feet; lift by ~110px to clear the body.
	var at := tok.position + Vector2(0.0, -110.0)
	fx.call("float_number", at, text, is_heal)


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


## #1060 — the count of currently-spawned actor tokens (party in exploration, the full
## roster in combat). Exposed for the combat-tokens conformance assertion.
func token_count() -> int:
	return _tokens.size()


## #1060 — the renderer-owned team modulate last applied to a token (WHITE if none).
## Exposed so the conformance can assert foes carry the hostile wash and allies don't.
func team_tint_for(engine_actor_id: String) -> Color:
	return _token_team_tint.get(engine_actor_id, Color(1, 1, 1, 1))


## The static pillar prop (null until spawned). Exposed for #1055's occlusion test.
func pillar_prop() -> PropActor:
	return _pillar


## #1060 — true while the Action-Replay queue is draining (a beat is mid-play or queued).
## Exposed so a conformance/visual harness can await the full exchange before asserting.
func is_replaying() -> bool:
	return _draining or not _replay_queue.is_empty()


## #1060 — clear the replay queue + dedup cursor so a fresh sequence replays from scratch.
## For a conformance/visual harness that drives beats DELIBERATELY (after the standalone
## FIXTURE auto-poll already consumed events.json once against the exploration stage).
## NOT used on the live path — there the cursor advances monotonically and never resets.
func reset_replay() -> void:
	_replay_queue.clear()
	_last_replayed_seq = -1
	_draining = false


# ---------------------------------------------------------------------------
# Interactive grid-walk (#iso-interactive-walk). Store the nav spec loaded by
# the --preview-scene harness so _unhandled_input can convert a floor click →
# grid cell → A* path → cell-by-cell animated walk for the active token.
# ---------------------------------------------------------------------------

## The nav spec block most recently loaded via setup_nav (called from Main after
## _run_preview_scene parses the spec JSON). Stored here so _unhandled_input can
## access the grid transform + blocked cells without coupling to NavOverlay.
var _nav_cols: int = 0
var _nav_rows: int = 0
var _nav_cell_w: float = 64.0
var _nav_origin: Vector2 = Vector2.ZERO
var _nav_blocked: Array = []   ## Array of Vector2i

## The current cell of the ACTIVE token (the one that moves on click). Updated on
## each successful walk step so the next click starts from the right cell.
var _active_cell: Vector2i = Vector2i(0, 0)

## True while a cell-by-cell walk coroutine is running (so a second click does not
## start a second concurrent walk — it is ignored until the current one finishes).
var _walking: bool = false

## The actor id that is the "active" token for interactive walk (default = first party
## actor in the spec, set by Main after spawning preview sprites via set_active_preview_actor).
var _active_preview_actor_id: String = ""

# ---------------------------------------------------------------------------
# #iso-camera — Camera2D state. Camera lives in the .tscn as SceneCamera;
# pan/zoom/clamp helpers all live here so the GDScript stays in one place.
# Zoom range: 0.75x (zoomed out) to 2.0x (zoomed in). Camera.zoom is a
# Vector2 scale factor — zoom=Vector2(2,2) means things appear 2x larger.
# ---------------------------------------------------------------------------
const CAM_ZOOM_MIN := 0.75  ## zoomed out (small camera zoom = see more)
const CAM_ZOOM_MAX := 2.00  ## zoomed in  (large camera zoom = see less)

## True while the player is dragging (middle or right mouse held).
var _cam_dragging: bool = false
## Where the drag started (global px), so delta is relative.
var _cam_drag_origin: Vector2 = Vector2.ZERO
## Camera world-position at the drag start, so pan tracks the finger 1:1.
var _cam_pos_at_drag_start: Vector2 = Vector2.ZERO
## Last known backdrop texture size (px) — used to clamp pan so we can't scroll off.
var _cam_backdrop_size: Vector2 = Vector2(1152.0, 648.0)
## True once the camera was explicitly panned/zoomed by the user; used by
## _camera_recenter to know whether a recenter makes sense.
var _cam_user_moved: bool = false


## Called by Main after parsing the spec JSON so WorldView knows the grid geometry.
## Stores the nav data so _unhandled_input can invert the transform and do A*.
func setup_nav(nav: Dictionary) -> void:
	_nav_cols = int(nav.get("cols", 0))
	_nav_rows = int(nav.get("rows", 0))
	_nav_cell_w = float(nav.get("cell_w_px", 64))
	var orig_v: Variant = nav.get("origin_px", [0, 0])
	if typeof(orig_v) == TYPE_ARRAY and (orig_v as Array).size() >= 2:
		_nav_origin = Vector2(float((orig_v as Array)[0]), float((orig_v as Array)[1]))
	_nav_blocked.clear()
	var blocked_v: Variant = nav.get("blocked", [])
	if typeof(blocked_v) == TYPE_ARRAY:
		for b in (blocked_v as Array):
			if typeof(b) == TYPE_ARRAY and (b as Array).size() >= 2:
				_nav_blocked.append(Vector2i(int((b as Array)[0]), int((b as Array)[1])))
	print("[NavWalk] setup_nav cols=%d rows=%d cell_w=%.0f blocked=%d" % [
		_nav_cols, _nav_rows, _nav_cell_w, _nav_blocked.size()])


## Set which actor is the "active" one for interactive walk, and what cell it starts on.
func set_active_preview_actor(actor_id: String, start_cell: Vector2i) -> void:
	_active_preview_actor_id = actor_id
	_active_cell = start_cell
	print("[NavWalk] active actor=%s start_cell=(%d,%d)" % [actor_id, start_cell.x, start_cell.y])


## Convert a screen position (WorldView-local px) to a grid cell using the inverse
## of the dimetric 2:1 transform from ISO-PROJECTION.md:
##   dx = sx - origin.x,  dy = sy - origin.y
##   c  = ( (2*dx/cell_w) + (4*dy/cell_w) ) / 2
##   r  = ( (4*dy/cell_w) - (2*dx/cell_w) ) / 2
## Returns Vector2i(-1,-1) if the result is out of grid bounds.
func _screen_to_cell(screen_pos: Vector2) -> Vector2i:
	if _nav_cols <= 0 or _nav_rows <= 0 or _nav_cell_w <= 0.0:
		return Vector2i(-1, -1)
	var dx := screen_pos.x - _nav_origin.x
	var dy := screen_pos.y - _nav_origin.y
	var c := int(round(((2.0 * dx / _nav_cell_w) + (4.0 * dy / _nav_cell_w)) / 2.0))
	var r := int(round(((4.0 * dy / _nav_cell_w) - (2.0 * dx / _nav_cell_w)) / 2.0))
	if c < 0 or c >= _nav_cols or r < 0 or r >= _nav_rows:
		return Vector2i(-1, -1)
	return Vector2i(c, r)


## Convert a grid cell (c, r) to screen position — same as NavOverlay._cell_to_screen.
func _cell_to_screen_nav(c: int, r: int) -> Vector2:
	return Vector2(
		_nav_origin.x + float(c - r) * _nav_cell_w * 0.5,
		_nav_origin.y + float(c + r) * _nav_cell_w * 0.25
	)


## Build an AStarGrid2D for the current nav spec (same settings as NavOverlay._solve_astar).
func _build_nav_grid() -> AStarGrid2D:
	var grid := AStarGrid2D.new()
	grid.region = Rect2i(0, 0, _nav_cols, _nav_rows)
	grid.diagonal_mode = AStarGrid2D.DIAGONAL_MODE_AT_LEAST_ONE_WALKABLE
	grid.cell_shape = AStarGrid2D.CELL_SHAPE_SQUARE
	grid.update()
	for b in _nav_blocked:
		grid.set_point_solid(b, true)
	return grid


## Zoom the camera by `factor` centered on `anchor_global_px` (the mouse position),
## clamped to [CAM_ZOOM_MIN, CAM_ZOOM_MAX]. Adjusts camera position so the world
## point under the cursor stays fixed (standard "zoom toward cursor" behaviour).
func _camera_zoom_by(factor: float, anchor_global_px: Vector2) -> void:
	if _camera == null:
		return
	var old_zoom := _camera.zoom.x
	var new_zoom := clampf(old_zoom * factor, CAM_ZOOM_MIN, CAM_ZOOM_MAX)
	if is_equal_approx(new_zoom, old_zoom):
		return
	# world point under cursor BEFORE the zoom.
	var vp := _viewport_size()
	var cursor_world := _camera.position + (anchor_global_px - vp * 0.5) / old_zoom
	# Apply the new zoom.
	_camera.zoom = Vector2(new_zoom, new_zoom)
	# Shift camera so the cursor point stays fixed.
	_camera.position = cursor_world - (anchor_global_px - vp * 0.5) / new_zoom
	_clamp_camera()
	_cam_user_moved = true


## Clamp the camera position so the viewport never pans entirely off the backdrop.
## The backdrop is centered at (vp/2) in world-space and has size _cam_backdrop_size *
## its Sprite2D scale. We allow panning up to half the backdrop in each direction,
## minus half the visible window — so at least one pixel of backdrop remains visible.
func _clamp_camera() -> void:
	if _camera == null:
		return
	var vp := _viewport_size()
	var bw := _cam_backdrop_size.x * _backdrop.scale.x
	var bh := _cam_backdrop_size.y * _backdrop.scale.y
	var zoom := _camera.zoom.x
	# Half the viewport in world units at this zoom level.
	var hw := (vp.x * 0.5) / zoom
	var hh := (vp.y * 0.5) / zoom
	# Backdrop half-extents in world units (backdrop centered at vp/2).
	var cx := vp.x * 0.5
	var cy := vp.y * 0.5
	var max_x := cx + bw * 0.5 - hw
	var min_x := cx - bw * 0.5 + hw
	var max_y := cy + bh * 0.5 - hh
	var min_y := cy - bh * 0.5 + hh
	# If the backdrop is smaller than the viewport (zoomed out past native), allow
	# the camera to stay centered (clamp collapses to the center).
	if min_x > max_x:
		min_x = cx
		max_x = cx
	if min_y > max_y:
		min_y = cy
		max_y = cy
	_camera.position = Vector2(
		clampf(_camera.position.x, min_x, max_x),
		clampf(_camera.position.y, min_y, max_y)
	)


## Smoothly recenter the camera on the active party token (or viewport centre).
## Called on Home/Space. Uses a create_tween so the smoothing is visible.
func _camera_recenter() -> void:
	if _camera == null:
		return
	# Target: the active party token's position, or viewport centre if none.
	var target := _viewport_size() * 0.5
	if _active_preview_actor_id != "":
		var tok := _get_active_preview_token()
		if tok != null and is_instance_valid(tok):
			target = tok.position
	elif not _tokens.is_empty():
		# Exploration path: recenter on any token.
		for aid in _tokens.keys():
			var t: CharacterToken = _tokens[aid]
			if is_instance_valid(t):
				target = t.position
				break
	# Smooth tween to target over 0.4s.
	var tw := get_tree().create_tween()
	tw.set_trans(Tween.TRANS_SINE)
	tw.set_ease(Tween.EASE_IN_OUT)
	tw.tween_property(_camera, "position", target, 0.4)
	print("[Camera] recenter → (%.0f,%.0f)" % [target.x, target.y])


## Expose the camera for the --preview-scene screenshot helper (e.g. programmatic zoom).
## Returns null if no camera is present.
func scene_camera() -> Camera2D:
	return _camera


## Handle a left floor click: convert to grid cell, solve A*, walk the active token
## along the path cell-by-cell. Called from _unhandled_input.
func _handle_floor_click(screen_pos: Vector2) -> void:
	if _active_preview_actor_id == "" or _nav_cols <= 0:
		return
	if _walking:
		print("[NavWalk] ignoring click — walk already in progress")
		return

	# Convert screen → cell.
	var target_cell := _screen_to_cell(screen_pos)
	if target_cell.x < 0:
		print("[NavWalk] click out of grid bounds — no move")
		return

	# Reject blocked cells.
	for b in _nav_blocked:
		if b == target_cell:
			print("[NavWalk] click on blocked cell (%d,%d) — no move" % [target_cell.x, target_cell.y])
			return

	# Build the grid and solve the path.
	var grid := _build_nav_grid()
	var id_path := grid.get_id_path(_active_cell, target_cell)
	if id_path.is_empty():
		print("[NavWalk] no A* path from (%d,%d) to (%d,%d) — no move" % [
			_active_cell.x, _active_cell.y, target_cell.x, target_cell.y])
		return

	# Convert PackedVector2Array → Array of Vector2i (grid ids).
	var path: Array = []
	for p in id_path:
		path.append(Vector2i(int(p.x), int(p.y)))

	print("[NavWalk] walking actor=%s path_len=%d from=(%d,%d) to=(%d,%d)" % [
		_active_preview_actor_id, path.size(),
		_active_cell.x, _active_cell.y, target_cell.x, target_cell.y])

	# Walk the token along the path.
	_walk_along_path(path)


## Coroutine: walk the active preview token along a grid path, one cell at a time.
## Each step: set facing (octant of segment), set walk anim, tween to next cell,
## await; on arrival set idle.
func _walk_along_path(path: Array) -> void:
	_walking = true
	var tok: CharacterToken = _get_active_preview_token()
	if tok == null:
		_walking = false
		return

	# Skip the first cell (it is the current position).
	for i in range(1, path.size()):
		tok = _get_active_preview_token()
		if tok == null:
			break
		var from_cell: Vector2i = path[i - 1]
		var to_cell: Vector2i = path[i]
		var from_screen := _cell_to_screen_nav(from_cell.x, from_cell.y)
		var to_screen := _cell_to_screen_nav(to_cell.x, to_cell.y)

		# Derive 8-way facing for this segment.
		var facing := FacingResolver.octant(from_screen, to_screen, FACING_ORDER)
		tok.set_facing(facing)
		tok.set_anim("walk")

		# Tween to the next cell's screen position.
		tok.set_zone_target(to_screen)
		_token_prev_pos[_active_preview_actor_id] = to_screen
		_active_cell = to_cell

		# Await the tween duration.
		await get_tree().create_timer(CharacterToken.MOVE_TWEEN_SEC).timeout

	# Arrived — return to idle.
	tok = _get_active_preview_token()
	if tok != null:
		tok.set_anim("idle")
		print("[NavWalk] walk complete, actor=%s final_cell=(%d,%d)" % [
			_active_preview_actor_id, _active_cell.x, _active_cell.y])

	_walking = false


## Return the active preview token (or null if not found / freed).
func _get_active_preview_token() -> CharacterToken:
	var ysort := get_node_or_null("YSortLayer") as Node2D
	if ysort == null:
		return null
	var node_name := "PreviewToken_" + _active_preview_actor_id
	var tok := ysort.get_node_or_null(node_name) as CharacterToken
	if tok == null or not is_instance_valid(tok):
		return null
	return tok


## Handle unhandled input for interactive walk (only active when nav is configured).
## Also handles camera pan (middle/right drag) and zoom (wheel), and recenter (Home/Space).
func _unhandled_input(event: InputEvent) -> void:
	# --- CAMERA: scroll-to-zoom ---
	if event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		if mb.pressed:
			if mb.button_index == MOUSE_BUTTON_WHEEL_UP:
				_camera_zoom_by(1.1, mb.global_position)
				get_viewport().set_input_as_handled()
				return
			elif mb.button_index == MOUSE_BUTTON_WHEEL_DOWN:
				_camera_zoom_by(1.0 / 1.1, mb.global_position)
				get_viewport().set_input_as_handled()
				return
			# Track middle/right button drag start.
			elif mb.button_index == MOUSE_BUTTON_MIDDLE or mb.button_index == MOUSE_BUTTON_RIGHT:
				_cam_dragging = true
				_cam_drag_origin = mb.global_position
				_cam_pos_at_drag_start = _camera.position
				get_viewport().set_input_as_handled()
				return
		else:
			if mb.button_index == MOUSE_BUTTON_MIDDLE or mb.button_index == MOUSE_BUTTON_RIGHT:
				_cam_dragging = false
				get_viewport().set_input_as_handled()
				return
		# Left click — fall through to floor-click / walk logic below.
		if mb.button_index != MOUSE_BUTTON_LEFT or not mb.pressed:
			return
		if _nav_cols <= 0 or _active_preview_actor_id == "":
			return
		var local_pos := to_local(mb.global_position)
		_handle_floor_click(local_pos)
		get_viewport().set_input_as_handled()
		return

	# --- CAMERA: drag-to-pan ---
	if event is InputEventMouseMotion and _cam_dragging:
		var mm := event as InputEventMouseMotion
		var delta := (mm.global_position - _cam_drag_origin) / _camera.zoom.x
		_camera.position = _cam_pos_at_drag_start - delta
		_clamp_camera()
		get_viewport().set_input_as_handled()
		return

	# --- CAMERA: recenter on party (Home or Space) ---
	if event is InputEventKey:
		var ke := event as InputEventKey
		if ke.pressed and not ke.echo:
			if ke.keycode == KEY_HOME or ke.keycode == KEY_SPACE:
				_camera_recenter()
				get_viewport().set_input_as_handled()
				return


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

## Spawn/reconcile actor tokens into the Y-sorted layer, keyed by engine_actor_id so
## each is built ONCE and only repositioned/re-tinted thereafter. Tokens for actors no
## longer present are freed (reconcile = no leaks across ticks).
##   - COMBAT (#1060): one token PER combat token (the whole roster — party + foes),
##     placed at its named ZONE's anchor, with a renderer-owned TEAM tint.
##   - EXPLORATION (#1054, unchanged): ONE token for character.party[0] at the
##     FOREGROUND zone. Empty combat == today's behavior exactly.
## The combat-token x/y are IGNORED (positionAuthority:"derived"); the renderer derives
## the screen position from the engine's named zone via zone_screen_pos(), staying the
## sole owner of layout while the engine stays the sole owner of WHERE (the zone).
func _reconcile_actors(character: Dictionary, combat: Dictionary, in_combat: bool) -> void:
	var wanted: Dictionary = {}  # actor_id -> true (actors that should exist this tick)
	# A location change since the last snapshot is a `travel` — reset facing to the
	# rest/default facing rather than deriving a walk direction (ISO-PROJECTION.md).
	var traveled := _prev_location_id != "" and _prev_location_id != _location_id

	var combat_tokens := _combat_token_list(combat) if in_combat else []
	if not combat_tokens.is_empty():
		_reconcile_combat_tokens(combat_tokens, wanted, traveled)
	else:
		_reconcile_exploration_token(character, wanted, traveled)

	# Free tokens whose actor is no longer present (no leaks).
	for existing_id in _tokens.keys():
		if not wanted.has(existing_id):
			var stale: CharacterToken = _tokens[existing_id]
			if is_instance_valid(stale):
				stale.queue_free()
			_tokens.erase(existing_id)
			_token_prev_pos.erase(existing_id)
			_token_team_tint.erase(existing_id)
			# Drop any served-atlas scope mapping pointing at the freed token (#1063).
			for sc in _sheet_scope_actor.keys():
				if String(_sheet_scope_actor[sc]) == String(existing_id):
					_sheet_scope_actor.erase(sc)


## #1054 (UNCHANGED behavior) — spawn/reconcile ONE token for character.party[0] at the
## foreground zone. The exploration path; runs whenever combat is inactive/empty.
func _reconcile_exploration_token(character: Dictionary, wanted: Dictionary, traveled: bool) -> void:
	var lead := _lead_actor(character)
	if lead.is_empty():
		return
	var actor_id := String(lead.get("id", ""))
	if actor_id == "":
		return
	wanted[actor_id] = true
	var tok: CharacterToken = _tokens.get(actor_id, null)
	var is_new := tok == null
	if is_new:
		tok = _spawn_token(actor_id)
		if tok != null:
			_tokens[actor_id] = tok
	if tok != null:
		# Exploration tokens carry the neutral ally tint (no hostile wash).
		_apply_team_tint(tok, actor_id, "ally")
		var target_pos := _foreground_pos()
		_reconcile_token_motion(tok, actor_id, target_pos, is_new, traveled)


## #1060 — spawn/reconcile a token for EVERY combatant at its named zone, team-tinted.
## Each token is keyed by its engine_actor_id (combat token `id`); foes render with the
## committed placeholder (no per-actor atlas) plus a hostile tint so they read as enemies.
func _reconcile_combat_tokens(combat_tokens: Array, wanted: Dictionary, traveled: bool) -> void:
	for entry in combat_tokens:
		if typeof(entry) != TYPE_DICTIONARY:
			continue
		var t: Dictionary = entry
		var actor_id := String(t.get("id", ""))
		if actor_id == "":
			continue
		wanted[actor_id] = true
		var tok: CharacterToken = _tokens.get(actor_id, null)
		var is_new := tok == null
		if is_new:
			tok = _spawn_token(actor_id)
			if tok != null:
				_tokens[actor_id] = tok
		if tok == null:
			continue
		# Renderer-owned team tint (foe = hostile red wash, ally = neutral).
		_apply_team_tint(tok, actor_id, String(t.get("team", "ally")))
		# Derive the screen position from the engine's named ZONE (never the x/y hint).
		var target_pos := _combat_token_pos(t)
		_reconcile_token_motion(tok, actor_id, target_pos, is_new, traveled)


## #1060 — the combat token list (combat.tokens[]) or [] if absent/empty/malformed.
func _combat_token_list(combat: Dictionary) -> Array:
	var toks: Variant = combat.get("tokens", [])
	return toks if typeof(toks) == TYPE_ARRAY else []


## #1060 — screen position for a combat token: its named zone's anchor (the derived,
## projection-correct placement). Falls back to the foreground when the zone is unknown
## this tick (e.g. a combatant in a zone the profile/atlas didn't declare), so a token
## is never lost off-stage.
func _combat_token_pos(token: Dictionary) -> Vector2:
	var zone := String(token.get("zone", ""))
	if zone != "" and _zone_screen.has(zone):
		return _zone_screen[zone]
	return _foreground_pos()


## #1060 — apply the renderer-owned TEAM tint to a token's sprite (idempotent). Modulate
## is pure presentation; it touches no game state. Cached so a re-tint is a no-op.
func _apply_team_tint(tok: CharacterToken, actor_id: String, team: String) -> void:
	var tint := _team_tint(team)
	if _token_team_tint.get(actor_id, null) == tint:
		return
	tok.set_team_tint(tint)
	_token_team_tint[actor_id] = tint


## #1060 — map a team string to its modulate Color. foe → hostile red; ally → neutral;
## anything else → a faint cool wash so unknown teams still read as "not an ally".
func _team_tint(team: String) -> Color:
	match team.to_lower():
		"foe", "enemy", "monster", "hostile":
			return TEAM_TINT_FOE
		"ally", "pc", "companion", "friendly":
			return TEAM_TINT_ALLY
		_:
			return TEAM_TINT_NEUTRAL


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
	# #1060 — FALLBACK-SAFE default: a combatant without its OWN committed asset dir
	# (every foe/NPC today) renders with the shared committed placeholder. The team
	# tint (_apply_team_tint) is what distinguishes it visually. Without this, foes
	# would silently fail to spawn (_spawn_token returns null), losing the roster.
	var default_dir: String = CHAR_ASSET_ROOT + DEFAULT_CHAR_SLUG + "/"
	return _load_sheet_dir(default_dir)


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


## Load a backdrop directly from a local filesystem path (e.g. /tmp/art/tavern.png)
## and apply it as the BackdropPlane texture. This lets the --preview-scene harness
## point at a freshly-generated PNG in /tmp with zero HTTP/serving overhead.
## Returns true on success, false on load failure (logs a warning and leaves the
## current backdrop unchanged so the procedural fallback stays visible).
func apply_local_backdrop(path: String) -> bool:
	if path == "":
		push_warning("[WorldView] apply_local_backdrop: empty path")
		return false
	var img := Image.new()
	var err := img.load(path)
	if err != OK:
		push_warning("[WorldView] apply_local_backdrop: Image.load failed for path=%s err=%d" % [path, err])
		return false
	var tex := ImageTexture.create_from_image(img)
	if tex == null:
		push_warning("[WorldView] apply_local_backdrop: ImageTexture.create_from_image returned null for path=%s" % path)
		return false
	_apply_texture_backdrop(tex)
	print("[WorldView] apply_local_backdrop: ok path=%s size=%dx%d" % [path, img.get_width(), img.get_height()])
	return true


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
		# #iso-camera: record the backdrop texture size for pan clamping.
		_cam_backdrop_size = tex_size


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
