extends Node2D
class_name CharacterToken
## CharacterToken — a directional, foot-anchored actor token (#1054).
##
## CONTRACT ROLE: the on-stage presence of ONE engine actor. It is built from a
## sprite-sheet MANIFEST (sheet.json — see godot/tools/gen_placeholder_sheet.py and
## ISO-PROJECTION.md) + the atlas Texture2D, and it renders the actor at one of the
## 8 LOCKED dimetric facings (S,SE,E,NE,N,NW,W,SW). The node ORIGIN is the actor's
## FEET (the manifest `anchor`), so `global_position.y` IS the Y-sort depth key —
## drop this into WorldView's `YSortLayer` and occlusion sorts for free (#1055).
##
## SCOPE (#1054): build the SpriteFrames (one anim per (anim,facing) = 32 anims for
## the 4-anim x 8-facing placeholder), expose `set_facing(dir8)`, `set_anim(name)`,
## `place_at(world_pos)`, and a tweened `set_zone_target(world_pos)` so #1055 just
## wires input + the FacingResolver derivation. An Area2D body picker is included so
## #1055 can do inspect-click selection — no input handling is wired here yet.
##
## OWNS NO GAME STATE. Facing is RENDERER-DERIVED (#1055), never from the engine.

## The locked dimetric facing order (ISO-PROJECTION.md). The manifest carries its
## own `facing_order`; this is only the hard fallback if a manifest omits it.
const LOCKED_FACING_ORDER := ["S", "SE", "E", "NE", "N", "NW", "W", "SW"]
const PROJECTION_LOCK := "dimetric-2to1"

## How long set_zone_target's position tween runs (seconds). #1055 may override.
const MOVE_TWEEN_SEC := 0.45

## The engine actor this token stands for (e.g. "char-aubree"). Exposed for #1055
## inspect-click selection + reconcile-by-id in WorldView.
var engine_actor_id: String = ""

## Current render state.
var _facing: String = "S"
var _anim: String = "idle"
var _facing_order: Array = LOCKED_FACING_ORDER.duplicate()
var _anchor: Vector2 = Vector2(64, 116)
var _kind: String = "character"
## #1060 — renderer-owned team modulate (WHITE = neutral/none). Re-applied after every
## set_manifest (which rebuilds the sprite) so a served-atlas swap keeps the tint.
var _team_tint: Color = Color(1, 1, 1, 1)

var _sprite: AnimatedSprite2D
var _frames: SpriteFrames
var _picker: Area2D
var _picker_shape: CollisionShape2D
var _move_tween: Tween


func _ready() -> void:
	# Build the child nodes once. set_manifest populates the frames; until then the
	# sprite is empty (harmless — WorldView always calls set_manifest before adding).
	if _sprite == null:
		_sprite = AnimatedSprite2D.new()
		_sprite.name = "Sprite"
		add_child(_sprite)
	if _picker == null:
		_picker = Area2D.new()
		_picker.name = "Picker"
		# Don't collide/monitor — it exists purely for #1055 click picking.
		_picker.monitoring = false
		_picker.monitorable = false
		_picker.input_pickable = true
		_picker_shape = CollisionShape2D.new()
		_picker_shape.name = "PickerShape"
		_picker.add_child(_picker_shape)
		add_child(_picker)


# ---------------------------------------------------------------------------
# Build from a manifest + the atlas texture.
# ---------------------------------------------------------------------------

## Build a SpriteFrames with one animation per (anim_name, facing), named
## "{anim}_{facing}" (e.g. idle_S, walk_SE). Each frame is an AtlasTexture slicing
## the sheet at region (col*fw, facing_row*fh, fw, fh) for cols start..start+count.
## The AnimatedSprite2D `offset` is set from the manifest foot `anchor` so the NODE
## ORIGIN lands at the feet (origin.y == depth key for Y-sort).
func set_manifest(manifest: Dictionary, sheet_texture: Texture2D) -> void:
	if _sprite == null:
		# _ready may not have run yet (set before add_child). Build children now.
		_ready()

	_kind = String(manifest.get("kind", "character"))

	# Projection guard — refuse a mis-baked sheet loudly (ISO-PROJECTION.md lock).
	var proj := String(manifest.get("projection", ""))
	if proj != "" and proj != PROJECTION_LOCK:
		push_warning("[CharacterToken] manifest projection '%s' != lock '%s' (actor=%s)" % [
			proj, PROJECTION_LOCK, engine_actor_id])

	var frame: Dictionary = manifest.get("frame", {}) if typeof(manifest.get("frame", {})) == TYPE_DICTIONARY else {}
	var fw := int(frame.get("w", 128))
	var fh := int(frame.get("h", 128))

	var order_v: Variant = manifest.get("facing_order", LOCKED_FACING_ORDER)
	_facing_order = (order_v as Array).duplicate() if typeof(order_v) == TYPE_ARRAY and not (order_v as Array).is_empty() else LOCKED_FACING_ORDER.duplicate()

	var anchor: Dictionary = manifest.get("anchor", {}) if typeof(manifest.get("anchor", {})) == TYPE_DICTIONARY else {}
	_anchor = Vector2(float(anchor.get("x", fw / 2.0)), float(anchor.get("y", fh)))

	var fps := float(manifest.get("fps", 10))

	var anims: Dictionary = manifest.get("animations", {}) if typeof(manifest.get("animations", {})) == TYPE_DICTIONARY else {}

	# Build the SpriteFrames: one entry per (anim, facing).
	_frames = SpriteFrames.new()
	# SpriteFrames is created with a "default" animation; remove it so the count is
	# exactly anim_count * facing_count.
	if _frames.has_animation("default"):
		_frames.remove_animation("default")

	for fi in range(_facing_order.size()):
		var facing := String(_facing_order[fi])
		var row := fi  # row index = facing index (manifest layout lock)
		for anim_name in anims.keys():
			var spec: Dictionary = anims[anim_name]
			if typeof(spec) != TYPE_DICTIONARY:
				continue
			var start := int(spec.get("start", 0))
			var count := int(spec.get("count", 1))
			var loop := bool(spec.get("loop", true))
			var clip := "%s_%s" % [String(anim_name), facing]
			_frames.add_animation(clip)
			_frames.set_animation_loop(clip, loop)
			_frames.set_animation_speed(clip, fps)
			for c in range(count):
				var col := start + c
				var at := AtlasTexture.new()
				at.atlas = sheet_texture
				at.region = Rect2(col * fw, row * fh, fw, fh)
				_frames.add_frame(clip, at)

	_sprite.sprite_frames = _frames
	_sprite.centered = false
	# Place the cell so its anchor px lands on the node origin (feet at origin).
	_sprite.offset = -_anchor
	# #1060 — re-apply the team tint (this rebuilt the sprite; modulate would reset).
	_sprite.modulate = _team_tint

	# Default: idle at the profile default facing (fallback S).
	var default_facing := RenderProfile.default_facing()
	set_facing(default_facing)
	set_anim("idle")


# ---------------------------------------------------------------------------
# Facing / animation control.
# ---------------------------------------------------------------------------

## Set the 8-way facing (one of LOCKED_FACING_ORDER). Replays the current anim in
## the new direction. Unknown directions are ignored (keeps the last valid facing).
func set_facing(dir8: String) -> void:
	if not _facing_order.has(dir8):
		return
	_facing = dir8
	_play_current()


## Set the active animation name (idle/walk/attack/cast). Replays it at the current
## facing. Unknown names fall back at play time to idle_S.
func set_anim(name: String) -> void:
	_anim = name
	_play_current()


func facing() -> String:
	return _facing


func anim() -> String:
	return _anim


## #1060 — apply a renderer-owned TEAM tint (modulate) to the sprite so foe vs ally
## reads at a glance. This is PURE presentation (it owns no game state); the engine
## ships only the `team` string and WorldView maps it to a Color. Persists across
## set_anim/set_facing because it modulates the AnimatedSprite2D, not a single clip.
func set_team_tint(tint: Color) -> void:
	_team_tint = tint
	if _sprite != null:
		_sprite.modulate = tint


## The team tint currently applied (WHITE == neutral/none). For validation.
func team_tint() -> Color:
	return _team_tint


## The SpriteFrames animation count (for validation: expect anim_count*facings = 32).
func animation_count() -> int:
	if _frames == null:
		return 0
	return _frames.get_animation_names().size()


## Frame count of a specific clip (e.g. "walk_S") — for the slice sanity check.
func clip_frame_count(clip: String) -> int:
	if _frames == null or not _frames.has_animation(clip):
		return 0
	return _frames.get_frame_count(clip)


func _play_current() -> void:
	if _sprite == null or _frames == null:
		return
	var clip := "%s_%s" % [_anim, _facing]
	if not _frames.has_animation(clip):
		clip = "idle_S"  # hard fallback
	if not _frames.has_animation(clip):
		return
	_sprite.play(clip)
	_resize_picker()


# ---------------------------------------------------------------------------
# Placement. The node origin is the feet → set `position` to the foot world point.
# ---------------------------------------------------------------------------

## Immediately place the token's feet at `world_pos` (no tween). Used by #1054.
func place_at(world_pos: Vector2) -> void:
	if _move_tween != null and _move_tween.is_valid():
		_move_tween.kill()
	position = world_pos


## Tween the token's feet to `world_pos` (so Y updates progressively for #1055's
## Y-sort occlusion). #1055 wires this to a floor click → zone snap.
func set_zone_target(world_pos: Vector2) -> void:
	if _move_tween != null and _move_tween.is_valid():
		_move_tween.kill()
	_move_tween = create_tween()
	_move_tween.tween_property(self, "position", world_pos, MOVE_TWEEN_SEC)


# ---------------------------------------------------------------------------
# Inspect-click picker (#1055). Size the Area2D's shape to the body cell so a click
# anywhere on the token selects it. No input is handled here yet.
# ---------------------------------------------------------------------------
func _resize_picker() -> void:
	if _picker_shape == null or _frames == null:
		return
	# Use the current frame's region size as the body extent; fall back to the cell.
	var w := 128.0
	var h := 128.0
	var clip := "%s_%s" % [_anim, _facing]
	if _frames.has_animation(clip) and _frames.get_frame_count(clip) > 0:
		var tex := _frames.get_frame_texture(clip, 0)
		if tex != null:
			var sz := tex.get_size()
			if sz.x > 0.0 and sz.y > 0.0:
				w = sz.x
				h = sz.y
	var rect := RectangleShape2D.new()
	rect.size = Vector2(w, h)
	_picker_shape.shape = rect
	# Center the shape over the body: the node origin is at the feet (anchor), so
	# the cell center is at (-anchor + cell/2) relative to origin.
	_picker_shape.position = Vector2(-_anchor.x + w * 0.5, -_anchor.y + h * 0.5)
