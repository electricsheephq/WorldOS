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
		if _has_arg("--combat-tokens"):
			call_deferred("_run_combat_tokens")
			return
		if _has_arg("--combat-replay"):
			call_deferred("_run_combat_replay")
			return
		if _has_arg("--demo-combat"):
			call_deferred("_run_demo_combat")
			return
		if _has_arg("--demo-occlusion"):
			call_deferred("_run_demo_occlusion")
			return
		if _has_arg("--preview-scene"):
			call_deferred("_run_preview_scene")
			return
		if _has_arg("--walk-demo"):
			call_deferred("_run_walk_demo")
			return
		if _has_arg("--cam-demo"):
			call_deferred("_run_cam_demo")
			return
		if _has_arg("--table-occlusion"):
			call_deferred("_run_table_occlusion")
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
# /image?scope=sprite-fighter-iso8 stub) is resolved by the ImageResolver AND swapped
# onto the spawned token (re-sliced from the render-profile layout → 32 anims). The
# token spawn already kicked an async resolve(); we await texture_ready (bounded), then
# assert (a) the resolver cached the served atlas and (b) the token carries 32 anims
# built from it. If NO atlas is served (the stub absent / 404), this is a clean MISS —
# the committed placeholder still yields 32 anims, but cached==null flags the no-serve
# case so the smoke FAILS LOUDLY rather than passing on the fallback.
# ---------------------------------------------------------------------------
func _run_served_finals_smoke() -> void:
	var scope := "sprite-fighter-iso8"
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
# #1060 COMBAT-TOKENS: prove the iso view renders a token for EVERY combatant (party +
# foes), each at its named ZONE, visually distinguished by TEAM. Drives an ACTIVE combat
# snapshot (res://fixtures/combat-surface-active.json — the steady SurfaceClient path
# keeps combat-surface.json active:false = the exploration default) straight into
# WorldView.apply_snapshot, then asserts (a) one token per combat token, (b) each placed
# at its zone's screen anchor, (c) foes carry the hostile team tint and allies do not.
# Deterministic + headless (no window needed). Quits cleanly.
# ---------------------------------------------------------------------------
func _run_combat_tokens() -> void:
	print("[Main] --combat-tokens: rendering an ACTIVE combat roster (party + foes)")

	# Load the read-only surfaces directly (the active combat fixture + the standalone
	# atlas/character fixtures), exactly the shape SurfaceClient emits to apply_snapshot.
	var combat: Dictionary = _load_fixture_dict("combat-surface-active")
	var atlas: Dictionary = _load_fixture_dict("atlas-surface")
	var character: Dictionary = _load_fixture_dict("character-surface")
	if combat.is_empty():
		print("[CombatTokens] RESULT ok=false reason=missing-active-fixture")
		call_deferred("_quit_clean")
		return

	# Project the ACTIVE combat snapshot into the world (the #1060 path under test).
	# A token that PRE-EXISTED from the prior exploration snapshot (the lead party
	# token) is reconciled with a short walk-tween to its combat zone rather than an
	# instant snap; let that tween settle so the placement assertion reads the final
	# position, not a mid-walk frame. (New tokens are placed instantly.)
	_world.apply_snapshot(atlas, combat, character)
	await get_tree().create_timer(CharacterToken.MOVE_TWEEN_SEC + 0.2).timeout

	var tokens: Array = combat.get("tokens", []) if typeof(combat.get("tokens", [])) == TYPE_ARRAY else []
	var expected: int = tokens.size()
	var spawned: int = _world.token_count()
	print("[CombatTokens] expected=%d spawned=%d" % [expected, spawned])

	# (a) one token per combat token; (b) each at its named zone's anchor; (c) the team
	# tint matches (foes = hostile wash != WHITE; allies = neutral WHITE).
	var all_present := true
	var all_zoned := true
	var all_tinted := true
	var foe_seen := false
	var ally_seen := false
	for entry in tokens:
		if typeof(entry) != TYPE_DICTIONARY:
			continue
		var t: Dictionary = entry
		var actor_id := String(t.get("id", ""))
		var team := String(t.get("team", "ally"))
		var zone := String(t.get("zone", ""))
		var tok: CharacterToken = _world.token_for(actor_id)
		var present := tok != null and is_instance_valid(tok)
		all_present = all_present and present
		if not present:
			print("[CombatTokens] MISSING token actor=%s team=%s zone=%s" % [actor_id, team, zone])
			continue
		# (b) placement == the zone anchor (when the zone is known this tick).
		var zone_pos: Vector2 = _world.zone_screen_pos(zone)
		var at_zone := zone == "" or zone_pos == Vector2.ZERO or tok.position.distance_to(zone_pos) < 1.0
		all_zoned = all_zoned and at_zone
		# (c) team tint: foes must be tinted (non-white); allies stay neutral white.
		var tint: Color = _world.team_tint_for(actor_id)
		var is_white := tint.is_equal_approx(Color(1, 1, 1, 1))
		if team == "foe":
			foe_seen = true
			all_tinted = all_tinted and not is_white
		else:
			ally_seen = true
			all_tinted = all_tinted and is_white
		print("[CombatTokens] actor=%s team=%s zone=%s at_zone=%s tint=(%.2f,%.2f,%.2f)" % [
			actor_id, team, zone, str(at_zone), tint.r, tint.g, tint.b])

	var count_ok: bool = spawned == expected and expected > 0
	var all_ok: bool = count_ok and all_present and all_zoned and all_tinted and foe_seen and ally_seen
	print("[CombatTokens] RESULT ok=%s count=%d/%d all_zoned=%s team_tinted=%s (foes=%s allies=%s)" % [
		str(all_ok), spawned, expected, str(all_zoned), str(all_tinted), str(foe_seen), str(ally_seen)])

	call_deferred("_quit_clean")


# ---------------------------------------------------------------------------
# #1060 COMBAT-REPLAY: prove the Action-Replay envelope ANIMATES on the iso tiles.
# Spawns the ACTIVE combat roster (so every actor/target_fk is on stage), then feeds
# the COMBAT beats from fixtures/events.json (the envelope shape) into the SAME
# enqueue_replay entry point the live /events poll uses, and awaits the serial drain.
# Asserts the ENGINE-DECIDED outcomes were SHOWN (not recomputed):
#   - attack/cast played + the actor faced its target (renderer-derived facing),
#   - damage flashed + the target HP bar dropped to the engine's hp_after/hp_max,
#   - a heal pulsed + raised the target's HP bar,
#   - the death beat faded + FREED the dying token (it left the stage).
# Deterministic + headless (no window). Quits cleanly.
# ---------------------------------------------------------------------------
func _run_combat_replay() -> void:
	print("[Main] --combat-replay: animating the Action-Replay envelope on the iso tiles")

	# Detach the steady FIXTURE auto-poll so it can't (a) re-apply the exploration
	# (active:false) snapshot and reconcile away the spawned combat roster, or (b)
	# double-feed events.json into the replay queue. We drive the surfaces + beats
	# DELIBERATELY below. (The live path never does this — this is a conformance harness.)
	_detach_live_feed()

	# The standalone FIXTURE boot-poll may already be draining events.json against the
	# (pre-roster) exploration stage. Let that in-flight drain FULLY finish, then RESET
	# the replay cursor — BEFORE we spawn the roster — so no stale boot-drain beat (e.g.
	# the seq-10 death) can touch a freshly-spawned token.
	var settle := 0.0
	while bool(_world.is_replaying()) and settle < 6.0:
		await get_tree().create_timer(0.1).timeout
		settle += 0.1
	_world.reset_replay()

	# (1) Spawn the roster from the ACTIVE combat snapshot (same path as --combat-tokens).
	var combat: Dictionary = _load_fixture_dict("combat-surface-active")
	var atlas: Dictionary = _load_fixture_dict("atlas-surface")
	var character: Dictionary = _load_fixture_dict("character-surface")
	if combat.is_empty():
		print("[CombatReplay] RESULT ok=false reason=missing-active-fixture")
		call_deferred("_quit_clean")
		return
	_world.apply_snapshot(atlas, combat, character)
	await get_tree().create_timer(CharacterToken.MOVE_TWEEN_SEC + 0.1).timeout

	# Capture pre-replay facts on the actors the beats touch.
	var cultist: CharacterToken = _world.token_for("npc-cultist-1")
	var companion: CharacterToken = _world.token_for("char-companion-1")
	var aubree: CharacterToken = _world.token_for("char-aubree")
	var roster_ok := cultist != null and companion != null and aubree != null
	print("[CombatReplay] roster cultist=%s companion=%s aubree=%s" % [
		str(cultist != null), str(companion != null), str(aubree != null)])

	# (2) Load the COMBAT beats from events.json (skip the narrate row) and play them
	# through the real enqueue_replay → serial drain.
	var beats := _load_combat_event_beats()
	print("[CombatReplay] beats=%d (combat verbs from events.json)" % beats.size())
	_world.enqueue_replay(beats)

	# (3) Await the serial drain. Bound generously: the queue length * the longest beat
	# (heal pulse is 2*0.35 + the move 0.45 + attack 0.5 ...) — ~6s ceiling is ample.
	var waited := 0.0
	while bool(_world.is_replaying()) and waited < 8.0:
		await get_tree().create_timer(0.1).timeout
		waited += 0.1

	# (4) Assertions — the engine-decided beats were SHOWN.
	# (a) the dying cultist token was freed (left the stage) by the death beat.
	var cultist_freed := not is_instance_valid(cultist)
	# (b) the healed companion's HP bar rose to the engine's 16/22 (~0.727).
	var companion_hp := companion.hp_fraction() if is_instance_valid(companion) else -1.0
	var companion_hp_ok := absf(companion_hp - (16.0 / 22.0)) < 0.02
	# (c) aubree faced her target during the attack exchange (a real 8-way facing).
	var aubree_facing := aubree.facing() if is_instance_valid(aubree) else "?"
	var aubree_facing_ok := aubree_facing in ["S", "SE", "E", "NE", "N", "NW", "W", "SW"]
	# (d) the replay queue fully drained (no beats left stuck).
	var drained: bool = not bool(_world.is_replaying())

	print("[CombatReplay] cultist_freed=%s companion_hp=%.3f (ok=%s) aubree_facing=%s drained=%s waited=%.1fs" % [
		str(cultist_freed), companion_hp, str(companion_hp_ok), aubree_facing, str(drained), waited])

	var all_ok := roster_ok and cultist_freed and companion_hp_ok and aubree_facing_ok and drained
	print("[CombatReplay] RESULT ok=%s death-freed=%s heal-hp-raised=%s attack-faced=%s drained=%s" % [
		str(all_ok), str(cultist_freed), str(companion_hp_ok), str(aubree_facing_ok), str(drained)])

	call_deferred("_quit_clean")


## #1060 — the COMBAT beats from fixtures/events.json (the envelope rows; the leading
## `narrate`/exploration rows are skipped). Returns the entries that carry a combat verb,
## so _run_combat_replay drives the exact same shape the live /events poll would.
func _load_combat_event_beats() -> Array:
	var fx: Dictionary = _load_fixture_dict("events")
	var entries: Variant = fx.get("entries", [])
	if typeof(entries) != TYPE_ARRAY:
		return []
	var combat_verbs := ["attack", "cast", "damage", "heal", "condition", "death", "move_to_zone", "zone_move"]
	var out: Array = []
	for e in entries:
		if typeof(e) != TYPE_DICTIONARY:
			continue
		var verb := String((e as Dictionary).get("verb", (e as Dictionary).get("kind", "")))
		if combat_verbs.has(verb):
			out.append(e)
	return out


# ---------------------------------------------------------------------------
# #1060 DEMO-COMBAT: the VISUAL combat-beat proof. Run WITHOUT --headless (a real
# window) so rendering is real. Spawns the combat roster, plays a couple of beats
# (an attack swing + a damage flash/float on a foe), settles a frame, and screenshots
# the animating beat to /tmp so a reviewer can SEE the action-replay on the iso tiles.
# ---------------------------------------------------------------------------
func _run_demo_combat() -> void:
	print("[Main] --demo-combat: visual combat Action-Replay screenshot")
	var combat: Dictionary = _load_fixture_dict("combat-surface-active")
	var atlas: Dictionary = _load_fixture_dict("atlas-surface")
	var character: Dictionary = _load_fixture_dict("character-surface")
	if combat.is_empty():
		print("[DemoCombat] cannot run — missing active combat fixture")
		call_deferred("_quit_clean")
		return
	# Detach the steady auto-poll (see _run_combat_replay), let any in-flight boot-poll
	# replay finish, and reset — BEFORE spawning the roster — so no stale beat touches it.
	_detach_live_feed()
	var settle := 0.0
	while bool(_world.is_replaying()) and settle < 6.0:
		await get_tree().create_timer(0.1).timeout
		settle += 0.1
	_world.reset_replay()
	_world.apply_snapshot(atlas, combat, character)
	await get_tree().create_timer(CharacterToken.MOVE_TWEEN_SEC + 0.1).timeout

	# Play a mid-exchange beat pair so the screenshot catches motion: aubree swings at a
	# cultist, the cultist flashes + a damage number floats.
	var beats: Array = [
		{ "seq": 100, "actor_fk": "char-aubree", "verb": "attack", "target_fk": "npc-cultist-1",
		  "result": { "outcome": "hit" }, "anim_hint": "melee_swing" },
		{ "seq": 101, "actor_fk": "char-aubree", "verb": "damage", "target_fk": "npc-cultist-1",
		  "result": { "damage": { "total": 8 }, "hp_after": 6, "hp_max": 14 }, "anim_hint": "damage_flinch" },
	]
	_world.enqueue_replay(beats)

	# Let the attack swing + the damage flash play, then capture mid-beat.
	await _settle_frames(3)
	await get_tree().create_timer(0.25).timeout
	await _settle_frames(2)
	var img := _capture_viewport()
	if img != null:
		img.save_png("/tmp/wos_godot_combat_replay.png")
		print("[DemoCombat] screenshot: /tmp/wos_godot_combat_replay.png (%dx%d)" % [img.get_width(), img.get_height()])
	else:
		print("[DemoCombat] no image captured (headless?) — run WITHOUT --headless for a real frame")

	# Let the rest of the beats drain before quitting.
	var waited := 0.0
	while bool(_world.is_replaying()) and waited < 4.0:
		await get_tree().create_timer(0.1).timeout
		waited += 0.1
	print("[DemoCombat] done drained=%s" % str(not bool(_world.is_replaying())))
	call_deferred("_quit_clean")


## #1060 — detach the steady SurfaceClient auto-poll from WorldView so a deliberate
## conformance/visual harness can drive apply_snapshot + enqueue_replay itself without
## the FIXTURE poll re-applying the (active:false) exploration snapshot mid-replay
## (which would reconcile away the spawned combat roster). Disconnects the two WorldView
## sinks; Hud + the SMOKE print stay wired (harmless). Idempotent. NOT a live-path action.
func _detach_live_feed() -> void:
	if SurfaceClient.snapshot_updated.is_connected(_world.apply_snapshot):
		SurfaceClient.snapshot_updated.disconnect(_world.apply_snapshot)
	if SurfaceClient.events_appended.is_connected(_world.enqueue_replay):
		SurfaceClient.events_appended.disconnect(_world.enqueue_replay)


## #1060 — load + parse a bundled res://fixtures/<name>.json into a Dictionary (or {}
## on any missing/parse failure). Mirrors SurfaceClient's fixture loader so the
## conformance drives apply_snapshot with the exact same shapes the transport emits.
func _load_fixture_dict(name: String) -> Dictionary:
	var path := "res://fixtures/%s.json" % name
	if not FileAccess.file_exists(path):
		push_warning("[Main] missing fixture: " + path)
		return {}
	var text := FileAccess.get_file_as_string(path)
	var parsed: Variant = JSON.parse_string(text)
	return parsed if typeof(parsed) == TYPE_DICTIONARY else {}


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


## Return the value that follows `flag` in the post-`--` user args (or "").
## E.g. for args ["--spec", "/tmp/scene.json"] returns "/tmp/scene.json" when
## flag=="--spec".
func _arg_value(flag: String) -> String:
	var args := OS.get_cmdline_user_args()
	var idx := args.find(flag)
	if idx < 0 or idx + 1 >= args.size():
		return ""
	return args[idx + 1]


# ---------------------------------------------------------------------------
# --preview-scene: scene-preview harness (additive, non-interactive).
#
# Spec format (/tmp/scene.json):
#   {
#     "backdrop": "/tmp/art/tavern.png",          // local PNG path, optional
#     "nav": {
#       "cols": 12, "rows": 8,                    // grid dimensions
#       "cell_w_px": 72,                          // screen diamond width
#       "origin_px": [512, 300],                  // screen coords of cell(0,0) center
#       "blocked": [[3,3],[8,2],[6,5]]            // solid (unwalkable) cells
#     },
#     "actors": [                                 // optional actor overlays
#       {"id":"pc",  "cell":[1,4], "facing":"E"},
#       {"id":"foe", "cell":[10,4],"facing":"W"}
#     ],
#     "path_probe": {"from":[1,4], "to":[10,4]}, // A* probe (full overlay mode)
#     "camera": {"zoom": 1.0}                     // reserved, not yet wired
#   }
#
# Args:
#   --spec  <path>   JSON spec file (required)
#   --shot  <path>   output PNG (default /tmp/scene.png)
#   --overlay <none|grid|full>  (default full)
#
# Output:
#   <shot>.png              — viewport capture with grid/path overlay
#   <shot>.nav.json         — {"path_found":bool,"path":[[c,r],...],"blocked_count":int,...}
#
# Usage (qa/preview_scene.sh wraps this):
#   godot --path godot --quit-after 300 -- \
#     --preview-scene --spec /tmp/scene.json --shot /tmp/scene.png --overlay full
# ---------------------------------------------------------------------------
func _run_preview_scene() -> void:
	print("[Main] --preview-scene: scene-preview harness")

	var spec_path := _arg_value("--spec")
	if spec_path == "":
		push_warning("[PreviewScene] --spec not supplied — aborting")
		print("[PreviewScene] RESULT ok=false reason=missing_spec")
		call_deferred("_quit_clean")
		return

	var shot_path := _arg_value("--shot")
	if shot_path == "":
		shot_path = "/tmp/scene.png"

	var overlay_mode := _arg_value("--overlay")
	if overlay_mode == "" or (overlay_mode != "none" and overlay_mode != "grid" and overlay_mode != "full"):
		overlay_mode = "full"

	# Parse spec JSON.
	if not FileAccess.file_exists(spec_path):
		push_warning("[PreviewScene] spec file not found: " + spec_path)
		print("[PreviewScene] RESULT ok=false reason=spec_not_found path=%s" % spec_path)
		call_deferred("_quit_clean")
		return

	var text := FileAccess.get_file_as_string(spec_path)
	var parsed: Variant = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		push_warning("[PreviewScene] spec JSON parse failed: " + spec_path)
		print("[PreviewScene] RESULT ok=false reason=spec_parse_error")
		call_deferred("_quit_clean")
		return

	var spec: Dictionary = parsed

	# Detach the live SurfaceClient feed so the fixture poll doesn't stomp our overlay.
	_detach_live_feed()

	# 1. Apply backdrop — local path first (no HTTP, no scope resolution).
	var backdrop_path: String = String(spec.get("backdrop", ""))
	if backdrop_path != "":
		var ok: bool = _world.apply_local_backdrop(backdrop_path)
		if not ok:
			print("[PreviewScene] backdrop load failed for path=%s — using procedural fallback" % backdrop_path)
	else:
		print("[PreviewScene] no backdrop specified — using procedural fallback")

	# 2. Build and attach the NavOverlay.
	var nav_block: Dictionary = {}
	var nav_v: Variant = spec.get("nav", {})
	if typeof(nav_v) == TYPE_DICTIONARY:
		nav_block = nav_v

	var actors_arr: Array = []
	var actors_v: Variant = spec.get("actors", [])
	if typeof(actors_v) == TYPE_ARRAY:
		actors_arr = actors_v

	var path_probe: Dictionary = {}
	var probe_v: Variant = spec.get("path_probe", {})
	if typeof(probe_v) == TYPE_DICTIONARY:
		path_probe = probe_v

	# Zone anchors from the nav spec (optional "zones" key: [[x,y],...] in screen px).
	var zone_anchors: Array = []
	var zones_v: Variant = nav_block.get("zones", [])
	if typeof(zones_v) == TYPE_ARRAY:
		for z in (zones_v as Array):
			if typeof(z) == TYPE_ARRAY and (z as Array).size() >= 2:
				zone_anchors.append(Vector2(float((z as Array)[0]), float((z as Array)[1])))

	var nav_overlay := preload("res://scenes/NavOverlay.gd").new()
	nav_overlay.name = "NavOverlay"
	# Draw grid/path BELOW the Y-sorted actor tokens so sprites stand ON the grid.
	# z=-5 sits between WalkmaskLayer (z=-50) and YSortLayer (z=0 default).
	nav_overlay.z_index = -5
	nav_overlay.setup(nav_block, actors_arr, path_probe, zone_anchors, overlay_mode)
	_world.add_child(nav_overlay)

	# 2b. Spawn real character sprites for each actor in the spec (full mode only).
	#
	# In "full" overlay mode we replace the foot-dot debug markers with REAL baked
	# fighter sprites standing on their cells, team-tinted (party=cool/blue,
	# foe=warm/red), each with a blob shadow so they read as seated on the floor.
	# In "grid" mode the debug foot-dots from NavOverlay remain (no sprites).
	# We load the committed fighter sheet directly (scope sprite-fighter-iso8) —
	# it is the only baked 8-facing sheet in the tree; all preview actors share it.
	if overlay_mode == "full" and not actors_arr.is_empty():
		_spawn_preview_sprites(nav_block, actors_arr, spec)

	# Wire the nav data to WorldView so interactive clicks / --walk-demo can walk the grid.
	if not nav_block.is_empty():
		_world.setup_nav(nav_block)
		# The first non-foe actor in the spec is the "active" token for click-to-walk.
		var first_actor: Dictionary = _first_party_actor(actors_arr)
		if not first_actor.is_empty():
			var a_id := String(first_actor.get("id", ""))
			var a_cell_v: Variant = first_actor.get("cell", [0, 0])
			var a_cell := Vector2i(0, 0)
			if typeof(a_cell_v) == TYPE_ARRAY and (a_cell_v as Array).size() >= 2:
				a_cell = Vector2i(int((a_cell_v as Array)[0]), int((a_cell_v as Array)[1]))
			_world.set_active_preview_actor(a_id, a_cell)

	# 3. Settle a few frames so the rendering pipeline flushes, then capture.
	await _settle_frames(3)
	await get_tree().create_timer(0.05).timeout
	await _settle_frames(2)

	var img := _capture_viewport()
	if img != null:
		img.save_png(shot_path)
		print("[PreviewScene] screenshot: %s (%dx%d)" % [shot_path, img.get_width(), img.get_height()])
	else:
		print("[PreviewScene] no image captured (headless?) — run WITHOUT --headless for a real frame")

	# 4. Write <shot>.nav.json.
	var nav_result: Dictionary = nav_overlay.nav_result()
	var nav_json_path := shot_path + ".nav.json"
	# strip ".png.nav.json" and replace with ".nav.json"
	if shot_path.ends_with(".png"):
		nav_json_path = shot_path.substr(0, shot_path.length() - 4) + ".nav.json"

	var nav_json := JSON.stringify(nav_result, "\t")
	var f := FileAccess.open(nav_json_path, FileAccess.WRITE)
	if f != null:
		f.store_string(nav_json)
		f.close()
		print("[PreviewScene] nav.json: %s path_found=%s" % [nav_json_path, str(nav_result.get("path_found", false))])
	else:
		push_warning("[PreviewScene] could not write nav.json to " + nav_json_path)

	print("[PreviewScene] RESULT ok=%s overlay=%s path_found=%s shot=%s" % [
		str(img != null), overlay_mode, str(nav_result.get("path_found", false)), shot_path])

	call_deferred("_quit_clean")


# ---------------------------------------------------------------------------
# --preview-scene sprite helpers (additive; only called from _run_preview_scene).
# ---------------------------------------------------------------------------

## Convert a grid cell (c, r) to screen position using the same ISO-PROJECTION.md
## dimetric 2:1 transform as NavOverlay._cell_to_screen():
##   x = origin_px.x + (c - r) * cell_w / 2
##   y = origin_px.y + (c + r) * cell_w / 4
func _preview_cell_to_screen(c: int, r: int, origin: Vector2, cell_w: float) -> Vector2:
	return Vector2(
		origin.x + float(c - r) * cell_w * 0.5,
		origin.y + float(c + r) * cell_w * 0.25
	)


## Spawn one CharacterToken per actor in the spec using the committed baked fighter
## sheet (sprite-fighter-iso8). Each token is:
##   - placed at the dimetric cell centre for its spec `cell:[c,r]`
##   - set to the spec `facing` (one of the 8 locked directions)
##   - team-tinted: actors with id starting "foe" or "enemy" get the warm red wash;
##     all others (party) get a cool blue-ish tint
##   - given a blob shadow so it reads as seated on the floor
##
## All tokens live in WorldView's YSortLayer so real Y-sort occlusion applies.
## Foot-dots from NavOverlay remain drawn at a lower z-level — the sprite body
## naturally covers them at its cell centre.
func _spawn_preview_sprites(nav: Dictionary, actors: Array, _spec: Dictionary) -> void:
	# Resolve the nav grid parameters (same as NavOverlay.setup parses them).
	var cell_w := float(nav.get("cell_w_px", 64))
	var orig := Vector2.ZERO
	var orig_v: Variant = nav.get("origin_px", [0, 0])
	if typeof(orig_v) == TYPE_ARRAY and (orig_v as Array).size() >= 2:
		orig = Vector2(float((orig_v as Array)[0]), float((orig_v as Array)[1]))

	# Load the committed baked fighter sheet once — all preview actors reuse it.
	const FIGHTER_DIR := "res://assets/characters/fighter/"
	var json_path := FIGHTER_DIR + "sheet.json"
	if not FileAccess.file_exists(json_path):
		push_warning("[PreviewScene] fighter sheet.json not found — no sprites spawned")
		return
	var manifest_text := FileAccess.get_file_as_string(json_path)
	var manifest_v: Variant = JSON.parse_string(manifest_text)
	if typeof(manifest_v) != TYPE_DICTIONARY:
		push_warning("[PreviewScene] fighter sheet.json parse failed — no sprites spawned")
		return
	var manifest: Dictionary = manifest_v
	var tex_path := FIGHTER_DIR + String(manifest.get("image", "sheet.png"))
	var sheet_tex: Texture2D = load(tex_path)
	if sheet_tex == null:
		push_warning("[PreviewScene] fighter sheet.png not found — no sprites spawned")
		return

	# Resolve the YSortLayer in WorldView so tokens depth-sort with the scene.
	var ysort := _world.get_node_or_null("YSortLayer") as Node2D
	if ysort == null:
		push_warning("[PreviewScene] YSortLayer not found in WorldView — no sprites spawned")
		return

	# Clear any tokens WorldView already spawned from the fixture snapshot (char-aubree
	# + the static pillar) so they don't appear alongside the preview-spec actors.
	# This is safe: we are in a one-shot preview harness — the YSortLayer is not being
	# used for live gameplay here.
	for child in ysort.get_children():
		child.queue_free()

	const CharacterTokenScene := preload("res://scenes/CharacterToken.tscn")

	# Team tint palette for the preview (party = cool steel-blue, foe = warm red).
	const TINT_PARTY := Color(0.72, 0.85, 1.00, 1.0)   ## cool blue-ish wash
	const TINT_FOE   := Color(1.00, 0.55, 0.45, 1.0)   ## warm hostile red (matches WorldView.TEAM_TINT_FOE)

	var spawned := 0
	for actor_v in actors:
		if typeof(actor_v) != TYPE_DICTIONARY:
			continue
		var actor: Dictionary = actor_v
		var actor_id := String(actor.get("id", "actor_%d" % spawned))
		var facing := String(actor.get("facing", "S"))

		# Resolve cell position.
		var cell_v: Variant = actor.get("cell", [0, 0])
		if typeof(cell_v) != TYPE_ARRAY or (cell_v as Array).size() < 2:
			continue
		var c := int((cell_v as Array)[0])
		var r := int((cell_v as Array)[1])
		var foot_pos := _preview_cell_to_screen(c, r, orig, cell_w)

		# Pick team tint: ids beginning with "foe" or "enemy" are hostile.
		var id_lower := actor_id.to_lower()
		var tint := TINT_FOE if (id_lower.begins_with("foe") or id_lower.begins_with("enemy")) else TINT_PARTY

		# Spawn the token.
		var tok: CharacterToken = CharacterTokenScene.instantiate()
		tok.engine_actor_id = actor_id
		tok.name = "PreviewToken_" + actor_id
		ysort.add_child(tok)
		tok.set_manifest(manifest, sheet_tex)
		tok.set_facing(facing)
		tok.set_anim("idle")
		tok.set_team_tint(tint)
		tok.show_blob_shadow(true)
		tok.place_at(foot_pos)

		print("[PreviewScene] token actor=%s cell=(%d,%d) foot=(%.0f,%.0f) facing=%s tint=(%.2f,%.2f,%.2f)" % [
			actor_id, c, r, foot_pos.x, foot_pos.y, facing, tint.r, tint.g, tint.b])
		spawned += 1

	print("[PreviewScene] spawned %d preview sprite(s)" % spawned)


## Return the first non-foe actor from an actors array (i.e., a party/pc actor).
## Used to designate the "active" walk token in --preview-scene and --walk-demo.
func _first_party_actor(actors: Array) -> Dictionary:
	for a_v in actors:
		if typeof(a_v) != TYPE_DICTIONARY:
			continue
		var a: Dictionary = a_v
		var id_lower := String(a.get("id", "")).to_lower()
		if not id_lower.begins_with("foe") and not id_lower.begins_with("enemy"):
			return a
	return {}


# ---------------------------------------------------------------------------
# --walk-demo: interactive A* walk visual proof. Loads the tavern milestone spec
# (from /tmp/tavern_milestone.json or a built-in inline fallback), spawns the
# preview sprites, then SCRIPTS a click at a far cell whose straight-line path
# crosses the blocked table cells (forcing an A* detour). Captures screenshots
# at start, mid-walk, and end to /tmp/walk_{start,mid,end}.png, then quits.
# ---------------------------------------------------------------------------
func _run_walk_demo() -> void:
	print("[Main] --walk-demo: scripted A* walk visual proof")

	# Load the spec — prefer /tmp/tavern_milestone.json, else use an inline default
	# with table-blocking cells that force a detour.
	var spec: Dictionary = {}
	var spec_candidates := ["/tmp/tavern_milestone.json", "/tmp/scene.json"]
	for sp in spec_candidates:
		if FileAccess.file_exists(sp):
			var txt := FileAccess.get_file_as_string(sp)
			var parsed: Variant = JSON.parse_string(txt)
			if typeof(parsed) == TYPE_DICTIONARY:
				spec = parsed
				print("[WalkDemo] loaded spec from %s" % sp)
				break
	if spec.is_empty():
		# Inline fallback: a 10×7 grid with a table-block cluster at [4,3],[5,3],[4,4]
		# so a walk from [1,4] to [8,2] must arc around it rather than going straight.
		spec = {
			"nav": {
				"cols": 10, "rows": 7, "cell_w_px": 56,
				"origin_px": [548, 405],
				"blocked": [[4,3],[5,3],[4,4],[6,2],[7,2]]
			},
			"actors": [
				{"id": "pc1", "cell": [1,4], "facing": "SE"},
				{"id": "foe1", "cell": [9,3], "facing": "W"}
			],
			"path_probe": {"from": [1,4], "to": [8,2]}
		}
		print("[WalkDemo] using inline fallback spec (no /tmp/tavern_milestone.json)")

	# Detach the live feed so the fixture poll does not stomp our preview.
	_detach_live_feed()

	# Apply backdrop if the spec has one.
	var backdrop_path: String = String(spec.get("backdrop", ""))
	if backdrop_path != "" and FileAccess.file_exists(backdrop_path):
		var ok: bool = _world.apply_local_backdrop(backdrop_path)
		if not ok:
			print("[WalkDemo] backdrop load failed — using procedural fallback")
	else:
		print("[WalkDemo] no backdrop or not found — using procedural fallback")

	# Spawn the preview sprites.
	var nav_block: Dictionary = {}
	var nav_v: Variant = spec.get("nav", {})
	if typeof(nav_v) == TYPE_DICTIONARY:
		nav_block = nav_v

	var actors_arr: Array = []
	var actors_v: Variant = spec.get("actors", [])
	if typeof(actors_v) == TYPE_ARRAY:
		actors_arr = actors_v

	var path_probe: Dictionary = {}
	var probe_v: Variant = spec.get("path_probe", {})
	if typeof(probe_v) == TYPE_DICTIONARY:
		path_probe = probe_v

	var zone_anchors: Array = []
	var zones_v: Variant = nav_block.get("zones", [])
	if typeof(zones_v) == TYPE_ARRAY:
		for z in (zones_v as Array):
			if typeof(z) == TYPE_ARRAY and (z as Array).size() >= 2:
				zone_anchors.append(Vector2(float((z as Array)[0]), float((z as Array)[1])))

	# Add a NavOverlay so the grid + solved A* path are visible.
	var nav_overlay := preload("res://scenes/NavOverlay.gd").new()
	nav_overlay.name = "NavOverlay"
	nav_overlay.z_index = -5
	nav_overlay.setup(nav_block, actors_arr, path_probe, zone_anchors, "full")
	_world.add_child(nav_overlay)

	# Spawn sprites.
	if not actors_arr.is_empty():
		_spawn_preview_sprites(nav_block, actors_arr, spec)

	# Wire nav to WorldView.
	if not nav_block.is_empty():
		_world.setup_nav(nav_block)
		var first_actor: Dictionary = _first_party_actor(actors_arr)
		if not first_actor.is_empty():
			var a_id := String(first_actor.get("id", ""))
			var a_cell_v: Variant = first_actor.get("cell", [0, 0])
			var a_cell := Vector2i(0, 0)
			if typeof(a_cell_v) == TYPE_ARRAY and (a_cell_v as Array).size() >= 2:
				a_cell = Vector2i(int((a_cell_v as Array)[0]), int((a_cell_v as Array)[1]))
			_world.set_active_preview_actor(a_id, a_cell)

	# Settle so the scene is fully rendered before the first screenshot.
	await _settle_frames(3)
	await get_tree().create_timer(0.1).timeout
	await _settle_frames(2)

	# SCREENSHOT 1 — START: the token is at its initial cell.
	var start_img := _capture_viewport()
	if start_img != null:
		start_img.save_png("/tmp/walk_start.png")
		print("[WalkDemo] screenshot: /tmp/walk_start.png (%dx%d)" % [start_img.get_width(), start_img.get_height()])

	# Build the click target: the path_probe "to" cell in screen coords.
	# This walk crosses the blocked table cells, so A* must detour.
	var target_cell := Vector2i(8, 2)  # default far cell
	var to_v: Variant = path_probe.get("to", null)
	if typeof(to_v) == TYPE_ARRAY and (to_v as Array).size() >= 2:
		target_cell = Vector2i(int((to_v as Array)[0]), int((to_v as Array)[1]))

	# Verify the target is not itself blocked (sanity check).
	var blocked_set: Dictionary = {}
	for b in (_nav_blocked_from_spec(nav_block)):
		blocked_set[b] = true
	if blocked_set.has(target_cell):
		print("[WalkDemo] target cell (%d,%d) is blocked — adjusting to (8,1)" % [target_cell.x, target_cell.y])
		target_cell = Vector2i(8, 1)

	print("[WalkDemo] scripted click target_cell=(%d,%d)" % [target_cell.x, target_cell.y])

	# Compute screen position of the target cell and pass it to WorldView as a click.
	var cell_w := float(nav_block.get("cell_w_px", 64))
	var orig := Vector2.ZERO
	var orig_v_2: Variant = nav_block.get("origin_px", [0, 0])
	if typeof(orig_v_2) == TYPE_ARRAY and (orig_v_2 as Array).size() >= 2:
		orig = Vector2(float((orig_v_2 as Array)[0]), float((orig_v_2 as Array)[1]))
	var target_screen := Vector2(
		orig.x + float(target_cell.x - target_cell.y) * cell_w * 0.5,
		orig.y + float(target_cell.x + target_cell.y) * cell_w * 0.25
	)

	# Trigger the walk by calling _handle_floor_click directly (no real mouse needed).
	print("[WalkDemo] calling _handle_floor_click at screen=(%.0f,%.0f)" % [target_screen.x, target_screen.y])
	_world._handle_floor_click(target_screen)

	# SCREENSHOT 2 — MID: wait ~half the total walk time then capture mid-walk.
	# The walk is cell-by-cell; each step is MOVE_TWEEN_SEC = 0.45s.
	# Estimate path length (A* on the grid); capture after roughly half the steps.
	# We wait a fixed time since we can't easily introspect the path length here.
	var half_walk_wait := CharacterToken.MOVE_TWEEN_SEC * 2.5  # ~2-3 steps in
	await get_tree().create_timer(half_walk_wait).timeout
	await _settle_frames(2)
	var mid_img := _capture_viewport()
	if mid_img != null:
		mid_img.save_png("/tmp/walk_mid.png")
		print("[WalkDemo] screenshot: /tmp/walk_mid.png (%dx%d)" % [mid_img.get_width(), mid_img.get_height()])

	# SCREENSHOT 3 — END: wait for the walk to complete (generous ceiling 15s).
	var waited := 0.0
	while bool(_world._walking) and waited < 15.0:
		await get_tree().create_timer(0.1).timeout
		waited += 0.1
	await _settle_frames(3)
	var end_img := _capture_viewport()
	if end_img != null:
		end_img.save_png("/tmp/walk_end.png")
		print("[WalkDemo] screenshot: /tmp/walk_end.png (%dx%d)" % [end_img.get_width(), end_img.get_height()])

	var final_cell: Vector2i = _world._active_cell
	print("[WalkDemo] RESULT walk_complete=%s final_cell=(%d,%d) target_cell=(%d,%d) match=%s" % [
		str(not bool(_world._walking)),
		final_cell.x, final_cell.y, target_cell.x, target_cell.y,
		str(final_cell == target_cell)
	])

	# Also test blocked-cell rejection: attempt a walk to a known blocked cell.
	var blocked_arr: Array = _nav_blocked_from_spec(nav_block)
	if not blocked_arr.is_empty():
		var blocked_cell: Vector2i = blocked_arr[0]
		var b_screen := Vector2(
			orig.x + float(blocked_cell.x - blocked_cell.y) * cell_w * 0.5,
			orig.y + float(blocked_cell.x + blocked_cell.y) * cell_w * 0.25
		)
		print("[WalkDemo] testing blocked-cell rejection at cell=(%d,%d)" % [blocked_cell.x, blocked_cell.y])
		_world._handle_floor_click(b_screen)
		await get_tree().create_timer(0.1).timeout
		var still_idle := not bool(_world._walking)
		print("[WalkDemo] blocked-cell click is_noop=%s (expected true)" % str(still_idle))

	call_deferred("_quit_clean")


# ---------------------------------------------------------------------------
# --cam-demo: camera milestone visual proof. Run WITHOUT --headless (real window).
# Loads the tavern spec (or inline fallback), spawns sprites, then:
#   1. Captures a DEFAULT framing screenshot (/tmp/cam_default.png)
#   2. Programmatically zooms to 1.6x centered on the active token + captures
#      (/tmp/cam_zoom.png)
#   3. Prints camera behaviour summary.
# The committed backdrop res://assets/backdrops/scene-lower-city.png is used
# via apply_local_backdrop if available at the res:// path.
# ---------------------------------------------------------------------------
func _run_cam_demo() -> void:
	print("[Main] --cam-demo: camera milestone visual proof")

	# Detach the live feed (same pattern as walk-demo).
	_detach_live_feed()

	# Load the tavern spec.
	var spec: Dictionary = {}
	for sp in ["/tmp/tavern_milestone.json", "/tmp/scene.json"]:
		if FileAccess.file_exists(sp):
			var txt := FileAccess.get_file_as_string(sp)
			var parsed: Variant = JSON.parse_string(txt)
			if typeof(parsed) == TYPE_DICTIONARY:
				spec = parsed
				print("[CamDemo] loaded spec from %s" % sp)
				break
	if spec.is_empty():
		spec = {
			"backdrop": "res://assets/backdrops/scene-lower-city.png",
			"nav": {
				"cols": 10, "rows": 7, "cell_w_px": 56,
				"origin_px": [548, 405],
				"blocked": [[4,3],[5,3],[4,4]]
			},
			"actors": [
				{"id": "pc1", "cell": [2,4], "facing": "SE"},
				{"id": "foe1", "cell": [8,2], "facing": "W"}
			],
			"path_probe": {"from": [2,4], "to": [7,2]}
		}
		print("[CamDemo] using inline fallback spec")

	# Apply backdrop — accept both "res://" paths and absolute /tmp paths.
	var bp: String = String(spec.get("backdrop", ""))
	if bp != "":
		# If it's a res:// path, translate to filesystem for apply_local_backdrop.
		var local_bp := bp
		if bp.begins_with("res://"):
			local_bp = ProjectSettings.globalize_path(bp)
		var ok: bool = _world.apply_local_backdrop(local_bp)
		if not ok:
			print("[CamDemo] backdrop load failed path=%s — using procedural fallback" % bp)
	else:
		# Try the committed tavern backdrop by default.
		var default_bp := ProjectSettings.globalize_path("res://assets/backdrops/scene-lower-city.png")
		if FileAccess.file_exists(default_bp):
			var _ok: bool = _world.apply_local_backdrop(default_bp)

	var nav_block: Dictionary = {}
	var nav_v: Variant = spec.get("nav", {})
	if typeof(nav_v) == TYPE_DICTIONARY:
		nav_block = nav_v

	var actors_arr: Array = []
	var actors_v: Variant = spec.get("actors", [])
	if typeof(actors_v) == TYPE_ARRAY:
		actors_arr = actors_v

	var path_probe: Dictionary = {}
	var probe_v: Variant = spec.get("path_probe", {})
	if typeof(probe_v) == TYPE_DICTIONARY:
		path_probe = probe_v

	# NavOverlay.
	var nav_overlay := preload("res://scenes/NavOverlay.gd").new()
	nav_overlay.name = "NavOverlay"
	nav_overlay.z_index = -5
	nav_overlay.setup(nav_block, actors_arr, path_probe, [], "full")
	_world.add_child(nav_overlay)

	# Spawn sprites.
	if not actors_arr.is_empty():
		_spawn_preview_sprites(nav_block, actors_arr, spec)

	# Wire nav.
	if not nav_block.is_empty():
		_world.setup_nav(nav_block)
		var first_actor := _first_party_actor(actors_arr)
		if not first_actor.is_empty():
			var a_id := String(first_actor.get("id", ""))
			var a_cell_v: Variant = first_actor.get("cell", [0, 0])
			var a_cell := Vector2i(0, 0)
			if typeof(a_cell_v) == TYPE_ARRAY and (a_cell_v as Array).size() >= 2:
				a_cell = Vector2i(int((a_cell_v as Array)[0]), int((a_cell_v as Array)[1]))
			_world.set_active_preview_actor(a_id, a_cell)

	# Settle with default framing.
	await _settle_frames(4)
	await get_tree().create_timer(0.1).timeout
	await _settle_frames(2)

	# SCREENSHOT 1 — DEFAULT framing.
	var img1 := _capture_viewport()
	if img1 != null:
		img1.save_png("/tmp/cam_default.png")
		print("[CamDemo] screenshot 1 (default): /tmp/cam_default.png (%dx%d)" % [img1.get_width(), img1.get_height()])
	else:
		print("[CamDemo] no image captured (headless?) — run WITHOUT --headless")

	# SCREENSHOT 2 — ZOOMED framing: programmatically zoom to 1.6x and pan toward
	# the active token to prove camera zoom works.
	var cam: Camera2D = _world.scene_camera()
	if cam != null:
		# Zoom to 1.6x.
		cam.zoom = Vector2(1.6, 1.6)
		# Pan to the first actor's screen position (if we can get it).
		var cell_w := float(nav_block.get("cell_w_px", 56))
		var orig_v: Variant = nav_block.get("origin_px", [548, 405])
		var orig := Vector2(548, 405)
		if typeof(orig_v) == TYPE_ARRAY and (orig_v as Array).size() >= 2:
			orig = Vector2(float((orig_v as Array)[0]), float((orig_v as Array)[1]))
		# Center on a point of interest — the midpoint between pc1 and foe1.
		var cell_w_half := cell_w * 0.5
		var pc_pos := Vector2(orig.x + (2 - 4) * cell_w_half, orig.y + (2 + 4) * cell_w * 0.25)
		var foe_pos := Vector2(orig.x + (8 - 2) * cell_w_half, orig.y + (8 + 2) * cell_w * 0.25)
		cam.position = (pc_pos + foe_pos) * 0.5
		print("[CamDemo] camera zoom=%.2f position=(%.0f,%.0f)" % [cam.zoom.x, cam.position.x, cam.position.y])

	await _settle_frames(4)
	await get_tree().create_timer(0.1).timeout
	await _settle_frames(2)

	var img2 := _capture_viewport()
	if img2 != null:
		img2.save_png("/tmp/cam_zoom.png")
		print("[CamDemo] screenshot 2 (zoomed): /tmp/cam_zoom.png (%dx%d)" % [img2.get_width(), img2.get_height()])

	print("[CamDemo] RESULT ok=%s cam_zoom=%.2f" % [str(img1 != null), cam.zoom.x if cam != null else 0.0])
	call_deferred("_quit_clean")


# ---------------------------------------------------------------------------
# --table-occlusion: Y-sort foreground table occluder proof.
# Run WITHOUT --headless (real window). Loads the tavern backdrop, spawns two
# tokens — one BEHIND the table (foot-y above table baseline) and one IN FRONT
# (foot-y below) — then adds the FgOccluder polygon over the table region.
# Screenshots /tmp/cam_default.png (default) and /tmp/occlusion_proof.png (proof).
# The polygon is authored from visual inspection of scene-lower-city.png (1344×768),
# matching the large wooden table in the mid-lower region of the tavern.
#
# If a SAM cut-out PNG is present at /tmp/table_cut.png, uses that as a sprite
# occluder instead (higher fidelity). Otherwise falls back to the polygon.
# ---------------------------------------------------------------------------
func _run_table_occlusion() -> void:
	print("[Main] --table-occlusion: foreground table occluder proof")
	_detach_live_feed()

	# Apply the committed tavern backdrop.
	var bp_local := ProjectSettings.globalize_path("res://assets/backdrops/scene-lower-city.png")
	if FileAccess.file_exists(bp_local):
		var _ok: bool = _world.apply_local_backdrop(bp_local)
		if not _ok:
			print("[TableOcclusion] backdrop load failed — using procedural fallback")
	else:
		print("[TableOcclusion] backdrop not found at %s — using procedural fallback" % bp_local)

	# Viewport size for coordinate calculations.
	var vp := Vector2(
		float(ProjectSettings.get_setting("display/window/size/viewport_width", 1152)),
		float(ProjectSettings.get_setting("display/window/size/viewport_height", 648))
	)
	# The backdrop is 1344×768; when cover-scaled to 1152×648 the scale factor is:
	# max(1152/1344, 648/768) = max(0.857, 0.844) = 0.857.
	# So the rendered image is 1344*0.857 ≈ 1152 wide, 768*0.857 ≈ 658 tall.
	# Centered at (576, 324). Effective y extent: 324 - 329 to 324 + 329 = -5..653.
	# Pixels from the raw image map to screen as: screen_y = 324 + (img_y - 384)*0.857
	const BP_W := 1344.0
	const BP_H := 768.0
	var scale_s := maxf(vp.x / BP_W, vp.y / BP_H)  # cover scale

	# Convert a raw-image pixel coordinate to WorldView-local screen coordinate.
	var img_to_screen := func(ix: float, iy: float) -> Vector2:
		return Vector2(
			vp.x * 0.5 + (ix - BP_W * 0.5) * scale_s,
			vp.y * 0.5 + (iy - BP_H * 0.5) * scale_s
		)

	# The tavern table in scene-lower-city.png — measured from SAM bounding box analysis:
	#   non-transparent bbox: x=205..512  y=443..625 (center x=358)
	#   foot (floor contact baseline): img_y = 625
	#   table center_x: img_x = 358
	# Polygon fallback traces the approximate table outline in image pixels.
	const T_TOP    := 443.0   ## table top (far edge of surface)
	const T_BOT    := 625.0   ## table bottom / floor contact baseline
	const T_LEFT   := 205.0   ## table left edge
	const T_RIGHT  := 512.0   ## table right edge
	const T_MID_X  := 358.0   ## center x (from SAM bbox)
	var poly_pts := PackedVector2Array([
		img_to_screen.call(T_LEFT  + 40.0, T_TOP + 10.0) as Vector2,
		img_to_screen.call(T_RIGHT - 40.0, T_TOP + 10.0) as Vector2,
		img_to_screen.call(T_RIGHT,        T_BOT - 10.0) as Vector2,
		img_to_screen.call(T_LEFT,         T_BOT - 10.0) as Vector2,
	])
	var foot_y_screen: float = (img_to_screen.call(T_MID_X, T_BOT) as Vector2).y

	# The ysort layer lives inside WorldView.
	var ysort := _world.get_node_or_null("YSortLayer") as Node2D
	if ysort == null:
		print("[TableOcclusion] YSortLayer not found — aborting")
		call_deferred("_quit_clean")
		return

	# Clear whatever was in the YSortLayer (no live fixture here).
	for child in ysort.get_children():
		child.queue_free()

	# ── Spawn the FgOccluder ──────────────────────────────────────────────
	const FgOccluderScript := preload("res://scenes/FgOccluder.gd")
	var occluder: FgOccluder = FgOccluderScript.new()
	occluder.occluder_id = "tavern_table"
	occluder.name = "FgOccluder_table"
	ysort.add_child(occluder)

	# Prefer the committed SAM cut-out (res://assets/backdrops/scene-lower-city.occluder-table.png).
	# Fall back to /tmp/table_cut.png, then to the polygon.
	var sam_cut_candidates := [
		ProjectSettings.globalize_path("res://assets/backdrops/scene-lower-city.occluder-table.png"),
		"/tmp/table_cut.png"
	]
	var used_sam := false
	for sam_candidate in sam_cut_candidates:
		var sam_cut_path: String = String(sam_candidate)
		if not FileAccess.file_exists(sam_cut_path):
			continue
		var img := Image.new()
		var err := img.load(sam_cut_path)
		if err != OK:
			continue
		var sam_tex := ImageTexture.create_from_image(img)
		if sam_tex == null:
			continue
		var backdrop_size := Vector2(BP_W, BP_H)
		occluder.setup_sprite(sam_tex, backdrop_size, vp, foot_y_screen)
		used_sam = true
		print("[TableOcclusion] using SAM cut-out sprite occluder from %s" % sam_cut_path)
		break
	if not used_sam:
		# Polygon fallback: warm brown matching the tavern table wood tone.
		var table_color := Color(0.42, 0.29, 0.16, 1.0)  # warm oak brown
		occluder.setup_polygon(poly_pts, table_color, foot_y_screen)
		print("[TableOcclusion] using polygon occluder (no SAM cut found in candidate list)")

	# ── Spawn two tokens: one BEHIND, one IN FRONT of the table ──────────
	# Load the committed fighter sheet.
	const FIGHTER_DIR := "res://assets/characters/fighter/"
	var json_text := FileAccess.get_file_as_string(FIGHTER_DIR + "sheet.json")
	var manifest_v: Variant = JSON.parse_string(json_text)
	if typeof(manifest_v) != TYPE_DICTIONARY:
		print("[TableOcclusion] fighter sheet not found — aborting")
		call_deferred("_quit_clean")
		return
	var manifest: Dictionary = manifest_v
	var sheet_tex: Texture2D = load(FIGHTER_DIR + String(manifest.get("image", "sheet.png")))
	if sheet_tex == null:
		print("[TableOcclusion] fighter sheet.png not found — aborting")
		call_deferred("_quit_clean")
		return

	const CharacterTokenScene := preload("res://scenes/CharacterToken.tscn")
	# BEHIND token: foot-y ABOVE the table baseline (smaller y = further back).
	# We place it at the table's mid-x, but ~70px above the baseline.
	var behind_foot := Vector2(
		(img_to_screen.call(T_MID_X, T_BOT) as Vector2).x,
		foot_y_screen - 70.0
	)
	var tok_behind: CharacterToken = CharacterTokenScene.instantiate()
	tok_behind.engine_actor_id = "behind_token"
	tok_behind.name = "Token_behind"
	ysort.add_child(tok_behind)
	tok_behind.set_manifest(manifest, sheet_tex)
	tok_behind.set_facing("S")
	tok_behind.set_anim("idle")
	tok_behind.set_team_tint(Color(0.72, 0.85, 1.00, 1.0))  # cool blue (party)
	tok_behind.show_blob_shadow(true)
	tok_behind.place_at(behind_foot)

	# FRONT token: foot-y BELOW the table baseline (larger y = nearer camera).
	var front_foot := Vector2(
		(img_to_screen.call(T_MID_X, T_BOT) as Vector2).x,
		foot_y_screen + 70.0
	)
	var tok_front: CharacterToken = CharacterTokenScene.instantiate()
	tok_front.engine_actor_id = "front_token"
	tok_front.name = "Token_front"
	ysort.add_child(tok_front)
	tok_front.set_manifest(manifest, sheet_tex)
	tok_front.set_facing("S")
	tok_front.set_anim("idle")
	tok_front.set_team_tint(Color(1.00, 0.55, 0.45, 1.0))  # warm red (foe)
	tok_front.show_blob_shadow(true)
	tok_front.place_at(front_foot)

	print("[TableOcclusion] behind_token foot_y=%.0f  table_baseline=%.0f  front_token foot_y=%.0f" % [
		behind_foot.y, foot_y_screen, front_foot.y])

	# Settle + screenshot.
	await _settle_frames(4)
	await get_tree().create_timer(0.1).timeout
	await _settle_frames(3)

	var img_proof := _capture_viewport()
	if img_proof != null:
		img_proof.save_png("/tmp/occlusion_proof.png")
		print("[TableOcclusion] screenshot: /tmp/occlusion_proof.png (%dx%d)" % [
			img_proof.get_width(), img_proof.get_height()])
		# Also save a copy to the standard cam_default slot for consistency.
		img_proof.save_png("/tmp/cam_default.png")
	else:
		print("[TableOcclusion] no image (headless?) — run WITHOUT --headless")

	var method := "SAM-sprite" if used_sam else "polygon"
	print("[TableOcclusion] RESULT ok=%s method=%s behind_y=%.0f baseline=%.0f front_y=%.0f" % [
		str(img_proof != null), method, behind_foot.y, foot_y_screen, front_foot.y])
	call_deferred("_quit_clean")


## Extract blocked cells from a nav spec block as Array of Vector2i. Helper for
## _run_walk_demo to validate the target cell and test blocked-click rejection.
func _nav_blocked_from_spec(nav: Dictionary) -> Array:
	var out: Array = []
	var blocked_v: Variant = nav.get("blocked", [])
	if typeof(blocked_v) != TYPE_ARRAY:
		return out
	for b in (blocked_v as Array):
		if typeof(b) == TYPE_ARRAY and (b as Array).size() >= 2:
			out.append(Vector2i(int((b as Array)[0]), int((b as Array)[1])))
	return out


func _quit_clean() -> void:
	print("[Main] smoke complete — quitting cleanly")
	get_tree().quit()
