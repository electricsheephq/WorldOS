extends Node2D
## NavOverlay — draws the tactical/nav grid over a backdrop in preview mode.
##
## ROLE: a read-only visual diagnostic tool. Given a nav spec block (from the
## --preview-scene JSON), it draws the dimetric 2:1 diamond grid, walkable/blocked
## cell tints, zone-anchor rings, a solved A* path, and actor foot-dots + facing
## arrows. It is ADDITIVE — only ever added as a child of WorldView in preview mode;
## the normal gameplay path never instantiates it.
##
## SPEC format (nav block inside /tmp/scene.json):
##   {
##     "cols": 12,            // grid width in cells
##     "rows": 8,             // grid height in cells
##     "cell_w_px": 72,       // screen width of one diamond cell in pixels
##     "origin_px": [512, 300],  // screen position of cell (0,0) center
##     "blocked": [[3,3],[8,2]]  // list of [col, row] blocked cells
##   }
##
## GRID TRANSFORM (ISO-PROJECTION.md dimetric 2:1):
##   cell (c, r) center in screen pixels:
##     x = origin_px.x + (c - r) * cell_w_px / 2
##     y = origin_px.y + (c + r) * cell_w_px / 4
##   (cell_w_px/2 is the half-width; cell_h_px = cell_w_px/2 for 2:1 ratio)
##
## OVERLAY MODES (--overlay arg):
##   none  — nothing drawn (no-op)
##   grid  — diamond outlines only (no tints/path/actors)
##   full  — everything (default): grid + tints + path + actors

## Visual style constants (keep subtle — this is a debug overlay, not UI chrome).
const COLOR_WALKABLE   := Color(0.2,  0.9,  0.3,  0.18)  ## green tint for walkable cells
const COLOR_BLOCKED    := Color(0.9,  0.2,  0.2,  0.28)  ## red tint for blocked cells
const COLOR_GRID       := Color(0.8,  0.8,  1.0,  0.35)  ## diamond outline
const COLOR_GRID_EDGE  := Color(0.6,  0.6,  0.8,  0.20)  ## faint outer cells
const COLOR_PATH       := Color(1.0,  0.9,  0.1,  0.90)  ## bright yellow path polyline
const COLOR_PATH_START := Color(0.2,  1.0,  0.2,  1.0)   ## green endpoint marker
const COLOR_PATH_END   := Color(1.0,  0.35, 0.1,  1.0)   ## orange endpoint marker
const COLOR_ZONE_RING  := Color(0.6,  0.9,  1.0,  0.55)  ## zone anchor ring
const COLOR_ACTOR_DOT  := Color(1.0,  1.0,  1.0,  0.9)   ## actor foot dot
const COLOR_ACTOR_FACE := Color(1.0,  0.85, 0.2,  0.85)  ## actor facing arrow

const LINE_WIDTH := 1.5
const PATH_LINE_WIDTH := 3.0

## Parsed from the spec block.
var _cols: int = 0
var _rows: int = 0
var _cell_w: float = 64.0
var _origin: Vector2 = Vector2.ZERO
var _blocked: Array = []        # Array of Vector2i (col, row)

## Resolved path (from _solve_astar).
var _path: Array = []           # Array of Vector2i
var _path_found: bool = false

## Actor data for drawing.
var _actors: Array = []         # Array of {cell: Vector2i, facing: String}

## Zone anchors (optional, "full" mode only).
var _zone_anchors: Array = []   # Array of Vector2 (screen positions)

## Overlay mode: "none", "grid", "full"
var _mode: String = "full"

## Facing string -> unit direction for the 8 facing arrows (dimetric 2:1 space).
## E/W are horizontal, N/S are vertical in screen space; diagonals follow the
## 2:1 tile geometry (dx=1, dy=0.5 for SE).
const FACING_DIRS := {
	"N":  Vector2( 0.0, -1.0),
	"NE": Vector2( 0.7, -0.35),
	"E":  Vector2( 1.0,  0.0),
	"SE": Vector2( 0.7,  0.35),
	"S":  Vector2( 0.0,  1.0),
	"SW": Vector2(-0.7,  0.35),
	"W":  Vector2(-1.0,  0.0),
	"NW": Vector2(-0.7, -0.35),
}


## Apply the nav spec from the preview JSON. Call before add_child so data is ready
## when _draw() fires.
func setup(nav: Dictionary, actors: Array, path_probe: Dictionary, zone_anchors: Array, mode: String) -> void:
	_mode = mode
	if _mode == "none":
		return

	_cols = int(nav.get("cols", 0))
	_rows = int(nav.get("rows", 0))
	_cell_w = float(nav.get("cell_w_px", 64))
	var orig: Variant = nav.get("origin_px", [0, 0])
	if typeof(orig) == TYPE_ARRAY and (orig as Array).size() >= 2:
		_origin = Vector2(float((orig as Array)[0]), float((orig as Array)[1]))

	var blocked_raw: Variant = nav.get("blocked", [])
	if typeof(blocked_raw) == TYPE_ARRAY:
		for b in (blocked_raw as Array):
			if typeof(b) == TYPE_ARRAY and (b as Array).size() >= 2:
				_blocked.append(Vector2i(int((b as Array)[0]), int((b as Array)[1])))

	# Build actor list.
	for a in actors:
		if typeof(a) != TYPE_DICTIONARY:
			continue
		var cell_v: Variant = (a as Dictionary).get("cell", [0, 0])
		if typeof(cell_v) == TYPE_ARRAY and (cell_v as Array).size() >= 2:
			var c := Vector2i(int((cell_v as Array)[0]), int((cell_v as Array)[1]))
			var facing := String((a as Dictionary).get("facing", "S"))
			_actors.append({"cell": c, "facing": facing})

	_zone_anchors = zone_anchors

	if _mode == "full":
		_solve_astar(path_probe)

	queue_redraw()


## Solve A* from path_probe.from -> path_probe.to using AStarGrid2D.
## Stores result in _path / _path_found.
func _solve_astar(probe: Dictionary) -> void:
	if _cols <= 0 or _rows <= 0:
		return
	var from_v: Variant = probe.get("from", null)
	var to_v: Variant = probe.get("to", null)
	if from_v == null or to_v == null:
		return
	if typeof(from_v) != TYPE_ARRAY or typeof(to_v) != TYPE_ARRAY:
		return
	var from_a := from_v as Array
	var to_a := to_v as Array
	if from_a.size() < 2 or to_a.size() < 2:
		return

	var from := Vector2i(int(from_a[0]), int(from_a[1]))
	var to   := Vector2i(int(to_a[0]),   int(to_a[1]))

	var grid := AStarGrid2D.new()
	grid.region = Rect2i(0, 0, _cols, _rows)
	# For dimetric 2:1 we allow diagonal movement when at least one neighbour walkable.
	grid.diagonal_mode = AStarGrid2D.DIAGONAL_MODE_AT_LEAST_ONE_WALKABLE
	# AStarGrid2D defaults to square cells; the dimetric aspect is handled in the
	# screen-space draw, not in the pathfinding itself (grid topology is square).
	grid.cell_shape = AStarGrid2D.CELL_SHAPE_SQUARE
	grid.update()

	for b in _blocked:
		grid.set_point_solid(b, true)

	# If the to-cell is solid, the path is trivially unreachable.
	if grid.is_point_solid(to):
		_path_found = false
		print("[NavOverlay] A* to=%s is blocked -> path_found=false" % str(to))
		return

	var id_path: PackedVector2Array = grid.get_id_path(from, to)
	if id_path.is_empty():
		_path_found = false
		print("[NavOverlay] A* from=%s to=%s -> no path" % [str(from), str(to)])
		return

	_path_found = true
	for p in id_path:
		_path.append(Vector2i(int(p.x), int(p.y)))
	print("[NavOverlay] A* path_found=true length=%d" % _path.size())


## Convert a grid cell (c, r) to screen position using the ISO-PROJECTION.md transform:
##   x = origin.x + (c - r) * cell_w / 2
##   y = origin.y + (c + r) * cell_w / 4
func _cell_to_screen(c: int, r: int) -> Vector2:
	var half_w := _cell_w * 0.5
	var quarter_w := _cell_w * 0.25
	return Vector2(
		_origin.x + float(c - r) * half_w,
		_origin.y + float(c + r) * quarter_w
	)


## The four screen-space corners of a dimetric diamond for cell (c, r).
## Clockwise from top: top, right, bottom, left.
func _diamond_points(c: int, r: int) -> PackedVector2Array:
	var ctr := _cell_to_screen(c, r)
	var hw := _cell_w * 0.5   # half diamond width
	var hh := _cell_w * 0.25  # half diamond height (2:1 ratio)
	return PackedVector2Array([
		ctr + Vector2(0.0, -hh),   # top
		ctr + Vector2(hw,  0.0),   # right
		ctr + Vector2(0.0,  hh),   # bottom
		ctr + Vector2(-hw, 0.0),   # left
	])


func _draw() -> void:
	if _mode == "none" or _cols <= 0 or _rows <= 0:
		return

	var blocked_set := {}
	for b in _blocked:
		blocked_set[b] = true

	# --- Draw cell tints (full mode only, under the grid lines). ---
	# draw_polygon(points, colors) takes a per-vertex PackedColorArray (Godot 4 CanvasItem API).
	if _mode == "full":
		for r in range(_rows):
			for c in range(_cols):
				var cell := Vector2i(c, r)
				var pts := _diamond_points(c, r)
				var tint: Color = COLOR_BLOCKED if blocked_set.has(cell) else COLOR_WALKABLE
				draw_polygon(pts, PackedColorArray([tint, tint, tint, tint]))

	# --- Draw grid diamond outlines. ---
	for r in range(_rows):
		for c in range(_cols):
			var pts := _diamond_points(c, r)
			# Close the polygon by appending the first point.
			var closed := PackedVector2Array(pts)
			closed.append(pts[0])
			draw_polyline(closed, COLOR_GRID, LINE_WIDTH, true)

	if _mode != "full":
		return

	# --- Zone anchor rings (full mode). ---
	for anchor in _zone_anchors:
		draw_arc(anchor, 10.0, 0.0, TAU, 20, COLOR_ZONE_RING, 2.0, true)

	# --- A* path polyline + endpoints (full mode). ---
	if _path_found and _path.size() >= 2:
		var screen_pts := PackedVector2Array()
		for cell in _path:
			screen_pts.append(_cell_to_screen(cell.x, cell.y))
		draw_polyline(screen_pts, COLOR_PATH, PATH_LINE_WIDTH, true)
		# Start marker (green circle).
		draw_circle(_cell_to_screen(_path[0].x, _path[0].y), 6.0, COLOR_PATH_START)
		# End marker (orange circle).
		var last: Vector2i = _path[_path.size() - 1]
		draw_circle(_cell_to_screen(last.x, last.y), 6.0, COLOR_PATH_END)

	# --- Actor foot dots + facing arrows (full mode). ---
	for actor in _actors:
		var cell: Vector2i = actor["cell"]
		var facing: String = actor["facing"]
		var foot := _cell_to_screen(cell.x, cell.y)
		# Foot dot.
		draw_circle(foot, 5.0, COLOR_ACTOR_DOT)
		# Facing arrow: draw a line from the foot in the facing direction.
		var dir: Vector2 = FACING_DIRS.get(facing, Vector2(0.0, 1.0))
		var arrow_tip := foot + dir * 18.0
		draw_line(foot, arrow_tip, COLOR_ACTOR_FACE, 2.0, true)
		# Arrow head (two short lines branching from the tip).
		var perp := Vector2(-dir.y, dir.x) * 5.0
		draw_line(arrow_tip, arrow_tip - dir * 6.0 + perp, COLOR_ACTOR_FACE, 1.5, true)
		draw_line(arrow_tip, arrow_tip - dir * 6.0 - perp, COLOR_ACTOR_FACE, 1.5, true)


## Export nav results as a Dictionary for writing to <shot>.nav.json.
func nav_result() -> Dictionary:
	var path_arr := []
	for cell in _path:
		path_arr.append([cell.x, cell.y])
	return {
		"path_found": _path_found,
		"path": path_arr,
		"blocked_count": _blocked.size(),
		"cols": _cols,
		"rows": _rows,
	}
