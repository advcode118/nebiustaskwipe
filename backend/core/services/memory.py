import re

from core.models import MemoryFact

FORBIDDEN_TOKENS = {
    "password",
    "passwd",
    "secret",
    "token",
    "ssn",
    "card",
    "cvv",
    "cvc",
    "pan",
    "iban",
    "pin",
    "cvv2",
}

STABLE_HINTS = {
    "full_name": "identity",
    "first_name": "identity",
    "last_name": "identity",
    "email": "identity",
    "phone": "identity",
    "university": "study",
    "course": "study",
    "degree": "study",
    "major": "study",
    "school_email": "identity",
    "preferred_name": "identity",
    "study.year": "study",
    "study.start_year": "study",
    "study.end_year": "study",
    "shipping.line1": "shipping",
    "shipping.line2": "shipping",
    "shipping.city": "shipping",
    "shipping.postcode": "shipping",
    "shipping.country": "shipping",
    "shipping.notes": "shipping",
}


def is_forbidden(key, value=""):
    tokens = re.split(r"[^a-z0-9]+", f"{key} {value}".lower())
    if any(tok in FORBIDDEN_TOKENS for tok in tokens if tok):
        return True
    return bool(re.search(r"(?:\d[ -]*?){13,19}", str(value)))


def fact_for_key(key):
    existing = MemoryFact.objects.filter(key=key, is_default=True).first()
    if existing:
        return existing
    return MemoryFact.objects.filter(key=key).first()


def set_as_default(fact):
    if fact.group_key:
        MemoryFact.objects.filter(category=fact.category).exclude(group_key=fact.group_key).update(
            is_default=False
        )
        MemoryFact.objects.filter(category=fact.category, group_key=fact.group_key).update(is_default=True)
        fact.refresh_from_db()
        return fact
    MemoryFact.objects.filter(key=fact.key).exclude(pk=fact.pk).update(is_default=False)
    if not fact.is_default:
        fact.is_default = True
        fact.save(update_fields=["is_default", "updated_at"])
    return fact


MEMORY_CAP = 30
CATEGORY_ORDER = [
    "identity",
    "shipping",
    "study",
    "work",
    "sites",
    "people",
    "preferences",
    "other",
]


def _preferred_confirmed():
    facts = list(
        MemoryFact.objects.filter(confirmed=True).exclude(sensitivity="never").order_by(
            "-is_default", "category", "key"
        )
    )
    chosen = {}
    for fact in facts:
        current = chosen.get(fact.key)
        if current is None or (fact.is_default and not current.is_default):
            chosen[fact.key] = fact
    return list(chosen.values())


def _haystack(*parts):
    chunks = []
    for part in parts:
        if isinstance(part, (list, tuple)):
            chunks.extend(str(item or "") for item in part)
        else:
            chunks.append(str(part or ""))
    return " ".join(chunks).lower()


def _wanted_categories(skill, current_url="", tab_title="", goal="", labels=None):
    text = _haystack(
        current_url,
        tab_title,
        goal,
        labels,
        skill.page_kind if skill else "",
        skill.goal if skill else "",
        skill.start_url if skill else "",
        skill.memory_keys if skill else [],
    )
    wanted = {"identity"}
    if any(
        token in text
        for token in (
            "shipping",
            "address",
            "parcel",
            "postcode",
            "post code",
            "checkout",
            "delivery",
            "billing",
        )
    ):
        wanted.add("shipping")
    if any(
        token in text
        for token in (
            "university",
            "student",
            "internship",
            "course",
            "college",
            "imperial",
            "study",
            "ambassador",
            "form-a",
            "form-b",
            "apply",
            "application",
            "job",
            "career",
            "cv",
            "resume",
            "vacancy",
        )
    ):
        wanted.add("study")
        wanted.add("work")
        wanted.add("shipping")
    if any(token in text for token in ("youtube", "studio", "etsy", "shop", "channel")):
        wanted.add("sites")
    if skill:
        wanted.add("shipping")
        for key in skill.memory_keys or []:
            hint = STABLE_HINTS.get(key)
            if hint:
                wanted.add(hint)
    return wanted


def _fact_rows(extra=None):
    rows = [fact.to_dict() for fact in _preferred_confirmed()]
    for item in extra or []:
        if isinstance(item, dict) and item.get("key") and item.get("value"):
            rows.append(item)
    return rows


def lookup_stored_fact(key="", label="", question="", extra=None):
    facts = _fact_rows(extra)
    key = str(key or "").strip().lower()
    label = str(label or "").strip().lower()
    question = str(question or "").strip().lower()
    aliases = {
        "university_email": "school_email",
        "college_email": "school_email",
        "student_email": "school_email",
        "school_email": "school_email",
        "legal_first_name": "first_name",
        "legal_last_name": "last_name",
        "surname": "last_name",
    }
    wanted = [key]
    if key in aliases:
        wanted.append(aliases[key])
    if "school" in key or "university" in key or "college" in key:
        wanted.extend(["school_email", "email"])
    blob = " ".join([key, label, question]).strip()
    if re.search(r"school|university|college|student", blob) and "email" in blob:
        wanted.extend(["school_email", "email"])
    if "first" in blob and "name" in blob:
        wanted.append("first_name")
    if "last" in blob and "name" in blob:
        wanted.extend(["last_name"])
    if key:
        for want in wanted:
            for fact in facts:
                if str(fact.get("key") or "").lower() == want:
                    return fact
    if label:
        for fact in facts:
            if str(fact.get("label") or "").lower() == label:
                return fact
        for fact in facts:
            fact_label = str(fact.get("label") or "").lower()
            if len(fact_label) >= 4 and (fact_label in label or label in fact_label):
                return fact
    if blob:
        for fact in facts:
            fact_label = str(fact.get("label") or "").lower()
            if len(fact_label) >= 4 and fact_label in blob:
                return fact
            readable = str(fact.get("key") or "").lower().replace("_", " ").replace(".", " ")
            if len(readable) >= 4 and readable in blob:
                return fact
    if "email" in blob:
        for fact in facts:
            if str(fact.get("key") or "").lower() in {"email", "school_email"}:
                return fact
    return None


def memory_for_run(skill, current_url="", tab_title="", goal="", labels=None, broad=False):
    confirmed = _preferred_confirmed()
    preferred = set(skill.memory_keys or []) if skill else set()
    has_context = bool(skill or current_url or tab_title or goal or labels)
    if broad:
        has_context = False
    if has_context:
        wanted = _wanted_categories(skill, current_url, tab_title, goal, labels)
        chosen = []
        for fact in confirmed:
            if (
                fact.key in preferred
                or fact.category in wanted
                or (fact.key.startswith("shipping.") and "shipping" in wanted)
            ):
                chosen.append(fact)
        if not chosen:
            chosen = confirmed
    else:
        chosen = confirmed

    def sort_key(fact):
        cat_rank = CATEGORY_ORDER.index(fact.category) if fact.category in CATEGORY_ORDER else 99
        return (0 if fact.key in preferred else 1, cat_rank, fact.key)

    chosen.sort(key=sort_key)
    return [fact.to_dict() for fact in chosen[:MEMORY_CAP]]


def commit_yours(yours, skill, overwrite_keys=None, source=None):
    overwrite_keys = set(overwrite_keys or [])
    source = source or ("recording" if skill else "typed")
    if source not in MemoryFact.SOURCES:
        source = "typed"
    saved = []
    skipped = []
    conflicts = []
    for raw in yours or []:
        key = str(raw.get("key") or "").strip()
        value = str(raw.get("value") or "").strip()
        label = str(raw.get("label") or key).strip()
        category = str(raw.get("category") or STABLE_HINTS.get(key) or "other")
        if category not in MemoryFact.CATEGORIES:
            category = "other"
        if not key or not value:
            skipped.append({"key": key, "reason": "empty"})
            continue
        if is_forbidden(key, value):
            skipped.append({"key": key, "reason": "forbidden"})
            continue
        existing = fact_for_key(key)
        if existing:
            if existing.value == value:
                saved.append(existing.to_dict())
                continue
            if key in overwrite_keys:
                existing.value = value
                existing.label = label or existing.label
                existing.category = category
                existing.source_skill = skill
                existing.source = source
                existing.confirmed = True
                existing.save()
                saved.append(existing.to_dict())
                continue
            conflicts.append(
                {
                    "key": key,
                    "label": label,
                    "existing": existing.value,
                    "proposed": value,
                }
            )
            saved.append(existing.to_dict())
            continue
        fact = MemoryFact.objects.create(
            key=key,
            label=label or key,
            value=value,
            category=category,
            source_skill=skill,
            source=source,
            confirmed=True,
            is_default=True,
        )
        saved.append(fact.to_dict())
    return {"saved": saved, "skipped": skipped, "conflicts": conflicts}
