from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mcp_server import DesireMCPServer

HOST = os.environ.get("DESIRE_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("DESIRE_MCP_PORT", "8765"))
AUTH_TOKEN = os.environ.get("DESIRE_MCP_TOKEN", "")


class DesireMCPHTTPHandler(BaseHTTPRequestHandler):
    server_version = "AstrBotDesireMCP/2.0"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        if not AUTH_TOKEN:
            return True
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {AUTH_TOKEN}"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.rstrip("/") not in {"", "/mcp", "/health", "/dashboard", "/dashboard/"}:
            # 尝试作为静态文件处理
            if self.path.startswith("/static/"):
                self._serve_static(self.path)
            else:
                self._send_json(404, {"error": "not found"})
            return
        if self.path.rstrip("/") == "/health":
            self._send_json(200, {"ok": True, "name": "astrbot-desire-system"})
            return
        if self.path.rstrip("/") in {"/dashboard", "/dashboard/"}:
            self._serve_dashboard()
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        event = {
            "name": "astrbot-desire-system",
            "version": "2.0.0",
            "message": "MCP HTTP endpoint is ready. Send JSON-RPC requests with POST /mcp.",
        }
        self.wfile.write(f"event: ready\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _serve_dashboard(self) -> None:
        """提供 dashboard 页面"""
        import os
        dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
        try:
            with open(dashboard_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        except FileNotFoundError:
            self._send_json(404, {"error": "dashboard.html not found"})

    def _serve_static(self, path: str) -> None:
        """提供静态文件"""
        import os
        static_path = os.path.join(os.path.dirname(__file__), path.lstrip("/"))
        if os.path.exists(static_path) and os.path.isfile(static_path):
            content_type = "text/plain"
            if path.endswith(".css"):
                content_type = "text/css"
            elif path.endswith(".js"):
                content_type = "application/javascript"
            elif path.endswith(".png"):
                content_type = "image/png"
            elif path.endswith(".jpg"):
                content_type = "image/jpeg"
            
            with open(static_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self._send_json(404, {"error": "file not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") not in {"", "/mcp"}:
            self._send_json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            message = json.loads(raw)
            response = self.server.mcp.handle(message)  # type: ignore[attr-defined]
            if response is None:
                response = {"jsonrpc": "2.0", "result": None, "id": message.get("id")}
            self._send_json(200, response)
        except Exception as exc:
            self._send_json(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": str(exc)},
                },
            )

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("DESIRE_MCP_LOG", ""):
            super().log_message(fmt, *args)


class HeartbeatThread(threading.Thread):
    """自主心跳后台线程"""
    
    def __init__(self, mcp_server: DesireMCPServer):
        super().__init__(daemon=True)
        self.mcp_server = mcp_server
        self.running = True
    
    def run(self):
        print("Heartbeat thread started (HEARTBEAT_AUTONOMY=true)", flush=True)
        while self.running:
            try:
                # 执行一次 tick
                result = self.mcp_server.engine.tick()
                interval = result.get("next_interval", 1800)
                
                # 如果有心血来潮，打印日志
                if result.get("wildcard"):
                    print(f"[Heartbeat] Wildcard: {result['wildcard']['action']}", flush=True)
                
                print(f"[Heartbeat] Tick {result['tick']}, next in {interval}s", flush=True)
                
                # 等待下一次心跳
                time.sleep(interval)
            except Exception as e:
                print(f"[Heartbeat] Error: {e}", flush=True)
                time.sleep(60)  # 出错后等1分钟再试


class DesireMCPHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler]):
        super().__init__(server_address, handler_class)
        self.mcp = DesireMCPServer()
        
        # 如果开启了自主心跳，启动后台线程
        from desire.core import GateConfig
        if GateConfig.HEARTBEAT_AUTONOMY:
            self.heartbeat = HeartbeatThread(self.mcp)
            self.heartbeat.start()
        else:
            print("Heartbeat thread disabled (HEARTBEAT_AUTONOMY=false)", flush=True)


def main() -> None:
    server = DesireMCPHTTPServer((HOST, PORT), DesireMCPHTTPHandler)
    print(f"AstrBot Desire MCP HTTP server listening on http://{HOST}:{PORT}/mcp", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
