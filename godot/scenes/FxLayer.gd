extends Node2D
class_name FxLayer
## FxLayer — transient, self-freeing click feedback above the world (#1055).
##
## CONTRACT ROLE: PURE presentation. It owns ZERO game state and never asserts a
## world position. Its only job is the OPTIMISTIC click ping: a brief expanding
## ring + flash at the click point, drawn on its own high z-index so it floats over
## the Y-sorted actors. The ping is feedback ONLY — it does NOT move any token. The
## engine owns the destination (the next authoritative snapshot moves the token);
## the renderer owns the path/feedback.
##
## Every ping is a transient node that tweens then queue_free()s itself, so the
## layer never accumulates nodes across clicks (no leaks).

## Draw above the world (YSortLayer actors sit at the default z=0..; the backdrop is
## z=-100). A high z keeps the ping visually on top regardless of foot-y.
const FX_Z_INDEX := 1000

## Ping look + timing (deterministic — no randomness).
const PING_DURATION_SEC := 0.45
const PING_START_RADIUS := 6.0
const PING_END_RADIUS := 30.0
const PING_COLOR := Color(0.78, 0.90, 1.0, 0.85)
const PING_WIDTH := 3.0


func _ready() -> void:
	z_index = FX_Z_INDEX
	z_as_relative = false


## Spawn an optimistic click ping at `world_pos` (a brief expanding, fading ring).
## The ping is a child Node2D that animates itself and frees on completion, so the
## FxLayer never holds onto nodes between clicks. Returns the spawned ping node
## (mainly for tests/validation — callers can ignore it).
func ping(world_pos: Vector2) -> Node2D:
	var p := _Ping.new()
	p.position = world_pos
	add_child(p)
	p.animate()
	return p


# ---------------------------------------------------------------------------
# The transient ping node. A small _draw()-based ring whose radius/alpha are driven
# by a single tween of a 0..1 progress value; it queue_free()s itself on finish.
# ---------------------------------------------------------------------------
class _Ping extends Node2D:
	var _t: float = 0.0  ## 0..1 animation progress

	func _draw() -> void:
		var radius: float = lerpf(FxLayer.PING_START_RADIUS, FxLayer.PING_END_RADIUS, _t)
		var col := FxLayer.PING_COLOR
		col.a = FxLayer.PING_COLOR.a * (1.0 - _t)  # fade out as it expands
		# Antialiased ring + a faint solid core dot for the initial flash.
		draw_arc(Vector2.ZERO, radius, 0.0, TAU, 32, col, FxLayer.PING_WIDTH, true)
		var core := col
		core.a *= 0.5
		draw_circle(Vector2.ZERO, FxLayer.PING_START_RADIUS * (1.0 - _t), core)

	func animate() -> void:
		var tween := create_tween()
		tween.tween_method(_set_progress, 0.0, 1.0, FxLayer.PING_DURATION_SEC)
		tween.tween_callback(queue_free)

	func _set_progress(v: float) -> void:
		_t = v
		queue_redraw()
