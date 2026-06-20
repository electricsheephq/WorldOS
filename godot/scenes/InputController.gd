extends Node
class_name InputController
## InputController — turns a left-click in the world into a CONSTRAINED move INTENT
## (#1055). It is the renderer's ONLY write gesture.
##
## CONTRACT ROLE: the renderer NEVER asserts world state — it asks the engine to,
## via a `/move` intent whose `kind` is on the FROZEN allowlist
## (docs/roadmap/contracts/move-intents.md). This node emits ONLY the three
## graphical kinds: `move_to_zone`, `travel`, `inspect`. It refuses to emit anything
## else (frozen-vocab guard) and it NEVER fabricates a world coordinate — a click
## outside the walkmask is ignored.
##
## RESPONSIBILITY SPLIT (the load-bearing invariant): the engine owns the
## DESTINATION (the next authoritative snapshot moves the token); the renderer owns
## the PATH + the optimistic feedback. So a click shows an immediate FxLayer ping at
## the click point but DOES NOT move the token — only the next snapshot does.
##
## HIT-TEST PRECEDENCE (most specific → least): actor/prop pick (inspect) → travel
## affordance (travel) → walkmask floor (move_to_zone) → ignore (outside the mask).

## The ONLY kinds this controller may emit. Asserted before every emit so a coding
## error can never widen the frozen /move vocabulary from the renderer side.
const ALLOWED_KINDS := ["move_to_zone", "travel", "inspect"]

## Emitted with the constrained intent Dictionary {kind, target}. Main routes it to
## SurfaceClient.move(). Listeners must treat it as a REQUEST, never a fact.
signal intent_requested(intent: Dictionary)

## Wired by Main at composition time. `_world` is the WorldView projection (the
## hit-test + zone-snap surface); `_fx` is the optimistic-ping layer; `_surface`
## is the transport (only used to know we are in FIXTURE mode for the log note).
var _world: Node = null      # WorldView
var _fx: Node = null         # FxLayer (optional — ping is skipped if null)
var _surface: Node = null    # SurfaceClient


## Composition-root wiring. Main calls this after instancing.
func setup(world: Node, fx: Node, surface: Node) -> void:
	_world = world
	_fx = fx
	_surface = surface


# ---------------------------------------------------------------------------
# Live input → intent. _unhandled_input so HUD/UI controls can consume clicks
# first; only world clicks reach here.
# ---------------------------------------------------------------------------
func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		if mb.button_index == MOUSE_BUTTON_LEFT and mb.pressed:
			# The FloorPolygon points are WorldView-local; WorldView is untransformed
			# at the scene root, so the viewport click pos maps 1:1 to world-local.
			var world_pos := _to_world(mb.position)
			handle_click(world_pos)
			get_viewport().set_input_as_handled()


## Map a viewport (screen) point to WorldView-local coordinates. WorldView is an
## untransformed Node2D at the scene root, so this is its inverse transform (the
## identity today, but routed through the node so a future camera/offset is honored).
func _to_world(viewport_pos: Vector2) -> Vector2:
	if _world != null and _world is Node2D:
		return (_world as Node2D).get_global_transform().affine_inverse() * viewport_pos
	return viewport_pos


# ---------------------------------------------------------------------------
# The decision: classify a world-space click into one frozen intent (or ignore).
# Returns the emitted intent Dictionary, or {} if the click was ignored.
# ---------------------------------------------------------------------------
func handle_click(world_pos: Vector2) -> Dictionary:
	if _world == null:
		return {}

	# (1) Most specific: a click on an actor/prop body → inspect.
	var picked := String(_world.pick_actor_at(world_pos))
	if picked != "":
		return _emit({"kind": "inspect", "target": picked}, world_pos,
			"hit actor/prop -> inspect")

	# (2) A click on a travel affordance → that option's verbatim `move`, else a
	# {kind:travel, target:<engine_location_id>}. (No affordance is RENDERED in this
	# slice, so this returns {} live; reachable via simulate_travel_click for tests.)
	var travel := _travel_intent_at(world_pos)
	if not travel.is_empty():
		return _emit(travel, world_pos, "hit travel affordance -> travel")

	# (3) Inside the walkmask → snap to the nearest zone → move_to_zone.
	if _world.is_walkable(world_pos):
		var zone := String(_world.nearest_zone(world_pos))
		if zone == "":
			print("[InputController] click@(%.0f,%.0f) inside-walkmask but no zones — ignored" % [world_pos.x, world_pos.y])
			return {}
		print("[InputController] click@(%.0f,%.0f) inside-walkmask -> {kind:move_to_zone, target:\"%s\"}" % [world_pos.x, world_pos.y, zone])
		return _emit({"kind": "move_to_zone", "target": zone}, world_pos, "")

	# (4) Outside the walkmask + no affordance → ignore. NEVER assert a coordinate.
	print("[InputController] click@(%.0f,%.0f) outside-walkmask — ignored (no coordinate asserted)" % [world_pos.x, world_pos.y])
	return {}


## Build the travel intent for a click, if it lands on a known travel affordance.
## Today no affordance node is rendered, so this returns {}; the VERBATIM-vs-wrapped
## emit logic lives here so a rendered chip later just needs a hit-test. Tests reach
## the travel path via simulate_travel_click(option).
func _travel_intent_at(_world_pos: Vector2) -> Dictionary:
	# No rendered travel-exit affordance in this slice → nothing to hit.
	return {}


# ---------------------------------------------------------------------------
# Deterministic simulation entry points (for the --smoke-intent path + tests).
# They drive the SAME handle_click decision logic as a live click.
# ---------------------------------------------------------------------------

## Simulate a left click at a WorldView-local point (e.g. a zone's screen anchor).
func simulate_click(world_pos: Vector2) -> Dictionary:
	return handle_click(world_pos)


## Simulate clicking a travel affordance for a given atlas travel_option Dictionary.
## Emits the option's VERBATIM `move` intent if present (must survive unchanged), else
## wraps {to|target} into {kind:travel, target:<engine_location_id>}.
func simulate_travel_click(option: Dictionary) -> Dictionary:
	var intent := _option_to_travel_intent(option)
	if intent.is_empty():
		return {}
	# Use the WorldView's anchor for the option's destination if known, else center.
	return _emit(intent, Vector2.ZERO, "simulated travel affordance")


## Turn a travel_option into a travel intent. VERBATIM `move` wins; else wrap the
## destination id. Returns {} if neither a move nor a destination id is present.
func _option_to_travel_intent(option: Dictionary) -> Dictionary:
	var mv: Variant = option.get("move", null)
	if typeof(mv) == TYPE_DICTIONARY and (mv as Dictionary).has("kind"):
		# Emit verbatim — the engine authored this exact intent.
		return (mv as Dictionary).duplicate(true)
	var dest := String(option.get("target", option.get("to", "")))
	if dest != "":
		return {"kind": "travel", "target": dest}
	return {}


# ---------------------------------------------------------------------------
# Emit (with the frozen-vocab guard + optimistic ping). Single choke point so EVERY
# intent is validated and EVERY click gets feedback.
# ---------------------------------------------------------------------------
func _emit(intent: Dictionary, ping_pos: Vector2, note: String) -> Dictionary:
	var kind := String(intent.get("kind", ""))
	# FROZEN-VOCAB GUARD: refuse to widen the /move allowlist from the renderer.
	if not ALLOWED_KINDS.has(kind):
		push_error("[InputController] BLOCKED non-frozen intent kind '%s' (allowed: %s)" % [kind, str(ALLOWED_KINDS)])
		return {}

	# Live-writable guard: only emit when the view says we may act. In FIXTURE mode
	# the atlas has no can_act/is_live_view (or can_act:true) so this passes and the
	# demo works — but we do NOT pretend it reached an engine (SurfaceClient logs
	# FIXTURE on the actual POST attempt).
	if _world != null and _world.has_method("is_live_writable") and not _world.is_live_writable():
		print("[InputController] view not live-writable — intent suppressed: ", intent)
		return {}

	# OPTIMISTIC feedback ONLY — the ping marks the click; it does NOT move a token.
	# The next authoritative snapshot moves the token (engine owns the destination).
	if _fx != null and _fx.has_method("ping") and ping_pos != Vector2.ZERO:
		_fx.ping(ping_pos)

	if note != "":
		print("[InputController] emit %s (%s)" % [str(intent), note])
	emit_signal("intent_requested", intent)
	return intent
