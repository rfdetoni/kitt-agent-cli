"""Optional client for the shared KITT memory owned by kittd.

No third-party dependency and no hard requirement that KITT Assistant is installed.
"""
from __future__ import annotations
import json, os, socket, sys
from pathlib import Path
from typing import Any

class SharedMemoryUnavailable(RuntimeError): pass

class SharedMemoryClient:
    def __init__(self, address: str | None=None, token_path: str | Path | None=None, timeout: float=0.35):
        self.address=address or os.getenv("KITT_DAEMON_ADDR","127.0.0.1:41827")
        if os.name == "nt":
            config_root = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            config_root = Path.home() / "Library" / "Application Support"
        else:
            config_root = Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        default = config_root / "kitt" / "assistant" / "auth.token"
        self.token_path=Path(token_path) if token_path else default; self.timeout=timeout
    def _call(self, command: str, **payload: Any) -> Any:
        try: token=self.token_path.read_text(encoding="utf-8").strip(); host,port=self.address.rsplit(":",1)
        except Exception as exc: raise SharedMemoryUnavailable(str(exc)) from exc
        req={"token":token,"command":command,**payload}
        try:
            with socket.create_connection((host,int(port)),timeout=self.timeout) as sock:
                sock.sendall((json.dumps(req,ensure_ascii=False,separators=(",",":"))+"\n").encode())
                data=b""
                while not data.endswith(b"\n"):
                    chunk=sock.recv(65536)
                    if not chunk: break
                    data+=chunk
            response=json.loads(data.decode("utf-8"))
        except Exception as exc: raise SharedMemoryUnavailable(str(exc)) from exc
        if not response.get("ok"): raise SharedMemoryUnavailable(str(response.get("error","unknown shared memory error")))
        return response.get("result")
    def remember(self,workspace_id:str,content:str,kind:str="PROJECT_RULE",pinned:bool=True)->str:
        result=self._call("memory_remember",namespace="agent-cli",workspace_id=workspace_id,content=content,kind=kind,sensitivity="private",scope="workspace",pinned=pinned)
        return str(result["id"])
    def recall(self,workspace_id:str,query:str,limit:int=8)->list[dict[str,Any]]:
        result=self._call("memory_recall",namespace="agent-cli",workspace_id=workspace_id,query=query,limit=limit,allow_private=True)
        return list(result or [])
