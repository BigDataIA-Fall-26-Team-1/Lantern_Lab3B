"""Part 6: the session's pending actions, server-side.

A payload is stored by the server (draft_email, or watchlist_write without a
token) and approved by Priya for exactly those bytes. The payloads follow
notebook 29: an email is {draft_id, scope, to, subject, body, attachments};
a watchlist entry is {draft_id, scope, bdc, reason}.
"""


class ActionStore:
    def __init__(self):
        self.pending = {}          # draft_id -> payload
        self.requested = set()     # draft_ids with an approval request
        self.sent = set()          # draft_ids already sent or written: one use only
        self._n = 0

    def new_id(self, prefix):
        self._n += 1
        return f"{prefix}-{self._n:04d}"

    def put(self, payload):
        self.pending[payload["draft_id"]] = payload

    def get(self, draft_id):
        return self.pending.get(draft_id)
