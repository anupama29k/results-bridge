"""MCP server for Results Bridge (M5).

Exposes the tool layer (tools.py) over the Model Context Protocol, so an
AI assistant — Claude Desktop, for instance — can operate this service in
conversation: "check my Qubit export against library-prep criteria."

Run (needs Python 3.10+ and `pip install -r requirements-mcp.txt`):

    ROBOTIC_ASSAYS_PATH=~/robotic-assays python -m results_bridge.mcp_server

Claude Desktop config (Settings → Developer → Edit Config) entry:

    "results-bridge": {
      "command": "/path/to/python3.12",
      "args": ["-m", "results_bridge.mcp_server"],
      "env": {
        "PYTHONPATH": "/Users/anu/results-bridge/src",
        "ROBOTIC_ASSAYS_PATH": "/Users/anu/robotic-assays"
      }
    }

The tests do NOT go through this file — they test tools.py directly, which
is why this wrapper stays skeletal on purpose.
"""
import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        "The MCP SDK needs Python 3.10+. Your tests still run on any Python — "
        "only serving MCP requires the newer interpreter (e.g. `brew install python@3.12`)."
    )

from mcp.server.fastmcp import FastMCP  # noqa: E402  (import after version gate)

from results_bridge import tools  # noqa: E402

mcp = FastMCP("results-bridge")


@mcp.tool()
def list_assays() -> list:
    """List every assay Results Bridge can validate against, with its
    acceptance range and unit. Call this first to discover valid assay ids."""
    return tools.list_assays()


@mcp.tool()
def check_file(file_path: str, assay_id: str) -> dict:
    """Parse an instrument export file (Qubit CSV, Tecan Spark / BMG
    CLARIOstar Excel, TapeStation export) at the given absolute path and
    validate every sample against the named assay's acceptance criteria.
    Returns per-sample PASS/FAIL/INVALID verdicts with reasons and a
    summary. Records an audit row; never writes to Benchling."""
    return tools.check_file(file_path, assay_id)


@mcp.tool()
def recent_audit(limit: int = 20) -> list:
    """Return the most recent audit-log entries (newest first): every file
    this service has checked or ingested, with verdict counts and delivery
    status. The traceability view."""
    return tools.recent_audit(limit)




@mcp.tool()
def search_protocols(query: str) -> list:
    """Search the robotic-assays protocol library by assay name
    (case-insensitive substring). Returns compact hits with source module,
    automation difficulty, and regulatory references. Use get_protocol for
    the full entry."""
    return tools.search_protocols(query)


@mcp.tool()
def get_protocol(name: str) -> dict:
    """Full protocol entry for one assay from the robotic-assays library:
    all acceptance criteria (including curve- and ratio-based ones the
    validator cannot score numerically), troubleshooting guidance,
    regulatory references, and automation notes. Exact name match — use
    search_protocols to discover names."""
    return tools.get_protocol(name)


if __name__ == "__main__":
    mcp.run()
