"""Part 0: the terminal chat.

    PYTHONPATH=src python -m services.chat.cli --mandate mandate_a

Asks for the session token without echoing it, opens a session bound to ONE
mandate, and prints that mandate's context report. There is no way to switch
clients inside a session: /quit, then start a new session.
"""
import argparse
import getpass
import sys

from services.chat.session import SkillsTampered, load_config, open_session, report_lines

PLACEHOLDER = ("(Part 0: this session is bound to {m}. The agent loop that answers "
               "arrives in Part 2.)")


def main(argv=None, ask=input, ask_secret=getpass.getpass, out=print):
    P, _, _ = load_config()
    ap = argparse.ArgumentParser(description="Credit Watch chat (CLI)")
    ap.add_argument("--principal", default=P["agent"]["principal"])
    ap.add_argument("--mandate", required=True, help="the one client this session serves")
    args = ap.parse_args(argv)

    try:
        o = open_session(args.principal, ask_secret("Session token: "), args.mandate)
    except SkillsTampered as e:
        out(f"Start-up refused: {e}")
        return 1
    except PermissionError as e:
        out(f"Login refused: {e}")
        return 1

    m = o.session.mandate
    out(f"Logged in as {o.session.principal}. Session bound to {m} ({o.client}).")
    out("Context assembled for this mandate only:")
    for line in report_lines(o.report):
        out("  " + line)
    out("Type a message, or /quit to end the session.")

    while True:
        try:
            msg = ask(f"[{m}] {o.session.principal}> ").strip()
        except (EOFError, KeyboardInterrupt):
            out("")
            break
        if msg == "/quit":
            break
        if msg:
            out(PLACEHOLDER.format(m=m))
    out("Session ended. To work on another client, start a new session.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
