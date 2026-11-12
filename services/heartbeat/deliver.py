"""Part 5: where the heartbeat's output goes, and its state between runs.

    outputs/heartbeat/alerts.jsonl     every alert or hold delivered to a mandate that holds the BDC
    outputs/heartbeat/decisions.jsonl  every decision, including not_material and not_published
    outputs/heartbeat/briefs/<day>-<mandate>.md   the 07:30 brief, for Priya
    outputs/heartbeat/hygiene/<day>.md the Monday hygiene report, for Priya
    logs/audit/<day>.jsonl             the saved ledger: ids and hashes only
    .cache/heartbeat/state.json        what was already seen (git-ignored)
"""
import hashlib
import json
from pathlib import Path

from creditwatch import audit, clock


class Out:
    def __init__(self, root, now):
        self.root, self.now = Path(root), now
        self.dir = self.root / "outputs" / "heartbeat"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.root / "logs" / "audit").mkdir(parents=True, exist_ok=True)
        self.ledger = audit.Ledger(path=self.root / "logs" / "audit" / f"{now.date().isoformat()}.jsonl",
                                   clock=lambda: now)
        self.state_path = self.root / ".cache" / "heartbeat" / "state.json"

    def _append(self, name, record):
        with open(self.dir / name, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def alert(self, record):
        """An alert or a hold, for a mandate that holds the BDC."""
        self._append("alerts.jsonl", record)
        self.decision({k: record[k] for k in ("alert_id", "ts", "mandate", "bdc", "trigger", "rule", "action")})
        self.ledger.record("alert_sent" if record["action"] == "alert" else "alert_held",
                           alert_id=record["alert_id"], mandate=record["mandate"], rule=record["rule"])

    def decision(self, record):
        self._append("decisions.jsonl", record)

    def report(self, sub, name, text, **event):
        d = self.dir / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
        self.ledger.record(f"{sub.rstrip('s')}_sent", file=f"{sub}/{name}",
                           text_hash=hashlib.sha256(text.encode()).hexdigest(), **event)

    def state(self):
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"last_release": None, "release_day": None, "seen": [], "briefed": []}

    def save(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True))


def now_or_clock(now):
    return now or clock.clock()
