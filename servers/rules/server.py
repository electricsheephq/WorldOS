"""ClawDnD rules MCP server.

Read-only D&D 5e rules reference. Serves bundled SRD 5.2 data (CC-BY-4.0) from
data/srd/ first (offline, canonical), with a dnd5eapi.co fallback for breadth.
Lookups use fuzzy / synonym matching so "fire ball" finds "Fireball".

Epic 0 skeleton: only a health check today. lookup_spell / lookup_monster /
lookup_rule / lookup_condition land with the SRD ingest — see the repo issues.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("clawdnd-rules")


@mcp.tool()
def ping() -> str:
    """Health check. Returns ok if the ClawDnD rules server is reachable."""
    return "clawdnd-rules: ok (v0.0.1)"


if __name__ == "__main__":
    mcp.run()
