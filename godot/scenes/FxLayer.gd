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

## #1060 — floating combat number (damage/heal) look + timing. A label that rises a
## short distance and fades out, then frees itself (no leaks). RENDERER-OWNED
## presentation of the engine-decided number — it shows it, never recomputes it.
const FLOAT_DURATION_SEC := 0.7
const FLOAT_RISE_PX := 34.0
const FLOAT_FONT_SIZE := 22
const FLOAT_DAMAGE_COLOR := Color(1.0, 0.5, 0.42, 1.0)  ## red-orange for damage
const FLOAT_HEAL_COLOR := Color(0.55, 1.0, 0.62, 1.0)   ## green for heals


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


## #1060 — float a combat number (e.g. "8" damage, "+7" heal) up from `world_pos`. The
## label rises FLOAT_RISE_PX while fading, then frees itself. `is_heal` picks the green
## heal color (else the red damage color). Returns the spawned node (for validation).
func float_number(world_pos: Vector2, text: String, is_heal: bool = false) -> Node2D:
	var f := _FloatNumber.new()
	f.position = world_pos
	f.text = text
	f.color = FLOAT_HEAL_COLOR if is_heal else FLOAT_DAMAGE_COLOR
	add_child(f)
	f.animate()
	return f


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


# ---------------------------------------------------------------------------
# #1060 — the transient floating combat number. A Label that rises and fades, then
# frees itself. Pure presentation of an engine-decided value (damage/heal amount).
# ---------------------------------------------------------------------------
class _FloatNumber extends Node2D:
	var text: String = ""
	var color: Color = Color.WHITE
	var _label: Label = null
	var _base_y: float = 0.0

	func _ready() -> void:
		_base_y = position.y
		_label = Label.new()
		_label.text = text
		_label.add_theme_color_override("font_color", color)
		_label.add_theme_font_size_override("font_size", FxLayer.FLOAT_FONT_SIZE)
		# Center the text roughly over the anchor point.
		_label.position = Vector2(-18.0, -FxLayer.FLOAT_FONT_SIZE)
		add_child(_label)

	func animate() -> void:
		var tween := create_tween()
		tween.set_parallel(true)
		# Rise.
		tween.tween_property(self, "position:y", _base_y - FxLayer.FLOAT_RISE_PX, FxLayer.FLOAT_DURATION_SEC)
		# Fade (modulate alpha on the whole node).
		tween.tween_property(self, "modulate:a", 0.0, FxLayer.FLOAT_DURATION_SEC)
		# Free after the (parallel) effects finish.
		tween.chain().tween_callback(queue_free)
