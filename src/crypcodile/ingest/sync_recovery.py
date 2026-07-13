import os
import json
import logging

log = logging.getLogger(__name__)

class SyncRecovery:
    def __init__(self, state_path: str) -> None:
        self.state_path = state_path
        self.state = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    self.state = json.load(f)
            except Exception as e:
                log.warning(f"Failed to load sync recovery state from {self.state_path}: {e}")
                self.state = {}

    def _save(self) -> None:
        tmp_path = self.state_path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(self.state, f)
            os.replace(tmp_path, self.state_path)
        except Exception as e:
            log.error(f"Failed to save sync recovery state: {e}")

    def get_last_block(self, pool: str) -> int | None:
        val = self.state.get(pool)
        return int(val) if val is not None else None

    def save_last_block(self, pool: str, block: int) -> None:
        self.state[pool] = block
        self._save()

    def get_seen_logs(self) -> dict[tuple[str, int], bool]:
        raw = self.state.get("seen_logs", [])
        res = {}
        for entry in raw:
            if isinstance(entry, list) and len(entry) >= 2:
                res[(str(entry[0]), int(entry[1]))] = True
        return res

    def save_seen_logs(self, logs: dict[tuple[str, int], bool]) -> None:
        serialized = [[k[0], k[1]] for k in logs.keys()]
        self.state["seen_logs"] = serialized
        self._save()

