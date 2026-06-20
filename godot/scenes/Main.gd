extends Node2D
## Main — the renderer root that wires the transport to the scene (#1053).
##
## ROLE: the composition root. It instances WorldView (the snapshot→scene
## projection) + Hud (read-only chrome) and connects them to the SINGLE transport
## boundary, SurfaceClient:
##   - snapshot_updated  → WorldView.apply_snapshot + Hud.apply_snapshot
##   - transport_mode_changed → Hud.set_mode
##   - events_appended   → WorldView.enqueue_replay (a #1055/combat stub today)
##
## It owns NO game state and contains NO rules — it only routes signals. The
## headless boot smoke (location + party + mode prints, then a clean quit) is
## preserved here so CI / --headless validation stays observable end-to-end:
## with no server running, SurfaceClient falls back to res://fixtures/* (FIXTURE
## mode) and the scene projects those.

@onready var _world: Node2D = $WorldView
@onready var _hud: CanvasLayer = $Hud

var _got_first_snapshot: bool = false
var _last_mode: String = "?"


func _ready() -> void:
	# Wire the transport → scene. WorldView projects the snapshot into the world;
	# Hud mirrors the read-only display facts; replay is a stub for now.
	SurfaceClient.transport_mode_changed.connect(_on_mode_changed)
	SurfaceClient.snapshot_updated.connect(_on_snapshot)
	SurfaceClient.snapshot_updated.connect(_world.apply_snapshot)
	SurfaceClient.snapshot_updated.connect(_hud.apply_snapshot)
	SurfaceClient.events_appended.connect(_on_events)
	SurfaceClient.events_appended.connect(_world.enqueue_replay)


func _on_mode_changed(mode: String) -> void:
	_last_mode = mode
	_hud.set_mode(mode)


func _on_snapshot(atlas: Dictionary, _combat: Dictionary, character: Dictionary) -> void:
	# Headless smoke: keep printing the transport facts (location + party + mode) so
	# validation stays observable. The WorldView prints the projection facts itself
	# (location, zone-marker count, backdrop status) in its own apply_snapshot.
	var location_name := _location_name(atlas)
	var party_names := _party_names(character)
	var mode := SurfaceClient.mode()
	if mode == "":
		mode = _last_mode
	var party_joined := ", ".join(party_names) if not party_names.is_empty() else "<empty>"
	print("[Main] SMOKE [%s] location=%s | party=%s" % [mode, location_name, party_joined])

	if not _got_first_snapshot:
		_got_first_snapshot = true
		_maybe_quit()


func _on_events(records: Array) -> void:
	print("[Main] events_appended: %d record(s)" % records.size())


# ---------------------------------------------------------------------------
# Read-only surface extraction (shapes mirror viewer/server.py surfaces).
# ---------------------------------------------------------------------------
func _location_name(atlas: Dictionary) -> String:
	var cur: Variant = atlas.get("current_location", null)
	if typeof(cur) == TYPE_DICTIONARY and (cur as Dictionary).has("name"):
		return String((cur as Dictionary)["name"])
	if atlas.has("current_location_id"):
		return String(atlas["current_location_id"])
	return "<unknown>"


func _party_names(character: Dictionary) -> PackedStringArray:
	var out := PackedStringArray()
	var party: Variant = character.get("party", [])
	if typeof(party) != TYPE_ARRAY:
		return out
	for member in party:
		if typeof(member) == TYPE_DICTIONARY and (member as Dictionary).has("name"):
			out.append(String((member as Dictionary)["name"]))
	return out


# ---------------------------------------------------------------------------
# Headless / smoke quit. Quit after the first snapshot when running headless or
# when launched with a --smoke user arg, so CI can boot-and-check cleanly.
# ---------------------------------------------------------------------------
func _maybe_quit() -> void:
	var smoke_flag := OS.get_cmdline_user_args().has("--smoke")
	var headless := DisplayServer.get_name() == "headless"
	if smoke_flag or headless:
		# Defer the quit one frame so the prints flush before teardown.
		call_deferred("_quit_clean")


func _quit_clean() -> void:
	print("[Main] smoke complete — quitting cleanly")
	get_tree().quit()
