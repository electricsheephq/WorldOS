#!/usr/bin/env python3
"""Drive mcp-for-unity over STDIO. In Stdio mode the Unity Editor is the TCP server on
127.0.0.1:6400 (registry: ~/.unity-mcp/unity-mcp-status-*.json) and this python server
connects TO it, so no Editor transport change is needed.

Usage:
  MCP_BIN=/path/to/mcp-for-unity python3 mcp_stdio_exec.py list
  MCP_BIN=... python3 mcp_stdio_exec.py call <tool> '<json-args>'      (or @/path/payload.json)

Env: MCP_TIMEOUT  one time budget in seconds for attach retries + the call (default 180)
     MCP_STDERR   file that receives the server's stderr (default: discarded)
     MCP_MAX_OUT  0 (default) prints the COMPLETE result; N truncates stdout to N chars
Exit codes: 0 ok · 1 the MCP call failed (JSON-RPC error, isError, success:false, no session)
            · 2 transport failure (server exited / closed / timeout).
Finding MCP_BIN: the Editor-spawned HTTP server's command line names it —
  ps -o command= -p "$(cat <project>/Library/MCPForUnity/RunState/mcp_http_8080.pid)"
"""
import json
import os
import select
import subprocess
import sys
import time

BIN = os.environ.get("MCP_BIN", "mcp-for-unity")
MAX_OUT = int(os.environ.get("MCP_MAX_OUT", "0"))


class Timeout(Exception):
    pass


class Closed(Exception):
    pass


class Transport:
    """Newline-delimited JSON-RPC over the server's stdin/stdout (binary, self-buffered so a
    line the server already wrote is consumed before we ever wait on the pipe)."""

    def __init__(self, err_path):
        self.err = open(err_path, "ab")
        self.proc = subprocess.Popen([BIN, "--transport", "stdio"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.err)
        self.buf = bytearray()
        self._id = 0

    def close(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        finally:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.err.close()

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        return None if notify else self._id

    def _line(self, deadline):
        while True:
            nl = self.buf.find(b"\n")
            if nl >= 0:
                line = bytes(self.buf[:nl])
                del self.buf[:nl + 1]
                return line
            remaining = deadline - time.time()
            if remaining <= 0:
                raise Timeout()
            ready, _, _ = select.select([self.proc.stdout], [], [], min(remaining, 1.0))
            if not ready:
                if self.proc.poll() is not None:
                    raise Closed(f"server exited rc={self.proc.returncode}")
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise Closed("server closed stdout")
            self.buf += chunk

    def recv(self, want_id, deadline):
        while True:
            line = self._line(deadline)
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # non-JSON noise on stdout
            if obj.get("id") == want_id:
                return obj


def call_failed(reply):
    if reply.get("error") is not None:
        return True
    result = reply.get("result")
    if not isinstance(result, dict):
        return False
    if result.get("isError"):
        return True
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured.get("success") is False:
        return True
    return "no_unity_session" in json.dumps(result)


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "list"
    timeout = float(os.environ.get("MCP_TIMEOUT", "180"))
    deadline = time.time() + timeout  # ONE budget: attach retries + the call
    transport = Transport(os.environ.get("MCP_STDERR", os.devnull))
    rc = 2
    try:
        init = transport.recv(transport.send("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "worldos-stdio-exec", "version": "0.2"}}),
            min(deadline, time.time() + 60))
        print("server:", json.dumps(init.get("result", {}).get("serverInfo")), file=sys.stderr)
        transport.send("notifications/initialized", notify=True)
        if cmd == "list":
            reply = transport.recv(transport.send("tools/list", {}), deadline)
            print(json.dumps([t["name"] for t in reply["result"]["tools"]]))
            rc = 0
        elif cmd == "call" and len(argv) >= 2:
            tool = argv[1]
            raw = argv[2] if len(argv) > 2 else "{}"
            if raw.startswith("@"):
                with open(raw[1:]) as fh:
                    args = json.load(fh)
            else:
                args = json.loads(raw)
            while True:
                reply = transport.recv(
                    transport.send("tools/call", {"name": tool, "arguments": args}), deadline)
                if "no_unity_session" in json.dumps(reply) and time.time() < deadline - 2:
                    time.sleep(2)  # the server is still attaching to the Editor
                    continue
                break
            payload = reply.get("result") if reply.get("result") is not None else reply.get("error")
            text = json.dumps(payload, indent=1)
            print(text[:MAX_OUT] if MAX_OUT else text)
            rc = 1 if call_failed(reply) else 0
        else:
            print(__doc__, file=sys.stderr)
            rc = 2
    except Timeout:
        print(f"timeout after {timeout}s", file=sys.stderr)
        rc = 2
    except Closed as exc:
        print(str(exc), file=sys.stderr)
        rc = 2
    finally:
        transport.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
