"""Which model each kind of sub-agent runs on, by the story's risk rating: `cgp config reviewerModel low:haiku,medium:sonnet,high:opus`
(or one name for every rating). Workers pass the answer of `cgp models <rating>` as the Agent tool's `model`."""
from .util import die, out
from .store import cfg

ROLES = ("planner", "reviewer", "implementer")
NAMES = ("sonnet", "opus", "haiku", "fable")
RATINGS = ("low", "medium", "high")


def parse(value):
    """{rating: model} from a setting value; dies on anything it does not understand. An empty value means 'the session's model'."""
    value = (value or "").strip()
    if not value:
        return {}
    if ":" not in value and "," not in value:
        pairs = [(r, value) for r in RATINGS]
    else:
        pairs = [tuple(p.strip().split(":", 1)) if ":" in p else (p.strip(), "") for p in value.split(",")]
    res = {}
    for rating, model in pairs:
        if rating not in RATINGS or model not in NAMES:
            die(f"model setting must be one of {list(NAMES)} or rating:model pairs like low:haiku,medium:sonnet,high:opus; got {value!r}")
        res[rating] = model
    return res


def cmd_models(a):
    c = cfg()
    out({role: parse(c["settings"].get(f"{role}Model")).get(a.rating) for role in ROLES})
