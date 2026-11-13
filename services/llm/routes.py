"""Part 2: model routing. Cheap classification goes to the small model (params.yaml model.routes):

    triage       what kind of request is this, and which skill fits? (every task)
    materiality  heartbeat-kind tasks, "which signals are material?", run whole on the small model
    agent        everything else: tool choice and drafting
"""
import json

from services.llm.model import body

KINDS = ["client_note", "isolation", "channel", "approval", "watchlist", "heartbeat", "other"]
TRIAGE = ("Classify one request to a credit-watch agent. Reply with JSON only: "
          '{"kind": one of ' + json.dumps(KINDS) + ', "skill": the name of the one best skill from the list, or null}. '
          "isolation: it asks about another client. channel: it asks to reply to a message. approval: it asks to "
          "send something. watchlist: it asks to change a watchlist. heartbeat: it asks to run the release tick or "
          "alert on material signals. client_note: a client note or monthly update.")


def triage(replay_client, cfg, prompt, skills_index, skill_names):
    req = body(cfg, cfg["routes"]["triage"], [
        {"role": "system", "content": TRIAGE},
        {"role": "user", "content": f"Skills:\n{skills_index}\n\nRequest:\n{prompt}"}],
        response_format={"type": "json_object"})
    out = replay_client(req)
    try:
        got = json.loads(out.get("text") or "{}")
    except json.JSONDecodeError:
        got = {}
    kind = got.get("kind") if got.get("kind") in KINDS else "other"
    skill = got.get("skill") if got.get("skill") in skill_names else None
    return {"kind": kind, "skill": skill, "model": out["model"], "usd": out["usd"]}


def route_for(kind):
    return "materiality" if kind == "heartbeat" else "agent"
