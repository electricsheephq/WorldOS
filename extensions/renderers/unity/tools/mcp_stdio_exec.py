#!/usr/bin/env python3
"""Drive mcp-for-unity over STDIO. In Stdio mode the Editor is the TCP server on
127.0.0.1:6400 and this python server connects TO it, so no transport switch is
needed. Usage:
  MCP_BIN=/path/to/mcp-for-unity python3 mcp_stdio_exec.py list
  MCP_BIN=... python3 mcp_stdio_exec.py call <tool> '<json>'      (or @payload.json)
Env: MCP_TIMEOUT (s, default 180), MCP_STDERR (server stderr log path).
"""
import json, os, select, subprocess, sys, time

BIN = os.environ.get("MCP_BIN", "mcp-for-unity")
ERR = open(os.environ.get("MCP_STDERR", os.devnull), "ab")
proc = subprocess.Popen([BIN, "--transport", "stdio"], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=ERR, text=True, bufsize=1)
_id = 0


def send(method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not notify:
        _id += 1
        msg["id"] = _id
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    return None if notify else _id


def recv(want_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], 1.0)
        if not r:
            if proc.poll() is not None:
                raise SystemExit(f"server exited rc={proc.returncode}")
            continue
        line = proc.stdout.readline()
        if not line:
            raise SystemExit("server closed stdout")
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("id") == want_id:
            return obj
    raise SystemExit(f"timeout after {timeout}s waiting for id {want_id}")


def call(tool, args, timeout):
    """tools/call with a bounded retry while the server is still attaching to Unity."""
    end = time.time() + timeout
    while True:
        r = recv(send("tools/call", {"name": tool, "arguments": args}), timeout)
        text = json.dumps(r)
        if "no_unity_session" in text and time.time() < end:
            time.sleep(2)
            continue
        return r


def main():
    i = send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "worldos-stdio-exec", "version": "0.1"}})
    init = recv(i, 60)
    print("server:", json.dumps(init.get("result", {}).get("serverInfo")), file=sys.stderr)
    send("notifications/initialized", notify=True)
    timeout = float(os.environ.get("MCP_TIMEOUT", "180"))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        r = recv(send("tools/list", {}), 60)
        print(json.dumps([t["name"] for t in r["result"]["tools"]]))
    else:
        tool = sys.argv[2]
        raw = sys.argv[3] if len(sys.argv) > 3 else "{}"
        args = json.load(open(raw[1:])) if raw.startswith("@") else json.loads(raw)
        r = call(tool, args, timeout)
        out = r.get("result", r.get("error"))
        print(json.dumps(out, indent=1)[:8000])
    try:
        proc.stdin.close()
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
