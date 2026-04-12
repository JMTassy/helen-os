import json
import time
import uuid
from typing import Dict, Any

class MemoryKernel:
    """
    Handles persistent storage and retrieval of conversational memory
    from a newline-delimited JSON file.
    """
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data: Dict[str, Any] = {}
        self._load_memory()

    def _load_memory(self):
        """Loads the memory history from the ndjson file."""
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines:
                    try:
                        record = json.loads(line.strip())
                        if record.get("type") == "memory_observation":
                            event_id = record.get("event_id")
                            if event_id not in self.data:
                                self.data[event_id] = record
                    except json.JSONDecodeError:
                        # Skip malformed lines
                        continue
        except FileNotFoundError:
            pass

    def replay_kv(self) -> Dict[str, str]:
        """
        Recreates a simplified key-value dictionary view of the most recent context.
        Returns a dictionary mapping the key to the value
        from the most recent observation found for that key.
        """
        latest_values: Dict[str, str] = {}
        
        # Sort by timestamp (t) to process chronologically
        sorted_events = sorted(self.data.values(), key=lambda x: x.get('t', ''))
        
        for event in sorted_events:
            key = event.get("key")
            value = event.get("value")
            if key and value:
                # Overwrite ensures we get the latest value seen for that key
                latest_values[key] = value

        return latest_values

    def append(self, key: str, value: str, actor: str, status: str):
        """
        Appends a new observation record to the memory file and updates internal state.
        """
        event_id = f"ev_{uuid.uuid4().hex[:16]}"
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        new_record = {
            "schema_version": "MEMORY_V1",
            "event_id": event_id,
            "t": timestamp,
            "type": "memory_observation",
            "actor": actor,
            "key": key,
            "value": value,
            "status": status
        }
        
        line = json.dumps(new_record)
        
        try:
            with open(self.file_path, 'a', encoding='utf-8') as f:
                f.write(line + "\n")
        except IOError as e:
            print(f"Error writing to memory file: {e}")
            return

        # Update internal state as well
        self.data[event_id] = new_record
