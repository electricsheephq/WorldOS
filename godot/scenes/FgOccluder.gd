extends Node2D
class_name FgOccluder
## FgOccluder — a foreground prop occluder for Y-sort depth ordering (#iso-occlusion).
##
## CONTRACT ROLE: a painted foreground element (e.g. the tavern table, the bar counter)
## that participates in YSortLayer depth ordering so actors standing BEHIND it are
## drawn beneath it and actors in FRONT draw over it. The node ORIGIN is set to the
## prop's FOOT/BASELINE (the y at which the prop contacts the floor), so the Y-sort
## engine uses this y as the depth key — exactly the same contract as CharacterToken
## and PropActor.
##
## TWO MODES (selected at construction):
##   1. SPRITE — a full-frame RGBA PNG cut-out (e.g. from SAM segmentation). The PNG
##      is a full-image-size transparent cut: its dimensions match the backdrop, the
##      table pixels are opaque, everything else is transparent. The sprite is placed
##      at the same transform as the backdrop (centered at vp/2, scaled to fill), so
##      the table pixels land on the EXACT same screen pixels as in the painted backdrop.
##      The backdrop's table region is then hidden behind the occluder's alpha channel
##      (since the sprite is on top and is opaque only where the table is).
##
##   2. POLYGON — a coarse Polygon2D covering the table region, with the prop's painted
##      color (or a faint tint matching the backdrop). Less accurate but zero dependency
##      on SAM. The polygon's vertices are in WorldView-local screen coordinates.
##
## In both modes: place this node in the YSortLayer. Its `position.y` is its baseline_y
## (floor contact). Actors whose foot-y < baseline_y (further back) render BEHIND this
## node; actors with foot-y > baseline_y (closer) render in FRONT.
##
## OWNS NO GAME STATE. Static and non-interactive.

## Identifier for diagnostic prints.
var occluder_id: String = "fg_occluder"

## The floor-contact y for this occluder in WorldView-local coords. This IS the node's
## position.y (set via place_at), so the Y-sort key is the floor contact.
var baseline_y: float = 0.0

## Mode tracking.
var _mode: String = "none"  ## "sprite" or "polygon"
var _sprite: Sprite2D = null
var _polygon: Polygon2D = null


## Set up this occluder as a full-frame sprite cut-out (mode "sprite").
##
## `texture` — the RGBA cut PNG (SAM output with applyMask=true). Must be the same
##   pixel dimensions as the backdrop so no transform mismatch occurs.
## `backdrop_size` — the original texture's pixel size (e.g. Vector2(1344,768)).
## `viewport_size` — the current viewport size so the sprite scale/position matches
##   the backdrop Sprite2D exactly (same centered + cover-scale transform).
## `foot_y_screen` — the WorldView-local y coordinate of the table's floor contact.
##   This sets position.y so Y-sort uses it as the depth key.
func setup_sprite(texture: Texture2D, backdrop_size: Vector2, viewport_size: Vector2, foot_y_screen: float) -> void:
	_mode = "sprite"
	if _sprite == null:
		_sprite = Sprite2D.new()
		_sprite.name = "OccluderSprite"
		add_child(_sprite)
	if _polygon != null:
		_polygon.visible = false

	_sprite.texture = texture
	_sprite.centered = true

	# Match the backdrop's cover-scale: same logic as WorldView._apply_texture_backdrop.
	var sx := viewport_size.x / backdrop_size.x
	var sy := viewport_size.y / backdrop_size.y
	var s := maxf(sx, sy)
	_sprite.scale = Vector2(s, s)

	# The backdrop Sprite2D is centered at vp/2 in WorldView-local space. To overlap
	# it exactly, our Sprite2D must be centered at the same point — but since THIS node's
	# origin is at (0, foot_y_screen), we offset the sprite by (vp/2 - our_origin).
	# our_origin x = vp/2 (we call place_at(Vector2(vp/2, foot_y_screen)) below).
	# So sprite offset = Vector2(0, -(foot_y_screen - vp/2)) relative to origin.
	_sprite.position = Vector2(0.0, viewport_size.y * 0.5 - foot_y_screen)

	# Our node origin = foot of the table (floor contact).
	place_at(Vector2(viewport_size.x * 0.5, foot_y_screen))
	print("[FgOccluder] id=%s mode=sprite foot_y=%.0f scale=%.3f" % [occluder_id, foot_y_screen, s])


## Set up this occluder as a coarse polygon (mode "polygon").
##
## `poly_points` — Array of Vector2 in WorldView-local screen coords that outline the
##   prop (the table region). This is the convex or arbitrary polygon.
## `color` — fill color matching the backdrop's table tone (or a slight tint). Alpha
##   should be 1.0 so the polygon fully covers actors behind it.
## `foot_y_screen` — the y of the polygon's floor contact line (bottom edge of table).
func setup_polygon(poly_points: PackedVector2Array, color: Color, foot_y_screen: float) -> void:
	_mode = "polygon"
	if _polygon == null:
		_polygon = Polygon2D.new()
		_polygon.name = "OccluderPolygon"
		add_child(_polygon)
	if _sprite != null:
		_sprite.visible = false

	# The polygon points are in WorldView-local space; since the node origin is at
	# (vp/2, foot_y_screen), we keep the polygon in local-to-node coords by
	# shifting each point by -position.
	# Actually: place the node at (0, foot_y_screen) and give the polygon points
	# relative to that origin. The caller passes absolute screen coords, so we
	# subtract the node's x origin (vp/2) — but for a polygon it's simpler to just
	# place the node at (0, 0) and let the polygon hold screen coords directly, then
	# set position.y to foot_y_screen for the Y-sort key.
	_polygon.polygon = poly_points
	_polygon.color = color

	place_at(Vector2(0.0, foot_y_screen))
	print("[FgOccluder] id=%s mode=polygon points=%d foot_y=%.0f" % [occluder_id, poly_points.size(), foot_y_screen])


## Place the node so its foot (baseline_y) lands at `world_pos` and the Y-sort key
## is world_pos.y. Call AFTER setup_sprite/setup_polygon.
func place_at(world_pos: Vector2) -> void:
	baseline_y = world_pos.y
	position = world_pos


## True when the occluder has been set up and is visible.
func is_ready() -> bool:
	return _mode != "none"
