extends Node2D
## Main — the renderer root that wires the transport to the scene (#1053 + #1055).
##
## ROLE: the composition root. It instances WorldView (the snapshot→scene
## projection) + Hud (read-only chrome) and connects them to the SINGLE transport
## boundary, SurfaceClient:
##   - snapshot_updated  → WorldView.apply_snapshot + Hud.apply_snapshot
##   - transport_mode_changed → Hud.set_mode
##   - events_appended   → WorldView.enqueue_replay (zone_move facing beats; #1055)
##
## #1055 ALSO wires the renderer's ONLY write gesture: an InputController that turns
## a left-click into a constrained move INTENT, an FxLayer for the optimistic click
## ping, and routes InputController.intent_requested → SurfaceClient.move(). The
## engine owns the destination (the next snapshot moves the token); the renderer
## owns the path + feedback.
##
## It owns NO game state and contains NO rules — it only routes signals. The
## headless boot smoke (location + party + mode prints, then a clean quit) is
## preserved here so CI / --headless validation stays observable end-to-end:
## with no server running, SurfaceClient falls back to res://fixtures/* (FIXTURE
## mode) and the scene projects those.

const FxLayerScene := preload("res://scenes/FxLayer.gd")
const InputControllerScene := preload("res://scenes/InputController.gd")

@onready var _world: Node2D = $WorldView
@onready var _hud: CanvasLayer = $Hud

var _input: InputController = null
var _fx: FxLayer = null

var _got_first_snapshot: bool = false
var _last_mode: String = "?"


func _ready() -> void:
	# #1055: build the optimistic-ping layer (above the world) + the input
	# controller (the only write gesture), then wire the transport → scene.
	_fx = FxLayerScene.new()
	_fx.name = "FxLayer"
	# Parent to WorldView so the ping shares the WorldView-local coord space the
	# clicks are resolved in; its absolute z_index keeps it above the Y-sorted actors.
	_world.add_child(_fx)

	_input = InputControllerScene.new()
	_input.name = "InputController"
	add_child(_input)
	_input.setup(_world, _fx, SurfaceClient)
	# The renderer's ONLY write: route the constrained intent to POST /move.
	_input.intent_requested.connect(_on_intent_requested)

	# Wire the transport → scene. WorldView projects the snapshot into the world;
	# Hud mirrors the read-only display facts; replay carries zone_move facing beats.
	SurfaceClient.transport_mode_changed.connect(_on_mode_changed)
	SurfaceClient.snapshot_updated.connect(_on_snapshot)
	SurfaceClient.snapshot_updated.connect(_world.apply_snapshot)
	SurfaceClient.snapshot_updated.connect(_hud.apply_snapshot)
	SurfaceClient.events_appended.connect(_on_events)
	SurfaceClient.events_appended.connect(_world.enqueue_replay)


func _on_mode_changed(mode: String) -> void:
	_last_mode = mode
	_hud.set_mode(mode)


## The renderer's only write. InputController already enforced the frozen-vocab
## allowlist; here we just hand the intent to the transport (FIXTURE mode echoes a
## benign ok + logs what WOULD have been POSTed — it does NOT pretend to reach a
## live engine).
func _on_intent_requested(intent: Dictionary) -> void:
	var res: Dictionary = await SurfaceClient.move(intent)
	print("[Main] /move intent=%s -> ok=%s reason=%s" % [str(intent), str(res.get("ok", false)), str(res.get("reason", ""))])


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
		# #1055 deterministic validation paths run AFTER the scene is built. The flags
		# may arrive before OR after the `--` user-args separator, so check both arg
		# lists (the spec invokes `--demo-occlusion` without a `--` separator).
		if _has_arg("--smoke-intent"):
			call_deferred("_run_smoke_intent")
			return
		if _has_arg("--served-finals-smoke"):
			call_deferred("_run_served_finals_smoke")
			return
		if _has_arg("--demo-occlusion"):
			call_deferred("_run_demo_occlusion")
			return
		_maybe_quit()


## True if `flag` appears in either the engine args or the post-`--` user args.
func _has_arg(flag: String) -> bool:
	return OS.get_cmdline_user_args().has(flag) or OS.get_cmdline_args().has(flag)


func _on_events(records: Array) -> void:
	print("[Main] events_appended: %d record(s)" % records.size())


# ---------------------------------------------------------------------------
# #1055 SMOKE-INTENT: simulate a floor click at a known zone's screen anchor and
# print the emitted intent + the derived facing, asserting ONLY frozen kinds. This
# is the headless logic proof (no window needed). Runs deterministically then quits.
# ---------------------------------------------------------------------------
func _run_smoke_intent() -> void:
	print("[Main] --smoke-intent: simulating a floor click at a known zone anchor")

	# Pick two zones so we can prove (a) a click→move_to_zone intent and (b) a
	# renderer-DERIVED facing from one zone's anchor to another. The fixtures expose
	# "the gate approach", "the market row", "the alley mouth".
	var from_zone := "the gate approach"
	var to_zone := "the market row"
	var to_pos: Vector2 = _world.zone_screen_pos(to_zone)
	var from_pos: Vector2 = _world.zone_screen_pos(from_zone)

	# (1) Simulate a click AT the destination zone's floor anchor → expect a
	# move_to_zone intent snapped to that zone.
	var intent: Dictionary = _input.simulate_click(to_pos)
	var ok_kind := InputController.ALLOWED_KINDS.has(String(intent.get("kind", "")))
	print("[SmokeIntent] intent=%s frozen_kind=%s" % [str(intent), str(ok_kind)])

	# (2) Derive the facing for a move from `from_zone`'s anchor to `to_zone`'s
	# anchor — the same FacingResolver call WorldView makes on a zone change.
	var facing := FacingResolver.octant(from_pos, to_pos, _world.FACING_ORDER)
	print("[Facing] move %s->%s => %s" % [from_zone, to_zone, facing])

	# (3) Assert ONLY frozen kinds can be emitted: try an illegal kind and confirm
	# the controller refuses it (returns {}). Proves the renderer cannot widen the
	# frozen /move vocabulary.
	var blocked: Dictionary = _input._emit({"kind": "teleport", "target": "x"}, Vector2.ZERO, "illegal")
	print("[SmokeIntent] illegal-kind blocked=%s" % str(blocked.is_empty()))

	# (4) Also exercise the travel path with a verbatim option `move` (from the
	# atlas travel_options) to prove travel is emitted VERBATIM + frozen.
	var topts: Array = _world.travel_options()
	if not topts.is_empty() and typeof(topts[0]) == TYPE_DICTIONARY:
		var t_intent: Dictionary = _input.simulate_travel_click(topts[0])
		var t_frozen := InputController.ALLOWED_KINDS.has(String(t_intent.get("kind", "")))
		print("[SmokeIntent] travel intent=%s frozen_kind=%s" % [str(t_intent), str(t_frozen)])

	# Final assertion summary line for the validation grep.
	var is_move_to_zone := String(intent.get("kind", "")) == "move_to_zone"
	var all_ok: bool = ok_kind and blocked.is_empty() and is_move_to_zone
	print("[SmokeIntent] RESULT frozen-vocab-only=%s move_to_zone-emitted=%s" % [str(all_ok), str(is_move_to_zone)])

	call_deferred("_quit_clean")


# ---------------------------------------------------------------------------
# #1063 part 2 SERVED-FINALS-SMOKE: prove a SERVED sprite atlas (fetched from a live
# /image?scope=sprite-aubree-iso8 stub) is resolved by the ImageResolver AND swapped
# onto the spawned token (re-sliced from the render-profile layout → 32 anims). The
# token spawn already kicked an async resolve(); we await texture_ready (bounded), then
# assert (a) the resolver cached the served atlas and (b) the token carries 32 anims
# built from it. If NO atlas is served (the stub absent / 404), this is a clean MISS —
# the committed placeholder still yields 32 anims, but cached==null flags the no-serve
# case so the smoke FAILS LOUDLY rather than passing on the fallback.
# ---------------------------------------------------------------------------
func _run_served_finals_smoke() -> void:
	var scope := "sprite-aubree-iso8"
	print("[Main] --served-finals-smoke: awaiting SERVED atlas scope=%s" % scope)

	# Bounded wait for the async fetch the token spawn already started. We poll the
	# resolver cache each frame (the resolve coroutine emits texture_ready on success;
	# the swap happens in WorldView._on_texture_ready). ~6s ceiling is generous for a
	# localhost stub; a real 404 short-circuits to MISS immediately.
	var cached: Texture2D = null
	var waited := 0.0
	var step := 0.1
	while waited < 6.0:
		cached = ImageResolver.get_cached(scope)
		if cached != null or ImageResolver.is_missing(scope):
			break
		await get_tree().create_timer(step).timeout
		waited += step

	var resolver_ok := cached != null
	print("[ServedFinals] resolver_cached=%s waited=%.1fs missing=%s" % [
		str(resolver_ok), waited, str(ImageResolver.is_missing(scope))])

	# Inspect the token: with the served atlas applied it must still carry the full
	# 32 anims (4 anim-types x 8 facings), sliced from the served PNG via the profile.
	var tok: CharacterToken = _world.token_for("char-aubree")
	var token_present := tok != null and is_instance_valid(tok)
	var anims := tok.animation_count() if token_present else 0
	var anims_ok := anims == 32
	print("[ServedFinals] token_present=%s anims=%d anims_ok=%s" % [
		str(token_present), anims, str(anims_ok)])

	var all_ok := resolver_ok and token_present and anims_ok
	print("[ServedFinals] RESULT served-atlas-applied=%s resolver_cached=%s anims=%d" % [
		str(all_ok), str(resolver_ok), anims])

	call_deferred("_quit_clean")


# ---------------------------------------------------------------------------
# #1055 DEMO-OCCLUSION: the visual Y-sort occlusion proof. Run WITHOUT --headless
# (a real window) so rendering is real. Positions the party token behind, then in
# front of, the pillar (by relative foot-Y), renders a frame each, screenshots to
# /tmp, and programmatically asserts the occlusion by sampling the overlap column.
# ---------------------------------------------------------------------------
func _run_demo_occlusion() -> void:
	print("[Main] --demo-occlusion: Y-sort occlusion proof (behind then front)")
	var tok: CharacterToken = _world.token_for("char-aubree")
	var pillar: PropActor = _world.pillar_prop()
	if tok == null or pillar == null:
		print("[DEMO] cannot run — token or pillar missing (token=%s pillar=%s)" % [str(tok != null), str(pillar != null)])
		call_deferred("_quit_clean")
		return

	# Anchor the token on the pillar's COLUMN (same screen-x) so they overlap, and
	# face the camera (S) so the body fills the overlap column. The pillar foot-Y is
	# the depth pivot: token foot-Y above → behind; below → in front.
	var px := pillar.position.x
	var py := pillar.position.y
	tok.set_facing("S")
	tok.set_anim("idle")

	# --- BEHIND: token foot-Y ABOVE the pillar foot-Y (smaller Y = farther) ---
	tok.place_at(Vector2(px, py - 40.0))
	await _settle_frames(3)
	var behind_img := _capture_viewport()
	if behind_img != null:
		behind_img.save_png("/tmp/wos_godot_occlusion_behind.png")
	var behind_pass := _assert_occlusion(behind_img, tok, pillar, true)

	# --- FRONT: token foot-Y BELOW the pillar foot-Y (larger Y = nearer) ---
	tok.place_at(Vector2(px, py + 40.0))
	await _settle_frames(3)
	var front_img := _capture_viewport()
	if front_img != null:
		front_img.save_png("/tmp/wos_godot_occlusion_front.png")
	var front_pass := _assert_occlusion(front_img, tok, pillar, false)

	print("[DEMO] occlusion behind=%s front=%s" % [
		"pass" if behind_pass else "fail",
		"pass" if front_pass else "fail"])
	print("[DEMO] screenshots: /tmp/wos_godot_occlusion_behind.png /tmp/wos_godot_occlusion_front.png")
	call_deferred("_quit_clean")


## Wait `n` post-draw frames so the viewport texture is actually rendered before we
## read it back (RenderingServer.frame_post_draw fires after each draw).
func _settle_frames(n: int) -> void:
	for _i in range(n):
		await RenderingServer.frame_post_draw


## Read the current viewport back to a CPU Image (null in headless / no real frame).
func _capture_viewport() -> Image:
	var vp := get_viewport()
	if vp == null:
		return null
	var tex := vp.get_texture()
	if tex == null:
		return null
	return tex.get_image()


## Sample the token/pillar overlap column and decide who won the overlap pixels.
## `expect_pillar_wins` true == the "behind" case (the pillar should occlude the
## token); false == the "front" case (the token should occlude the pillar).
##
## The placeholder token is a GREEN body (g > r and g > b; see
## gen_placeholder_sheet.py); the pillar is a NEUTRAL grey column (r≈g≈b). So in the
## overlap column the WINNER is unambiguous by hue: green-dominant ⇒ the token is in
## front; neutral-grey ⇒ the pillar is in front. We sample a band that is inside
## BOTH bodies (just above the HIGHER of the two feet) and classify by greenness.
## Returns true iff the rendered pixels match the expected occluder.
func _assert_occlusion(img: Image, tok: CharacterToken, pillar: PropActor, expect_pillar_wins: bool) -> bool:
	if img == null:
		print("[DEMO] no image captured (headless?) — skipping pixel assertion")
		return false
	var iw := img.get_width()
	var ih := img.get_height()
	# The overlap column is the shared screen-x (token sits on the pillar column).
	var col := clampi(int(round(pillar.position.x)), 0, iw - 1)
	# Sample just ABOVE the higher foot (smaller Y) so we are inside BOTH body cells
	# (both sprites still extend well above this Y). 30px clears the foot/AA edge.
	var sample_y := clampi(int(round(minf(tok.position.y, pillar.position.y) - 30.0)), 0, ih - 1)

	# Average a small vertical band in the overlap column for AA robustness.
	var acc := Color(0, 0, 0, 0)
	var n := 0
	for dy in range(-8, 9, 4):
		acc += img.get_pixel(col, clampi(sample_y + dy, 0, ih - 1))
		n += 1
	var avg := acc / float(max(n, 1))

	# Greenness: how much the green channel exceeds the red/blue average. The token
	# body is clearly green-positive; the grey pillar is ~0. A small threshold keeps
	# the dark backdrop (≈0) on the pillar/neutral side.
	var greenness := avg.g - (avg.r + avg.b) * 0.5
	var token_won := greenness > 0.06
	var pillar_won := not token_won

	print("[DEMO] overlap@(%d,%d) avg=%s greenness=%.3f token_won=%s pillar_won=%s (expect_pillar=%s)" % [
		col, sample_y, _fmt(avg), greenness, str(token_won), str(pillar_won), str(expect_pillar_wins)])
	return pillar_won == expect_pillar_wins


func _fmt(c: Color) -> String:
	return "(%.2f,%.2f,%.2f)" % [c.r, c.g, c.b]


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
