extends CanvasLayer
## Hud — read-only chrome OUTSIDE the world Y-sort (#1053).
##
## CONTRACT ROLE: a thin presentation overlay. It shows the transport mode
## (LIVE/FIXTURE), the current location name, and a one-line party roster from
## character.party — and NOTHING else. No interaction, no game state, no rules.
## Living on a CanvasLayer keeps it above the Node2D world and excludes it from the
## scene's Y-sort (it must never be depth-sorted with tokens).
##
## SCOPE (#1053): display only. Interactive chrome (click-to-travel exits, combat
## initiative strip) is later work; this is the minimal readable status overlay.

## Banner colors per transport mode (LIVE = healthy green-ish, FIXTURE = amber).
const COLOR_LIVE := Color(0.53, 0.80, 0.53)
const COLOR_FIXTURE := Color(0.80, 0.67, 0.40)
const COLOR_NEUTRAL := Color(0.80, 0.88, 0.93)

@onready var _mode_label: Label = $Panel/Mode
@onready var _location_label: Label = $Panel/Location
@onready var _party_label: Label = $Panel/Party

var _mode: String = "?"
var _location: String = "—"
var _party_line: String = "—"


func _ready() -> void:
	_redraw()


## Connected to SurfaceClient.transport_mode_changed by Main.
func set_mode(mode: String) -> void:
	_mode = mode
	_redraw()


## Connected to SurfaceClient.snapshot_updated by Main — pull the read-only display
## facts (location name + party roster). Ignores all other surface fields.
func apply_snapshot(atlas: Dictionary, _combat: Dictionary, character: Dictionary) -> void:
	_location = _location_name(atlas)
	_party_line = _party_roster(character)
	_redraw()


func _redraw() -> void:
	if not is_instance_valid(_mode_label):
		return
	_mode_label.text = "transport: %s" % _mode
	_mode_label.add_theme_color_override("font_color", _mode_color())
	_location_label.text = "location: %s" % _location
	_party_label.text = "party: %s" % _party_line


func _mode_color() -> Color:
	match _mode:
		"LIVE":
			return COLOR_LIVE
		"FIXTURE":
			return COLOR_FIXTURE
		_:
			return COLOR_NEUTRAL


# ---------------------------------------------------------------------------
# Read-only surface extraction (mirrors viewer/server.py surface shapes).
# ---------------------------------------------------------------------------
func _location_name(atlas: Dictionary) -> String:
	var cur: Variant = atlas.get("current_location", null)
	if typeof(cur) == TYPE_DICTIONARY and (cur as Dictionary).has("name"):
		return String((cur as Dictionary)["name"])
	if atlas.has("current_location_id"):
		return String(atlas["current_location_id"])
	return "—"


func _party_roster(character: Dictionary) -> String:
	var party: Variant = character.get("party", [])
	if typeof(party) != TYPE_ARRAY:
		return "—"
	var names: PackedStringArray = PackedStringArray()
	for member in party:
		if typeof(member) == TYPE_DICTIONARY and (member as Dictionary).has("name"):
			names.append(String((member as Dictionary)["name"]))
	if names.is_empty():
		return "<empty>"
	return ", ".join(names)
