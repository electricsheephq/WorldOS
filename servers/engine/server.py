"""ClawDnD game-engine MCP server.

Authoritative D&D 5e game state — dice, character sheets, combat & initiative,
conditions, encounters, XP/leveling — plus single-writer, atomic persistence so
a campaign survives context compaction and spans many sessions.

Epic 0 skeleton: only a health check today. Gameplay tools (roll, get/update
character, combat, save/load state) land in the Epic 0+ engine work — see the
repo issues.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("clawdnd-engine")


@mcp.tool()
def ping() -> str:
    """Health check. Returns ok if the ClawDnD engine server is reachable."""
    return "clawdnd-engine: ok (v0.0.1)"


if __name__ == "__main__":
    mcp.run()
