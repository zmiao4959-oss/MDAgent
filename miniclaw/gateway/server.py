"""
gateway/server.py — WebSocket Gateway 服务器
"""
import json
import asyncio
import websockets
from typing import Dict, Set
from ..config import config
from ..logger import get_logger

logger = get_logger(__name__)


class GatewayServer:
    """
    Gateway WebSocket Server
    
    协议（简化版）：
    - 客户端发送:  { "type": "req", "id": "uuid", "method": "...", "params": {...} }
    - 服务端回复:  { "type": "res", "id": "uuid", "ok": true, "payload": {...} }
    - 服务端推送:  { "type": "event", "event": "...", "payload": {...} }
    """
    
    def __init__(self, agent, host: str = None, port: int = None):
        self.agent = agent
        self.host = host or config.gateway.host
        self.port = port or config.gateway.port
        self.connections: Set[websockets.WebSocketServerProtocol] = set()
        self._stop_event = asyncio.Event()
        # 旧版：websocket: websockets.WebSocketServerProtocol
        # 新版（你用的）：websocket: ServerConnection
    
    async def _handler(self, ws: websockets.WebSocketServerProtocol, path: str):
        """处理一个 WebSocket 连接"""
        self.connections.add(ws)
        peername = ws.remote_address
        logger.info(f"Client connected: {peername}")
        
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send(json.dumps({
                        "type": "res", "id": None, "ok": False,
                        "error": "Invalid JSON",
                    }))
                    continue
                
                msg_type = msg.get("type")
                
                if msg_type == "req":
                    await self._handle_request(ws, msg)
                elif msg_type == "connect":
                    # 握手确认
                    await ws.send(json.dumps({
                        "type": "res", "id": msg.get("id"),
                        "ok": True, "payload": {
                            "status": "connected",
                            "version": "0.1.0",
                            "features": {"methods": ["agent", "send", "status"]},
                        },
                    }))
                else:
                    await ws.send(json.dumps({
                        "type": "res", "id": msg.get("id"), "ok": False,
                        "error": f"Unknown message type: {msg_type}",
                    }))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.connections.discard(ws)
            logger.info(f"Client disconnected: {peername}")
    
    async def _handle_request(self, ws, msg: dict):
        """处理客户端请求"""
        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})
        
        try:
            if method == "agent":
                # 调用 Agent 处理消息
                from ..agent import AgentContext
                ctx = AgentContext(
                    chat_id=params.get("chat_id", str(id(ws))),
                    channel=params.get("channel", "websocket"),
                    account_id=params.get("account_id", ""),
                    user_message=params.get("message", ""),
                )
                
                # 收集流式输出
                stream_chunks = []
                async def collect(chunk: str):
                    stream_chunks.append(chunk)
                    await ws.send(json.dumps({
                        "type": "event", "event": "agent",
                        "payload": {"status": "streaming", "delta": chunk, "runId": req_id},
                    }))
                
                response = await self.agent.process_message(ctx, on_stream_chunk=collect)
                
                await ws.send(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {"status": "done", "content": response, "runId": req_id},
                }))
            
            elif method == "status":
                import time
                mem_mb, cpu = 0, 0
                try:
                    import psutil
                    proc = psutil.Process()
                    mem_mb = proc.memory_info().rss / (1024 * 1024)
                    cpu = proc.cpu_percent(interval=0.1)
                except Exception:
                    pass

                await ws.send(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {
                        "uptime": "running",
                        "connections": len(self.connections),
                        "channels": list(self.agent.sessions._chat_to_session.keys()),
                        "memory_mb": round(mem_mb, 1),
                        "cpu_percent": cpu,
                        "timestamp": time.time(),
                    },
                }))
            
            elif method == "send":
                # 发送消息到指定 chat_id 的会话
                from ..agent import AgentContext
                ctx = AgentContext(
                    chat_id=params.get("chat_id", ""),
                    channel=params.get("channel", "websocket"),
                    account_id=params.get("account_id", ""),
                    user_message=f"[Proactive message from system]\n{params.get('message', '')}",
                )
                response = await self.agent.process_message(ctx)
                await ws.send(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {"content": response, "chat_id": ctx.chat_id},
                }))
            
            else:
                await ws.send(json.dumps({
                    "type": "res", "id": req_id, "ok": False,
                    "error": f"Unknown method: {method}",
                }))
        except Exception as e:
            logger.exception(f"Request handler error: {method}")
            await ws.send(json.dumps({
                "type": "res", "id": req_id, "ok": False,
                "error": str(e),
            }))
    
    async def start(self):
        logger.info(f"Gateway starting on {self.host}:{self.port}")
        self._stop_event.clear()
        async with websockets.serve(self._handler, self.host, self.port):
            await self._stop_event.wait()
        logger.info("Gateway stopped")

    async def stop(self):
        """通知 start() 退出，并关闭现有 WebSocket 连接。"""
        if self._stop_event.is_set():
            return
        if self.connections:
            await asyncio.gather(
                *(ws.close() for ws in list(self.connections)),
                return_exceptions=True,
            )
        self._stop_event.set()
    
    async def broadcast_event(self, event: str, payload: dict):
        """向所有连接的客户端广播事件"""
        msg = json.dumps({"type": "event", "event": event, "payload": payload})
        if self.connections:
            await asyncio.gather(*(ws.send(msg) for ws in self.connections))