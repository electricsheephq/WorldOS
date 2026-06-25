extends Node2D
class_name PropActor
## PropActor — a static, foot-anchored scenery occluder (#1054).
##
## CONTRACT ROLE: a non-animated stage prop (e.g. a pillar/crate) built from a
## degenerate prop MANIFEST (1 facing x 1 frame — see extensions/renderers/godot/tools/gen_placeholder_sheet.py).
## Like CharacterToken, its node ORIGIN is the prop's BASE/FOOT (the manifest
## `anchor`), so `global_position.y` is the Y-sort depth key: dropped into WorldView's
## YSortLayer it occludes / is occluded by the CharacterToken purely by foot-y (#1055
## proves occlusion both ways).
##
## OWNS NO GAME STATE. Static: a single Sprite2D, no animation, no input.

const PROJECTION_LOCK := "dimetric-2to1"

## An optional id (so WorldView can reconcile props by name if it ever spawns many).
var prop_id: String = ""

var _anchor: Vector2 = Vector2(64, 184)
var _sprite: Sprite2D


func _ready() -> void:
	if _sprite == null:
		_sprite = Sprite2D.new()
		_sprite.name = "Sprite"
		add_child(_sprite)


## Build the static sprite from the prop manifest (uses frame 0 of the sheet) +
## texture. Foot-anchors the sprite so the node origin is the prop's base.
func set_manifest(manifest: Dictionary, sheet_texture: Texture2D) -> void:
	if _sprite == null:
		_ready()

	var proj := String(manifest.get("projection", ""))
	if proj != "" and proj != PROJECTION_LOCK:
		push_warning("[PropActor] manifest projection '%s' != lock '%s' (prop=%s)" % [
			proj, PROJECTION_LOCK, prop_id])

	var frame: Dictionary = manifest.get("frame", {}) if typeof(manifest.get("frame", {})) == TYPE_DICTIONARY else {}
	var fw := int(frame.get("w", 128))
	var fh := int(frame.get("h", 192))

	var anchor: Dictionary = manifest.get("anchor", {}) if typeof(manifest.get("anchor", {})) == TYPE_DICTIONARY else {}
	_anchor = Vector2(float(anchor.get("x", fw / 2.0)), float(anchor.get("y", fh)))

	# Single-frame prop: slice frame 0 (top-left cell) out of the sheet.
	var at := AtlasTexture.new()
	at.atlas = sheet_texture
	at.region = Rect2(0, 0, fw, fh)
	_sprite.texture = at
	_sprite.centered = false
	# Place the cell so its base anchor lands on the node origin (foot at origin).
	_sprite.offset = -_anchor


## Immediately place the prop's base at `world_pos` (the node origin == foot).
func place_at(world_pos: Vector2) -> void:
	position = world_pos
