#!/bin/bash
export DESIRE_MCP_HOST="0.0.0.0"
export DESIRE_MCP_PORT="8765"
export DESIRE_MCP_TOKEN="desire-evanmind"
export DESIRE_DB_FILE="/opt/AstrBot-Desire-System-/desire_system.db"

# Gating 开关
export DESIRE_DRIVEN="true"
export DESIRE_COUPLING="true"
export DESIRE_BASELINE_DRIFT="true"
export HEARTBEAT_AUTONOMY="true"
export DESIRE_SELF_DRIVE="true"

python3 mcp_http_server.py
