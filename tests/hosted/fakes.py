"""Explicit test double. Never loaded by the production entrypoint."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from selflearn_core.store.supabase_store import StoreConflict

TEST_KEY = "fixture-only-owner-key-not-for-production-0001"


class FakeStore:
    def __init__(self):
        self.predictions = {}
        self.outcomes = {}
        self.objects = {}

    def status(self):
        return {"schema_version": 1, "predictions": len(self.predictions), "outcomes": len(self.outcomes), "models": 0}

    def get_prediction(self, id):
        return self.predictions.get(id)

    def append_prediction(self, prediction):
        if prediction.id in self.predictions and self.predictions[prediction.id] != prediction:
            raise StoreConflict()
        self.predictions[prediction.id] = deepcopy(prediction)
        return prediction.id

    def attach_outcome(self, outcome):
        if outcome.prediction_id in self.outcomes and self.outcomes[outcome.prediction_id] != outcome:
            raise StoreConflict()
        self.outcomes[outcome.prediction_id] = deepcopy(outcome)

    def recent(self, limit=25):
        return [{**asdict(p), "received_at": datetime.now(timezone.utc).isoformat(),
                 "outcomes": asdict(self.outcomes[p.id]) if p.id in self.outcomes else None}
                for p in list(self.predictions.values())[-limit:][::-1]]

    def request(self, method, path, *, data=None, raw=None, content_type=None, binary=False):
        if path.startswith("/storage/v1/object/list/"):
            return [{"name": name, "created_at": "2026-09-20T00:00:00Z"} for name in self.objects]
        name = path.rsplit("/", 1)[-1]
        if method == "POST":
            if name in self.objects:
                raise StoreConflict()
            self.objects[name] = raw
            return {}
        return self.objects[name]
