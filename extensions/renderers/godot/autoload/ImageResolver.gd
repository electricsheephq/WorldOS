extends Node
## ImageResolver — the /image?scope=<scope> bridge.
##
## CONTRACT ROLE: art is fetched lazily, by SCOPE KEY, from the engine's image
## endpoint (the same scope keys the render-profile declares, e.g.
## "scene-lower-city", "portrait-aubree", "sprite-aubree-iso8"). The resolver is
## the only thing that turns a scope into a Texture2D, and it caches the result so
## a scope is fetched at most once.
##
## STATES per scope: untried -> loading -> ok | miss.
##   - HTTP 200  -> build a Texture2D from the bytes, cache it, emit texture_ready.
##   - HTTP 404  -> mark "miss" and NEVER retry, so a PROCEDURAL fallback (a flat
##                  colored quad / placeholder token) can stand in deterministically
##                  instead of the resolver hammering a known-absent scope.
##
## Never blocks a frame: every fetch is async (await http.request_completed).
## Owns no game state — purely an art cache keyed by scope.

signal texture_ready(scope: String, texture: Texture2D)

enum State { UNTRIED, LOADING, OK, MISS }

var _state: Dictionary = {}     # scope -> State
var _textures: Dictionary = {}  # scope -> Texture2D (only when State.OK)
var _http_pool: Array[HTTPRequest] = []  # idle reusable request nodes


## Kick off (or no-op) a fetch for `scope`. Idempotent: a scope already loading,
## ok, or missed will not re-fetch.
func resolve(scope: String) -> void:
	if scope == "":
		return
	var st: int = _state.get(scope, State.UNTRIED)
	if st == State.LOADING or st == State.OK or st == State.MISS:
		return
	# Committed res:// backdrop art (mirrors the res:// character placeholders): a standalone /
	# fixture run with no live /image host still shows real painted location art instead of the
	# procedural gradient. A live served-finals /image is only consulted when this is absent.
	if _try_local_backdrop(scope):
		return
	_state[scope] = State.LOADING
	_do_fetch(scope)  # fire-and-forget coroutine; never blocks the frame


## Committed local backdrop fallback: res://assets/backdrops/<scope>.png, loaded directly (no
## HTTP) so a standalone/fixture run shows real painted art. Returns true on a hit (cached + OK).
func _try_local_backdrop(scope: String) -> bool:
	var path := "res://assets/backdrops/" + scope + ".png"
	if not ResourceLoader.exists(path):
		return false
	var tex := load(path) as Texture2D
	if tex == null:
		return false
	_textures[scope] = tex
	_state[scope] = State.OK
	emit_signal("texture_ready", scope, tex)
	return true


func _do_fetch(scope: String) -> void:
	var http := _acquire_http()
	var url := Config.base_url + "/image?scope=" + scope.uri_encode()
	if Config.campaign != "":
		url += "&campaign=" + Config.campaign.uri_encode()

	var err := http.request(url)
	if err != OK:
		# Transport couldn't even start — treat as untried so a later resolve()
		# can retry once the host is reachable (NOT a 404 "miss").
		_state[scope] = State.UNTRIED
		_release_http(http)
		return

	var resp: Array = await http.request_completed
	_release_http(http)
	var result: int = resp[0]
	var code: int = resp[1]
	var body: PackedByteArray = resp[3]

	if code == 404:
		# Definitive absence — mark miss and never retry.
		_state[scope] = State.MISS
		return
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		# Transient (timeout, host down, 5xx) — allow a future retry.
		_state[scope] = State.UNTRIED
		return

	var tex := _texture_from_bytes(body)
	if tex == null:
		# Got bytes but couldn't decode them — treat as a miss so a fallback stands.
		_state[scope] = State.MISS
		return

	_textures[scope] = tex
	_state[scope] = State.OK
	emit_signal("texture_ready", scope, tex)


## Return the cached Texture2D for `scope`, or null if it isn't loaded/ok.
func get_cached(scope: String) -> Texture2D:
	if _state.get(scope, State.UNTRIED) == State.OK:
		return _textures.get(scope, null)
	return null


## True once a scope has been definitively found absent (404). Lets callers commit
## to a procedural fallback without re-asking.
func is_missing(scope: String) -> bool:
	return _state.get(scope, State.UNTRIED) == State.MISS


# ---------------------------------------------------------------------------
# Decode bytes -> Texture2D. Detect format by content (PNG magic), then fall back
# to letting Image sniff WEBP/JPG. Returns null if nothing decodes.
# ---------------------------------------------------------------------------
func _texture_from_bytes(body: PackedByteArray) -> Texture2D:
	if body.size() == 0:
		return null
	var img := Image.new()
	var ok := false
	# PNG magic: 89 50 4E 47
	if body.size() >= 4 and body[0] == 0x89 and body[1] == 0x50 and body[2] == 0x4E and body[3] == 0x47:
		ok = img.load_png_from_buffer(body) == OK
	else:
		# Try JPG then WEBP; whichever succeeds wins.
		ok = img.load_jpg_from_buffer(body) == OK
		if not ok:
			ok = img.load_webp_from_buffer(body) == OK
		if not ok:
			# Last resort: PNG anyway (some servers send PNG without us sniffing).
			ok = img.load_png_from_buffer(body) == OK
	if not ok:
		return null
	return ImageTexture.create_from_image(img)


# ---------------------------------------------------------------------------
# Small pool of reusable HTTPRequest nodes (single-flight each). We grow it only
# when all are busy so concurrent scope fetches never share a node.
# ---------------------------------------------------------------------------
func _acquire_http() -> HTTPRequest:
	if not _http_pool.is_empty():
		return _http_pool.pop_back()
	var http := HTTPRequest.new()
	add_child(http)
	return http


func _release_http(http: HTTPRequest) -> void:
	_http_pool.push_back(http)
