extends Node2D
## Main — the #1052 boot smoke.
##
## This is NOT the real scene graph (WorldView / CharacterToken / Y-sort /
## click-to-move are #1053–#1055). Its only job is to prove the transport end to
## end: connect to SurfaceClient, and on the FIRST snapshot print the current
## location name, the party member names, and the transport mode. With no server
## running, SurfaceClient falls back to bundled fixtures, so this smoke prints from
## res://fixtures/* and exits cleanly.

@onready var _label: Label = $UI/SmokeLabel

var _got_first_snapshot: bool = false
var _last_mode: String = "?"


func _ready() -> void:
	SurfaceClient.transport_mode_changed.connect(_on_mode_changed)
	SurfaceClient.snapshot_updated.connect(_on_snapshot)
	SurfaceClient.events_appended.connect(_on_events)
	_set_label("WorldOS GT2 (Godot) — connecting…")


func _on_mode_changed(mode: String) -> void:
	_last_mode = mode


func _on_snapshot(atlas: Dictionary, _combat: Dictionary, character: Dictionary) -> void:
	# Extract the smoke facts from the read-only surfaces (no client-side rules).
	var location_name := _location_name(atlas)
	var party_names := _party_names(character)
	var mode := SurfaceClient.mode()
	if mode == "":
		mode = _last_mode

	var party_joined := ", ".join(party_names) if not party_names.is_empty() else "<empty>"
	var line := "[%s] location=%s | party=%s" % [mode, location_name, party_joined]

	# TRANSPORT SMOKE PROOF.
	print("[Main] SMOKE ", line)
	_set_label("WorldOS GT2 (Godot)\n%s\nlocation: %s\nparty: %s" % [mode, location_name, party_joined])

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
	if typeof(cur) == TYPE_DICTIONARY and cur.has("name"):
		return String(cur["name"])
	if atlas.has("current_location_id"):
		return String(atlas["current_location_id"])
	return "<unknown>"


func _party_names(character: Dictionary) -> PackedStringArray:
	var out := PackedStringArray()
	var party: Variant = character.get("party", [])
	if typeof(party) != TYPE_ARRAY:
		return out
	for member in party:
		if typeof(member) == TYPE_DICTIONARY and member.has("name"):
			out.append(String(member["name"]))
	return out


# ---------------------------------------------------------------------------
# Headless / smoke quit. Quit after the first snapshot when running headless or
# when launched with a --smoke user arg, so CI can boot-and-check cleanly.
# ---------------------------------------------------------------------------
func _maybe_quit() -> void:
	var smoke_flag := OS.get_cmdline_user_args().has("--smoke")
	var headless := DisplayServer.get_name() == "headless"
	if smoke_flag or headless:
		# Defer the quit one frame so the print/label flush before teardown.
		call_deferred("_quit_clean")


func _quit_clean() -> void:
	print("[Main] smoke complete — quitting cleanly")
	get_tree().quit()


func _set_label(text: String) -> void:
	if is_instance_valid(_label):
		_label.text = text
