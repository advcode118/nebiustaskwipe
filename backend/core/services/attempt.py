import re
from urllib.parse import urlparse

from core.models import Skill
from core.services.memory import STABLE_HINTS
from core.services.stages import ensure_one_stage

DEFAULT_INTENT = "Fill this page with my details, stopping before Submit/Pay."

_STOP = {"www", "com", "co", "uk", "org", "net", "io", "app", "the", "and", "for", "with"}


def _tokens(text):
    return {
        tok
        for tok in re.split(r"[^a-z0-9]+", (text or "").lower())
        if len(tok) > 2 and tok not in _STOP
    }


def _host_root(host):
    host = (host or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _host_related(left, right):
    if not left or not right:
        return False
    root = _host_root(left)
    if root != _host_root(right):
        return False
    return root not in {"localhost", "127.0.0.1"}


def match_skills(url, title=""):
    parsed = urlparse(url or "")
    host = parsed.netloc
    page_tokens = _tokens(" ".join([title, parsed.path, host]))
    matches = []
    for skill in Skill.objects.filter(ready=True):
        score = 0
        reasons = []
        if skill.site_host and host and skill.site_host == host:
            score += 4
            reasons.append("same site")
        elif _host_related(skill.site_host, host):
            score += 2
            reasons.append("related site")
        kind_tokens = _tokens(" ".join([skill.page_kind, skill.name, skill.goal]))
        overlap = page_tokens & kind_tokens
        if overlap:
            score += min(3, len(overlap))
            reasons.append("looks like " + (skill.page_kind or skill.name))
        if not score:
            continue
        data = skill.to_dict()
        data["match_score"] = score
        data["match_reason"] = ", ".join(reasons)
        matches.append(data)
    matches.sort(key=lambda item: (-item["match_score"], item["name"]))
    return matches[:5]


def _key_from_label(label, fallback=""):
    slug = re.sub(r"[^a-z0-9]+", "_", (label or fallback or "").lower()).strip("_")[:40]
    return slug or "field"


def keep_from_run(run):
    if run.skill_id and run.skill and run.skill.ready:
        raise ValueError("This run already belongs to a ready skill.")
    report = run.fill_report or {}
    keep = report.get("keep_as_skill") or {}
    filled = report.get("filled") or []
    skipped = report.get("skipped") or []
    yours = []
    this_run = []
    demonstration = []
    seen = set()
    for item in filled:
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        key = str(item.get("key") or "").strip() or _key_from_label(label)
        source = str(item.get("source") or "memory")
        if key in seen:
            continue
        seen.add(key)
        demonstration.append(
            {
                "action": "fill",
                "field": label or key,
                "source": "memory" if source in {"memory", "asked"} else "input",
                "key": key,
                "demo_value": value,
            }
        )
        if source in {"memory", "asked"} and value:
            yours.append(
                {
                    "key": key,
                    "label": label or key,
                    "value": value,
                    "category": STABLE_HINTS.get(key) or "other",
                    "reason": "from this attempt",
                }
            )
        else:
            this_run.append(
                {
                    "name": key,
                    "type": "string",
                    "label": label or key,
                    "value_from_demo": value,
                    "reason": "from this attempt",
                }
            )
    playbook = keep.get("playbook") or []
    if not isinstance(playbook, list) or not playbook:
        playbook = [f"Fill {item.get('label')}" for item in filled if item.get("label")]
        for label in skipped:
            playbook.append("Leave blank: " + str(label))
        playbook.append("Stop before Submit/Pay")
    start_url = run.current_url or ""
    site_host = urlparse(start_url).netloc if start_url else ""
    lessons = []
    for lesson in report.get("lessons") or []:
        text = str(lesson).strip()
        if text:
            lessons.append(text)
    skill = Skill.objects.create(
        name=(keep.get("name") or run.intent or "Untitled skill")[:200],
        goal=(keep.get("goal") or run.intent or DEFAULT_INTENT).strip(),
        playbook=playbook,
        page_kind=(keep.get("page_kind") or "").strip(),
        start_url=start_url,
        site_host=site_host,
        split_yours=yours,
        split_this_run=this_run,
        inputs_schema=this_run,
        memory_keys=[item["key"] for item in yours if item.get("key")],
        demonstration=demonstration,
        lessons=lessons,
        ready=False,
    )
    run.skill = skill
    run.save(update_fields=["skill", "updated_at"])
    ensure_one_stage(skill)
    return {"skill": skill.to_dict(), "split": {"yours": yours, "this_run": this_run}}
