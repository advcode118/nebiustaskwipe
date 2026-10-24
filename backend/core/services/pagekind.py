import json
import re
import sys

from django.conf import settings

from core.services.stages import current_stage, url_matches_pattern

KINDS = ("expected", "next_stage", "login", "blocker", "wrong")
BLOCKER_NAMES = (
    "accept all",
    "reject all",
    "accept cookies",
    "reject cookies",
    "allow all",
    "necessary only",
    "got it",
    "i agree",
    "close",
    "dismiss",
    "continue without",
    "not now",
    "maybe later",
)
BLOCKER_TEXT = re.compile(r"\b(cookie|cookies|consent|gdpr|privacy overlay|choose your (country|language))\b", re.I)
CLASSIFY_PROMPT = """Classify this browser page for a form-filling agent.
Return JSON only: {"kind":"expected"} with kind one of:
expected — this is the current stage, fill it
next_stage — the URL already moved to the next step
login — a sign-in wall
blocker — cookies, consent, locale, or a modal to dismiss
wrong — this is not the task
Do not plan the job. Do not invent fields.
"""


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def blocker_control(page):
    ranked = []
    for item in page.get("controls") or []:
        name = _norm(item.get("name"))
        if name in BLOCKER_NAMES or any(name.startswith(prefix) for prefix in BLOCKER_NAMES):
            ranked.append((0 if "reject" in name else 1, item))
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1] if ranked else None


def _form_like(page):
    roles = {(item.get("role") or "").lower() for item in page.get("controls") or []}
    names = " ".join(item.get("name") or "" for item in page.get("controls") or [])
    if "textbox" in roles or "combobox" in roles or "file" in roles:
        return True
    return bool(re.search(r"\b(email|name|phone|university|resume|cv)\b", names, re.I))


def _host(url):
    from urllib.parse import urlparse

    return urlparse(url or "").netloc.lower()


def classify_page_heuristic(run, page):
    from core.services.runner import looks_like_login, unexpected_page

    if looks_like_login(page):
        return "login"
    if blocker_control(page) and not (run.checkpoint or {}).get("dismissed_blocker"):
        blob = (page.get("text") or "")[:4000]
        name = _norm(blocker_control(page).get("name"))
        if BLOCKER_TEXT.search(blob) or name in {
            "accept all",
            "reject all",
            "accept cookies",
            "reject cookies",
        }:
            return "blocker"
    url = page.get("url") or run.current_url or ""
    off_script = bool((run.checkpoint or {}).get("off_script") or ((run.job or {}).get("procedure") or {}).get("off_script"))
    skill = run.skill if run.skill_id else None
    if skill and not off_script:
        stage = current_stage(run)
        if stage and url_matches_pattern(url, stage.url_pattern):
            return "expected"
        if stage and url_matches_pattern(url, stage.exit):
            return "next_stage"
        if unexpected_page(run, url):
            return "wrong"
        return "expected"
    start = ((run.job or {}).get("start_url") or run.current_url or "")
    if url and start and _host(url) and _host(start) and _host(url) != _host(start):
        if _form_like(page):
            return None
        return "wrong"
    if _form_like(page):
        return "expected"
    if blocker_control(page):
        return "blocker"
    return None


def _classify_haiku(run, page):
    """Unsure pages only. The name stays so tests can patch it. The call uses COMPILE_MODEL."""
    if not (settings.NEBIUS_API_KEY or settings.ANTHROPIC_API_KEY) or "test" in sys.argv:
        return None
    from core.services.compile import _client, _parse_json

    stage = current_stage(run)
    payload = {
        "goal": run.intent or "",
        "url": page.get("url") or run.current_url or "",
        "title": page.get("title") or run.tab_title or "",
        "expected_stage": (stage.name if stage else "") or (stage.page_kind if stage else ""),
        "controls": [item.get("name") or "" for item in (page.get("controls") or [])[:40]],
    }
    client = _client()
    response = client.messages.create(
        model=settings.COMPILE_MODEL,
        max_tokens=64,
        messages=[{"role": "user", "content": CLASSIFY_PROMPT + "\n" + json.dumps(payload)[:4000]}],
    )
    provider = getattr(response, "provider", "") or ""
    if provider:
        checkpoint = dict(run.checkpoint or {})
        checkpoint["provider"] = provider
        run.checkpoint = checkpoint
    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    kind = str((_parse_json(text) or {}).get("kind") or "").strip().lower()
    if kind in KINDS:
        return kind
    return None


def classify_page(run, page):
    kind = classify_page_heuristic(run, page)
    if kind:
        return kind, False
    guessed = None
    if int((run.checkpoint or {}).get("classify_calls") or 0) < 1:
        try:
            guessed = _classify_haiku(run, page)
        except Exception:
            guessed = None
    if guessed:
        checkpoint = dict(run.checkpoint or {})
        checkpoint["classify_calls"] = int(checkpoint.get("classify_calls") or 0) + 1
        run.checkpoint = checkpoint
        return guessed, True
    if _form_like(page):
        return "expected", False
    return "wrong", False
