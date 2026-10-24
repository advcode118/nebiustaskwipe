import re
from urllib.parse import urlparse

from core.models import MemoryFact

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,}$")
POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.I)

YOU_LABELS = re.compile(
    r"\b(name|email|e-mail|phone|tel|mobile|university|college|course|programme|program|"
    r"address|street|city|town|postcode|zip|country|delivery notes?)\b",
    re.I,
)
RUN_LABELS = re.compile(
    r"\b(why|subject|title|message|body|motivation|cover|query|search|tracking|"
    r"when|date|time|description|caption)\b",
    re.I,
)
YOU_KEYS = {
    "full_name": "identity",
    "first_name": "identity",
    "last_name": "identity",
    "email": "identity",
    "phone": "identity",
    "university": "work",
    "course": "work",
    "programme": "work",
    "shipping.line1": "shipping",
    "shipping.city": "shipping",
    "shipping.postcode": "shipping",
    "shipping.notes": "shipping",
}


def start_from_events(events):
    for event in events or []:
        url = event.get("url") or ""
        if event.get("type") == "navigate" and url:
            parsed = urlparse(url)
            return url, parsed.netloc
        if url:
            parsed = urlparse(url)
            return url, parsed.netloc
    return "", ""


def _label_of(event):
    return " ".join(
        part for part in [event.get("name"), event.get("innerText"), event.get("selector")] if part
    )


def _key_for(label, value):
    lower = (label or "").lower()
    if EMAIL_RE.match(value or ""):
        return "email", "identity"
    if PHONE_RE.match((value or "").strip()) and any(ch.isdigit() for ch in value):
        return "phone", "identity"
    if POSTCODE_RE.match((value or "").strip()):
        return "shipping.postcode", "shipping"
    if "email" in lower:
        return "email", "identity"
    if re.search(r"\b(phone|tel|mobile)\b", lower):
        return "phone", "identity"
    if re.search(r"\b(legal name|full name|^name$)\b", lower):
        return "full_name", "identity"
    if "university" in lower or "college" in lower:
        return "university", "work"
    if "course" in lower or "programme" in lower or "program" in lower:
        return "course", "work"
    if "line 1" in lower or "address line" in lower or lower.strip() == "address":
        return "shipping.line1", "shipping"
    if re.search(r"\bcity\b", lower):
        return "shipping.city", "shipping"
    if "postcode" in lower or "zip" in lower:
        return "shipping.postcode", "shipping"
    if "delivery note" in lower:
        return "shipping.notes", "shipping"
    slug = re.sub(r"[^a-z0-9]+", "_", lower).strip("_")[:40] or "field"
    return slug, "other"


def _pile_for(label, value, key):
    if key in YOU_KEYS:
        return "yours"
    if EMAIL_RE.match(value or "") or PHONE_RE.match((value or "").strip()) or POSTCODE_RE.match((value or "").strip()):
        return "yours"
    if YOU_LABELS.search(label or "") and not RUN_LABELS.search(label or ""):
        return "yours"
    if RUN_LABELS.search(label or ""):
        return "this_run"
    if len(value or "") > 80:
        return "this_run"
    if YOU_LABELS.search(label or ""):
        return "yours"
    return "this_run"


def fields_from_events(events):
    latest = {}
    for event in events or []:
        if event.get("is_secret"):
            continue
        if event.get("type") not in {"input", "change"}:
            continue
        value = str(event.get("value") or "").strip()
        if not value:
            continue
        label = _label_of(event) or event.get("role") or "field"
        key, category = _key_for(label, value)
        latest[key] = {
            "key": key,
            "label": (event.get("name") or label)[:120],
            "value": value,
            "category": YOU_KEYS.get(key, category),
            "role": event.get("role") or "",
            "reason": "",
        }
    yours, this_run = [], []
    for item in latest.values():
        pile = _pile_for(item["label"], item["value"], item["key"])
        item["pile"] = pile
        item["reason"] = "looks like you" if pile == "yours" else "changes every time you do this"
        if pile == "yours":
            yours.append(item)
        else:
            this_run.append(item)
    return yours, this_run


def annotate_existing(yours):
    annotated = []
    for item in yours:
        copy = dict(item)
        fact = MemoryFact.objects.filter(key=copy["key"]).first()
        if fact:
            copy["existing"] = fact.value
            copy["conflict"] = fact.value != copy["value"]
        else:
            copy["existing"] = None
            copy["conflict"] = False
        annotated.append(copy)
    return annotated


def merge_model_split(heuristic_yours, heuristic_run, model_yours, model_inputs):
    yours_by_key = {item["key"]: dict(item) for item in heuristic_yours}
    run_by_name = {}
    for item in heuristic_run:
        run_by_name[item["key"]] = {
            "name": item["key"],
            "type": "string",
            "label": item["label"],
            "value_from_demo": item["value"],
            "reason": item.get("reason") or "changes every run",
        }

    for raw in model_yours or []:
        key = str(raw.get("key") or "").strip()
        value = str(raw.get("value") or "").strip()
        if not key or not value:
            continue
        yours_by_key[key] = {
            "key": key,
            "label": raw.get("label") or key,
            "value": value,
            "category": raw.get("category") or YOU_KEYS.get(key) or "other",
            "reason": raw.get("reason") or "model: this is you",
            "pile": "yours",
        }
        run_by_name.pop(key, None)

    for raw in model_inputs or []:
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        yours_by_key.pop(name, None)
        run_by_name[name] = {
            "name": name,
            "type": raw.get("type") or "string",
            "label": raw.get("label") or name,
            "value_from_demo": raw.get("value_from_demo") or raw.get("value") or "",
            "reason": raw.get("reason") or "model: type this next time",
        }

    yours = annotate_existing(list(yours_by_key.values()))
    this_run = []
    seen_values = set()
    for item in run_by_name.values():
        value = (item.get("value_from_demo") or "").strip().lower()
        if value and value in seen_values:
            continue
        if value:
            seen_values.add(value)
        this_run.append(item)
    return yours, this_run


def build_demonstration(events, yours, this_run):
    yours_values = {item["value"]: item for item in yours}
    run_values = {item.get("value_from_demo") or item.get("value"): item for item in this_run}
    steps = []
    seen = set()
    for event in events or []:
        if event.get("type") == "click":
            name = event.get("name") or event.get("innerText") or ""
            if name and name not in seen:
                steps.append({"action": "click", "field": name})
                seen.add("click:" + name)
            continue
        if event.get("type") not in {"input", "change"}:
            continue
        value = str(event.get("value") or "").strip()
        if not value or event.get("is_secret"):
            continue
        field = event.get("name") or event.get("role") or "field"
        marker = field + ":" + value
        if marker in seen:
            continue
        seen.add(marker)
        if value in yours_values:
            src = yours_values[value]
            steps.append(
                {
                    "action": "fill",
                    "field": field,
                    "source": "memory",
                    "key": src["key"],
                    "demo_value": value,
                }
            )
        elif value in run_values:
            src = run_values[value]
            steps.append(
                {
                    "action": "fill",
                    "field": field,
                    "source": "input",
                    "key": src.get("name"),
                    "demo_value": value,
                }
            )
        else:
            steps.append({"action": "fill", "field": field, "source": "literal", "demo_value": value})
    return steps
