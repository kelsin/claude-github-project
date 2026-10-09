"""`cgp resume get|clear <item>`: whether a send-back may message the worker that last handled the story instead of spawning a new one.
For the /cgp:run loop only; workers never run it. The record (data key `resume`, "<item>|<column>" -> {agent, head, at}) is written by
`worker stop --outcome ok` (see session.record_resume) and only holds inside one live /cgp:run session."""
from .models import RATINGS
from .policy import current_rating
from .store import cfg, load_data, update_data
from .board import get_item
from .session import drop_resume, remote_head, resume_column
from .util import out

RESUMABLE = RATINGS[:2]  # low and medium; a high-risk story always starts a fresh worker


def verdict(c, it, d):
    mine = {k: v for k, v in (d.get("resume") or {}).items() if k.split("|")[0] == it["item"]}
    if not mine or it["kind"] != "issue":
        return {"resume": False, "reason": "no-record"}
    rec = mine.get(f"{it['item']}|{resume_column(it['column'])}")
    if not rec:
        return {"resume": False, "reason": "column-changed"}
    if remote_head(c, it) != rec.get("head"):
        return {"resume": False, "reason": "head-moved"}
    rating = current_rating(d, it)
    if rating is None:
        return {"resume": False, "reason": "unrated"}
    if rating not in RESUMABLE:
        return {"resume": False, "reason": "rating-high"}
    return {"resume": True, "agent": rec["agent"]}


def cmd_resume(a):
    if a.action == "clear":
        update_data(lambda d: drop_resume(d, a.item))
        out({"item": a.item, "cleared": True})
        return
    c = cfg()
    out(verdict(c, get_item(c, a.item), load_data()))
