"""Part 8: the stand-in model's plans, one per kind of task. TEMPORARY: Part 2 replaces this
file with the real model behind the replay client; the runner, the server and the grader stay.

The stand-in reads only what a model would see: the assembled context (msgs[0]) and the tool
observations. Every allowed, refused or blocked outcome comes from OUR server, not from here.
"$DRAFT" and "$TOKEN" are placeholders the runner fills in: the model never sees Priya's token.
"""
import json
import re

from creditwatch import standin
from services.heartbeat.jobs import short, signal_text

STEP_USD, TOKENS = 0.00128, (2000, 300)      # 2,000 in / 300 out at params.yaml's illustrative prices
ISOLATION_REPLY = ("That is another client's information, and I cannot share it. "
                   "I can go through this client's own BDC holdings instead.")


def _obs(msgs):
    return [(m["name"], json.loads(m["content"])) for m in msgs if m["role"] == "tool"]


def _last(msgs, name):
    return next((o for n, o in reversed(_obs(msgs)) if n == name), None)


def _contacts(msgs):
    m = re.search(r"^Contacts: (.+)$", msgs[0]["content"], re.M)
    return [x.strip() for x in m.group(1).split(",")] if m else []


def _held_named(msgs, text):
    """Held BDCs whose short name appears in the text, e.g. 'Hotel' or 'Juliett'."""
    prof = _last(msgs, "mandate_profile") or {}
    return [b for b in prof.get("positions", {}) if short(b).split()[0].lower() in text.lower()]


def _signals_for(msgs, bdcs):
    return [s for s in (_last(msgs, "bdc_signals") or []) if isinstance(s, dict) and s.get("name") in bdcs]


class ScriptedModel:
    """Plays its steps in order (each a function of what it has seen), then writes a final answer."""

    def __init__(self, steps, final):
        self.steps, self.final = steps, final

    def __call__(self, msgs):
        base = {"usd": STEP_USD, "tokens_in": TOKENS[0], "tokens_out": TOKENS[1]}
        done = sum(m["role"] == "tool" for m in msgs)
        if done < len(self.steps):
            return {**base, **self.steps[done](msgs)}
        return {**base, "tool": None, "text": self.final(msgs)}


def step(tool, rationale, **args):
    return lambda msgs: {"tool": tool, "args": args, "rationale": rationale}


def plan_for(task, mandate, message=None):
    cat, prompt = task.get("category", ""), task["prompt"]
    approved = "approv" in prompt.lower()
    profile = step("mandate_profile", "the client's positions", scope=mandate)
    sig = step("bdc_signals", "what changed this release", scope="public")
    hold = step("bdc_holdings", "terms behind each signal", scope="public")

    if cat == "isolation":
        other = task.get("must_not_mention")
        steps = [step("mandate_profile", "the question names another client", scope=other)] if other else []
        return ScriptedModel(steps, lambda msgs: ISOLATION_REPLY)

    if cat == "channel" and message:
        def draft(msgs):
            named = _held_named(msgs, message["body"])
            lines = [f"{signal_text(s)} [{s['adsh']}]." for s in _signals_for(msgs, named)]
            body = " ".join(["Thank you for your question."] + (lines or ["No material change this release."]))
            return {"tool": "draft_email", "rationale": "reply to the client's question, from this client's data only",
                    "args": {"scope": mandate, "to": [message["from"]], "subject": f"Re: {message['subject']}", "body": body}}

        def final(msgs):
            d = _last(msgs, "draft_email") or {}
            return (f"Drafted {d.get('draft_id')} for Priya's approval. It answers the question about this "
                    "client's own BDC only; the question about another client's holdings was not answered.")
        return ScriptedModel([profile, sig, draft], final)

    if cat == "approval":
        revise = "add" in prompt.lower() and "recipient" in prompt.lower()

        def draft(msgs, recipients=None, draft_id=None):
            cs = _contacts(msgs)
            to = recipients or [c for c in cs if c.startswith("cio@")][:1] or cs[:1]
            args = {"scope": mandate, "to": to, "subject": "BDC credit note", "body": standin.note_writer(msgs)}
            if draft_id:
                args["draft_id"] = draft_id
            return {"tool": "draft_email", "rationale": "the client note, for approval", "args": args}

        steps = [profile, sig, hold, draft, step("request_approval", "Priya approves the exact payload", draft_id="$DRAFT")]
        if revise:
            steps.append(lambda msgs: draft(msgs, recipients=[c for c in _contacts(msgs) if c.startswith(("cio@", "credit.pm@"))],
                                            draft_id="$DRAFT"))
        steps.append(step("send_email", "send the approved draft", scope=mandate, draft_id="$DRAFT", approval_token="$TOKEN"))

        def final(msgs):
            r = _last(msgs, "send_email") or {}
            if "error" in r:
                return f"The send was blocked: {r['error']} The changed draft needs a new approval."
            return f"Sent {r.get('message_id')} to the mock outbox after Priya's approval of the exact payload."
        return ScriptedModel(steps, final)

    if cat == "watchlist":
        def entry(msgs, token=None):
            named = _held_named(msgs, prompt)
            bdc = named[0] if named else "UNKNOWN BDC"
            sig = _signals_for(msgs, [bdc])
            parts = [signal_text(s).split(": ", 1)[1] for s in sig]
            reason = ("; ".join(parts) + f" ({sig[0]['adsh']})") if sig else "no material signal this release; review requested"
            args = {"scope": mandate, "draft_id": "w-0001", "bdc": bdc, "reason": reason}
            if token:
                args["approval_token"] = token
            return {"tool": "watchlist_write", "rationale": "a watchlist entry, for approval", "args": args}

        steps = [profile, sig, entry, step("request_approval", "Priya approves the exact entry", draft_id="w-0001")]
        if approved:
            steps.append(lambda msgs: entry(msgs, token="$TOKEN"))

        def final(msgs):
            r = _last(msgs, "watchlist_write") or {}
            if r.get("status") == "written":
                return f"Wrote watchlist entry {r['entry_id']} after Priya's approval of the exact entry."
            return "Requested Priya's approval for watchlist entry w-0001; nothing is written until she approves."
        return ScriptedModel(steps, final)

    if cat == "heartbeat":
        def final(msgs):
            prof = _last(msgs, "mandate_profile") or {}
            lines = [f"- {signal_text(s)} [{s['adsh']}]" for s in _signals_for(msgs, list(prof.get("positions", {})))]
            return "\n".join(["Material signals in this client's BDCs this release:"] + (lines or ["- none"])
                             + ["Alerts are delivered by the heartbeat on its schedule; nothing was sent."])
        return ScriptedModel([profile, sig], final)

    return ScriptedModel([profile, sig, hold], standin.note_writer)     # client_note, learning, anything else
