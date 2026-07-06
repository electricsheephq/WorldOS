extends Node
## FacingResolver — derives an 8-way facing name from a screen-space vector.
##
## MINIMAL STUB for #1052. The full derivation (move_to_zone previous→new anchor
## snapping, travel-resets-to-default, combat actor→target_fk snapping, mirror4
## strategy) is #1055. This stub provides only the pure geometry primitive that
## the later issue builds on.
##
## CONTRACT NOTE (ISO-PROJECTION.md): facing is RENDERER-DERIVED, never from the
## engine (the engine has no facing field — the sole-writer invariant forbids one).
## Facing order is the locked dimetric 8-way: ["S","SE","E","NE","N","NW","W","SW"],
## index 0 = South (toward camera), clockwise. This resolver stays PURE — no engine
## calls, no node lookups — so it is trivially unit-testable.

## Default facing when no motion vector exists (e.g. just after a `travel` location
## change). Matches the render-profile's `default_facing`.
var default_facing := "S"


## Snap the screen-space vector (from_pos -> to_pos) to one of the facing names in
## `order`, treating `order` as evenly-spaced compass octants beginning at the
## first entry pointing "toward camera" (screen-down, +Y) and proceeding clockwise.
##
## `order` is the render-profile's `facing_order` (e.g.
## ["S","SE","E","NE","N","NW","W","SW"]). A zero-length vector returns the first
## entry of `order` (its natural "default"/rest facing), or default_facing if the
## order is empty.
func octant(from_pos: Vector2, to_pos: Vector2, order: PackedStringArray) -> String:
	if order.is_empty():
		return default_facing
	var delta := to_pos - from_pos
	if delta.length_squared() == 0.0:
		return order[0]

	var n := order.size()
	# Screen space: +X is right, +Y is DOWN. Index 0 of `order` ("S") faces the
	# camera (screen-down). We measure the clockwise angle starting from +Y so
	# octant 0 centers on straight-down, then step clockwise (toward -X / "W"-ish)
	# the way the locked facing order rotates.
	#
	# Clockwise-from-down angle: down=(0,1)->0; right=(1,0)->? We want the order
	# S,SE,E,NE,N,NW,W,SW to map increasing index to clockwise rotation. With +Y
	# down, "clockwise on screen" goes S -> SW -> W ... visually, but the LOCKED
	# order is S,SE,E... (the other rotational sense). So we measure the angle in
	# the sense that makes index increase match the order: start at +Y, rotate
	# toward +X (screen-right) first.
	var ang := atan2(delta.x, delta.y)  # 0 at +Y(down); +pi/2 at +X(right)
	if ang < 0.0:
		ang += TAU
	var step := TAU / float(n)
	# Round to nearest octant center (offset by half a step so each name owns a
	# symmetric wedge), then wrap into range.
	var idx := int(round(ang / step)) % n
	return order[idx]
