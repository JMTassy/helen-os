import json, uuid
from datetime import datetime
from pathlib import Path

STATUSES = {"OBSERVED","CONFIRMED","DISPUTED","RETRACTED"}

def now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()+"Z"

class MemoryKernel:
    def __init__(self, path: str = "memory/memory.ndjson"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, key: str, value, actor: str="assistant", status: str="OBSERVED"):
        if status not in STATUSES:
            raise ValueError("bad status")
        ev = {
            "schema_version":"MEMORY_V1",
            "event_id":"ev_"+uuid.uuid4().hex[:12],
            "t": now_iso(),
            "type":"memory_observation",
            "actor": actor,
            "key": key,
            "value": value,
            "status": status,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False)+"\n")
        return ev

    def replay_kv(self):
        kv = {}
        if not self.path.exists():
            return kv
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("type") == "memory_observation" and ev.get("status") in ("OBSERVED","CONFIRMED"):
                kv[ev["key"]] = ev["value"]
        return kv
