extends Node
## Config — boot-time resolution of the engine endpoint + campaign id.
##
## CONTRACT ROLE: the single place that decides WHERE the renderer talks to the
## engine. Every other singleton reads `Config.base_url` / `Config.campaign`;
## NOTHING else hardcodes a host or port. The only literal port in the whole
## project lives here (DEFAULT_PORT), and it is the last-resort default.
##
## Resolution precedence (highest wins):
##   1. env WORLDOS_PLAY_PORT       -> http://127.0.0.1:<port>   (play.sh launch)
##   2. CLI user args --port/--campaign/--base   (OS.get_cmdline_user_args)
##   3. web export ?port=&campaign=&base=  (page query string via JavaScriptBridge)
##   4. default http://127.0.0.1:<DEFAULT_PORT>

const DEFAULT_HOST := "http://127.0.0.1"
const DEFAULT_PORT := 8765  ## the ONLY hardcoded port in the project

## Transport cadence + reconnect backoff (read by SurfaceClient).
const POLL_CADENCE_SEC := 2.0
const BACKOFF_BASE_SEC := 1.0
const BACKOFF_CAP_SEC := 15.0
## How fast the very first fetch fires after boot, so the smoke print lands
## within ~0.1s instead of waiting a full cadence.
const FIRST_FETCH_DELAY_SEC := 0.1

var base_url: String = ""
var campaign: String = ""


func _ready() -> void:
	_resolve()
	print("[Config] base_url=%s campaign=%s" % [base_url, campaign if campaign != "" else "<none>"])


func _resolve() -> void:
	# Start from the default; each higher-precedence source overrides below.
	var host := DEFAULT_HOST
	var port := DEFAULT_PORT
	var port_set := false

	# (3) lowest of the explicit sources: web export query string.
	if OS.has_feature("web"):
		var q := _web_query()
		if q.has("base") and String(q["base"]) != "":
			base_url = _trim_trailing_slash(String(q["base"]))
		if q.has("port") and String(q["port"]) != "":
			var p := String(q["port"]).to_int()
			if p > 0:
				port = p
				port_set = true
		if q.has("campaign"):
			campaign = String(q["campaign"])

	# (2) CLI user args (everything after `--` on the command line).
	var cli := _parse_cli(OS.get_cmdline_user_args())
	if cli.has("base") and cli["base"] != "":
		base_url = _trim_trailing_slash(cli["base"])
	if cli.has("port") and cli["port"] != "":
		var cp := String(cli["port"]).to_int()
		if cp > 0:
			port = cp
			port_set = true
	if cli.has("campaign"):
		campaign = cli["campaign"]

	# (1) highest precedence: the play.sh-injected env var.
	var env_port := OS.get_environment("WORLDOS_PLAY_PORT")
	if env_port != "":
		var ep := env_port.to_int()
		if ep > 0:
			port = ep
			port_set = true

	# If no explicit --base/?base= won, compose host:port. (An explicit base_url
	# is honored verbatim; a port from any source still recomposes it.)
	if base_url == "" or port_set:
		base_url = "%s:%d" % [host, port]


## Parse CLI user args into a flag map. Accepts `--flag value` and `--flag=value`.
func _parse_cli(args: PackedStringArray) -> Dictionary:
	var out: Dictionary = {}
	var i := 0
	while i < args.size():
		var a := args[i]
		if a.begins_with("--"):
			var body := a.substr(2)
			if body.contains("="):
				var bits := body.split("=", false, 1)
				out[bits[0]] = bits[1] if bits.size() > 1 else ""
			else:
				# value is the next token if present and not another flag
				var val := ""
				if i + 1 < args.size() and not args[i + 1].begins_with("--"):
					val = args[i + 1]
					i += 1
				out[body] = val
		i += 1
	return out


## Read the browser page's query string on a web export. Guarded by callers with
## OS.has_feature("web"); JavaScriptBridge does not exist on native builds.
func _web_query() -> Dictionary:
	var out: Dictionary = {}
	if not OS.has_feature("web"):
		return out
	# JavaScriptBridge.eval returns the raw query (without the leading "?").
	var raw_variant: Variant = JavaScriptBridge.eval("window.location.search.replace(/^\\?/, '')", true)
	var raw := String(raw_variant) if raw_variant != null else ""
	if raw == "":
		return out
	for pair in raw.split("&", false):
		var kv := pair.split("=", false, 1)
		if kv.size() == 0:
			continue
		var k := kv[0].uri_decode()
		var v := kv[1].uri_decode() if kv.size() > 1 else ""
		out[k] = v
	return out


func _trim_trailing_slash(s: String) -> String:
	return s.trim_suffix("/")


## Build a "?campaign=<id>" query suffix (URL-encoded), or "" when no campaign.
func campaign_query() -> String:
	if campaign == "":
		return ""
	return "?campaign=" + campaign.uri_encode()
