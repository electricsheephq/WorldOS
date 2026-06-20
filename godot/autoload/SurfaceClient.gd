extends Node
## SurfaceClient — THE ONLY HTTP boundary in the Godot renderer.
##
## CONTRACT ROLE (mirrors viewer/openworlds/render/surface-client.js): proves the
## "renderer is a thin client; engine is sole writer" model.
##   - READ:  GET the read-model surfaces (/atlas-surface, /character-surface,
##            /combat-surface) every Config.POLL_CADENCE_SEC, plus /events with a
##            `${sid}:${seq}` cursor. The renderer owns ZERO game state — it
##            re-fetches every tick; the ONLY thing cached across ticks is the
##            /events cursor.
##   - WRITE: the renderer's ONLY write is an INTENT via POST /move. It never
##            mutates world state; it asks the engine to.
##
## TRANSPORT FALLBACK: on any unreachable host / non-200, this is treated as a
## TRANSIENT failure — capped exponential backoff on the live host AND an
## immediate fall back to the bundled res://fixtures so the project renders
## standalone with no server. A real 200 flips back to LIVE.
##
## SWAPPABLE TRANSPORT: polling is the path today. `_try_sse()` is a marked stub
## for the StreamPeer SSE upgrade on /surface-stream (see TODO below); when it
## lands it slots in behind this same signal interface with identical payloads,
## so Main / WorldView never change.

signal snapshot_updated(atlas: Dictionary, combat: Dictionary, character: Dictionary)
signal events_appended(records: Array)
signal transport_mode_changed(mode: String)  ## mode in {"LIVE", "FIXTURE"}

const MODE_LIVE := "LIVE"
const MODE_FIXTURE := "FIXTURE"

## HTTPRequest is single-flight (one in-flight request per node), so we keep a
## small pool — one node per concurrent surface GET — plus a dedicated node for
## /events and one for POST /move.
const _SURFACES := ["atlas-surface", "character-surface", "combat-surface"]

var _mode: String = ""  ## "" until the first tick decides; then LIVE or FIXTURE
var _poll_timer: Timer
var _backoff_sec: float = 0.0
var _consecutive_failures: int = 0

## /events cursor — the SOLE piece of state cached across ticks. `_since` is the
## integer line cursor the engine's /events route advances via its `next` field;
## `_sid` is the active session id used to compose the globally-unique
## `${sid}:${seq}` dedup/order key (see server.py /events BUG2 note).
var _since: int = 0
var _sid: String = ""

## Pool of reusable HTTPRequest child nodes, keyed by purpose.
var _http_surface: Dictionary = {}  # surface name -> HTTPRequest
var _http_events: HTTPRequest
var _http_move: HTTPRequest

var _ticking: bool = false  ## guard against overlapping ticks if one runs long


func _ready() -> void:
	# Build the HTTPRequest pool (one per concurrent GET; Godot's HTTPRequest is
	# single-flight so concurrent surfaces each need their own node).
	for surface in _SURFACES:
		var http := HTTPRequest.new()
		http.name = "http_" + surface
		add_child(http)
		_http_surface[surface] = http
	_http_events = HTTPRequest.new()
	_http_events.name = "http_events"
	add_child(_http_events)
	_http_move = HTTPRequest.new()
	_http_move.name = "http_move"
	add_child(_http_move)

	# Steady-cadence poll timer.
	_poll_timer = Timer.new()
	_poll_timer.name = "poll_timer"
	_poll_timer.wait_time = Config.POLL_CADENCE_SEC
	_poll_timer.one_shot = false
	_poll_timer.timeout.connect(_on_poll_tick)
	add_child(_poll_timer)

	# Prefer SSE if available; otherwise poll. (_try_sse is a stub today.)
	if not _try_sse():
		# Fire the FIRST fetch fast (so a headless smoke prints within ~0.1s),
		# then start the steady cadence.
		_poll_timer.start()
		var first := get_tree().create_timer(Config.FIRST_FETCH_DELAY_SEC)
		first.timeout.connect(_on_poll_tick)


# ---------------------------------------------------------------------------
# SSE transport upgrade — STUB.
# ---------------------------------------------------------------------------
## TODO(#455 transport-upgrade): full StreamPeer-based SSE on /surface-stream.
## When implemented, subscribe to the push channel that emits the SAME
## {atlas, combat, character} payloads the GET surfaces return, emit
## snapshot_updated on each frame, and fall back to polling if no frame ever
## arrives. Returning false here keeps POLLING as the active path for #1052.
func _try_sse() -> bool:
	return false


# ---------------------------------------------------------------------------
# Polling tick: fetch all surfaces + the /events delta, emit signals.
# ---------------------------------------------------------------------------
func _on_poll_tick() -> void:
	if _ticking:
		return
	_ticking = true
	await _tick()
	_ticking = false


func _tick() -> void:
	# Fetch the three surfaces, each on its own pooled HTTPRequest node (Godot
	# HTTPRequest is single-flight, so the per-surface pool is what lets a future
	# transport overlap them). We await each in turn here — correct and simple for
	# the #1052 smoke; an overlapped-fetch optimization can layer in later without
	# changing this signal interface.
	var results: Dictionary = {}
	var any_live := false
	var any_failed := false

	for surface in _SURFACES:
		var res: Dictionary = await _fetch_surface(surface)
		results[surface] = res["data"]
		if res["live"]:
			any_live = true
		else:
			any_failed = true

	# Decide transport mode for this tick. If even one real 200 came back, we are
	# LIVE; if every surface failed, we are FIXTURE (and back off the live host).
	if any_live:
		_set_mode(MODE_LIVE)
		_consecutive_failures = 0
		_backoff_sec = 0.0
	else:
		_set_mode(MODE_FIXTURE)
		_bump_backoff()

	emit_signal(
		"snapshot_updated",
		results.get("atlas-surface", {}),
		results.get("combat-surface", {}),
		results.get("character-surface", {})
	)

	# /events delta — only meaningful against a live host; in fixture mode we read
	# the bundled events.json once-ish so the smoke has a shaped payload.
	var records := await _fetch_events()
	if not records.is_empty():
		emit_signal("events_appended", records)


## Fetch a single surface. Returns {data: Dictionary, live: bool}. On any
## unreachable host / non-200 / parse error, falls back to the bundled fixture
## and reports live=false.
func _fetch_surface(surface: String) -> Dictionary:
	# If we are already backing off the live host, skip the network attempt this
	# tick and serve fixtures directly (avoids hammering a dead host).
	if _mode == MODE_FIXTURE and _backoff_sec > 0.0 and not _backoff_elapsed():
		return {"data": _load_fixture(surface), "live": false}

	var http: HTTPRequest = _http_surface[surface]
	var url := Config.base_url + "/" + surface + Config.campaign_query()
	var err := http.request(url)
	if err != OK:
		return {"data": _load_fixture(surface), "live": false}

	var resp: Array = await http.request_completed
	# request_completed(result, response_code, headers, body)
	var result: int = resp[0]
	var code: int = resp[1]
	var body: PackedByteArray = resp[3]
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		return {"data": _load_fixture(surface), "live": false}

	var parsed: Variant = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		return {"data": _load_fixture(surface), "live": false}
	return {"data": parsed, "live": true}


## Fetch the /events delta and advance the `${sid}:${seq}` cursor. Returns the
## list of new event records (possibly empty). In fixture mode it returns the
## bundled events once (it never advances a real cursor against a dead host).
func _fetch_events() -> Array:
	if _mode == MODE_FIXTURE:
		# Standalone: surface the bundled events.json so listeners get a shaped
		# payload. We do NOT keep advancing a cursor against fixtures.
		var fx: Variant = _load_fixture_raw("events")
		if typeof(fx) == TYPE_DICTIONARY and fx.has("entries"):
			return fx["entries"]
		if typeof(fx) == TYPE_ARRAY:
			return fx
		return []

	var url := Config.base_url + "/events?since=" + str(_since)
	if Config.campaign != "":
		url += "&campaign=" + Config.campaign.uri_encode()
	var err := _http_events.request(url)
	if err != OK:
		return []
	var resp: Array = await _http_events.request_completed
	if resp[0] != HTTPRequest.RESULT_SUCCESS or resp[1] != 200:
		return []
	var parsed: Variant = JSON.parse_string((resp[3] as PackedByteArray).get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		return []

	# Advance the cursor: `next` is the new `since`, `sid` keys the dedup space.
	if parsed.has("next"):
		_since = int(parsed["next"])
	if parsed.has("sid") and typeof(parsed["sid"]) == TYPE_STRING:
		_sid = parsed["sid"]
	var entries: Variant = parsed.get("entries", [])
	return entries if typeof(entries) == TYPE_ARRAY else []


# ---------------------------------------------------------------------------
# The renderer's ONLY write: POST /move — a constrained INTENT, never a
# world-assertion. Returns {ok: bool, reason: String}.
# ---------------------------------------------------------------------------
func move(intent: Dictionary) -> Dictionary:
	# Carry the campaign with every intent (the engine scopes the move to it).
	var payload := intent.duplicate(true)
	payload["campaign"] = Config.campaign

	if _mode == MODE_FIXTURE:
		# Standalone: there is no engine to accept the intent. Echo a benign ok so
		# UI flows don't dead-end, and log what WOULD have been POSTed.
		print("[SurfaceClient] FIXTURE mode — would POST /move: ", payload)
		return {"ok": true, "reason": "fixture"}

	var headers := ["Content-Type: application/json"]
	var err := _http_move.request(
		Config.base_url + "/move",
		headers,
		HTTPClient.METHOD_POST,
		JSON.stringify(payload)
	)
	if err != OK:
		return {"ok": false, "reason": "request_failed"}
	var resp: Array = await _http_move.request_completed
	var result: int = resp[0]
	var code: int = resp[1]
	var body: PackedByteArray = resp[3]
	if result != HTTPRequest.RESULT_SUCCESS:
		return {"ok": false, "reason": "transport_error"}
	var parsed: Variant = JSON.parse_string(body.get_string_from_utf8())
	var data: Dictionary = parsed if typeof(parsed) == TYPE_DICTIONARY else {}
	var ok: bool = code == 200 and data.get("ok", true) != false
	var reason: String = String(data.get("reason", "")) if data.has("reason") else ("http_%d" % code)
	return {"ok": ok, "reason": reason}


# ---------------------------------------------------------------------------
# Mode / backoff helpers.
# ---------------------------------------------------------------------------
func _set_mode(mode: String) -> void:
	if _mode == mode:
		return
	_mode = mode
	emit_signal("transport_mode_changed", mode)


func mode() -> String:
	return _mode


func _bump_backoff() -> void:
	_consecutive_failures += 1
	# Capped exponential: base * 2^(n-1), clamped to the cap.
	var raw := Config.BACKOFF_BASE_SEC * pow(2.0, float(_consecutive_failures - 1))
	_backoff_sec = minf(raw, Config.BACKOFF_CAP_SEC)
	_backoff_started_ms = Time.get_ticks_msec()


var _backoff_started_ms: int = 0


func _backoff_elapsed() -> bool:
	if _backoff_sec <= 0.0:
		return true
	var elapsed_ms := Time.get_ticks_msec() - _backoff_started_ms
	return float(elapsed_ms) / 1000.0 >= _backoff_sec


# ---------------------------------------------------------------------------
# Bundled fixtures (res://fixtures/*.json) — the standalone fallback.
# ---------------------------------------------------------------------------
func _load_fixture(surface: String) -> Dictionary:
	var v: Variant = _load_fixture_raw(surface)
	return v if typeof(v) == TYPE_DICTIONARY else {}


func _load_fixture_raw(name: String) -> Variant:
	var path := "res://fixtures/%s.json" % name
	if not FileAccess.file_exists(path):
		push_warning("[SurfaceClient] missing fixture: " + path)
		return null
	var text := FileAccess.get_file_as_string(path)
	return JSON.parse_string(text)
