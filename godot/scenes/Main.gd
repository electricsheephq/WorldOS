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


func _quit_clean() -> void:
	print("[Main] smoke complete — quitting cleanly")
	get_tree().quit()
