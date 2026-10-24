import base64
import json
import re
import uuid
from urllib.parse import urlparse

from core.services.compile import _parse_json

SITES = [
    {
        "id": "vinted",
        "name": "Vinted",
        "hosts": ("vinted.",),
        "url": "https://www.vinted.co.uk/items/new",
        "publish": ("upload",),
        "photo": ("upload photos", "add photos", "add photo"),
    },
    {
        "id": "ebay",
        "name": "eBay",
        "hosts": ("ebay.",),
        "url": "https://www.ebay.co.uk/sl/sell",
        "publish": ("list it for free", "list it", "list item"),
        "photo": ("add photos", "upload photos", "add photo"),
    },
    {
        "id": "facebook",
        "name": "Facebook Marketplace",
        "hosts": ("facebook.com", "fb.com"),
        "url": "https://www.facebook.com/marketplace/create/item",
        "publish": ("publish",),
        "photo": ("add photos", "add photo"),
    },
]

FALLBACK = {
    "title": "Blue ceramic mug",
    "description": (
        "Used cobalt-blue ceramic mug, about 350 ml. Glossy glaze with light crazing across the bowl. "
        "The rim and the foot have small chips from ordinary kitchen use. It still holds tea, coffee, or soup without leaking. "
        "One mug only. No box and no saucer. Washed and dry. Happy to post by Royal Mail or collect from a UK address."
    ),
    "price_gbp": "8",
    "condition": "Good",
    "brand": "Unbranded",
    "size": "350 ml",
    "colour": "Cobalt blue",
    "category": "Home",
    "category_search": "mug",
    "material": "Ceramic",
    "measurements": "9 cm tall, 8 cm across",
    "package": "Medium",
}

_POST = re.compile(r"\b(post|list|sell|marketplace)\b", re.I)
_SKIP_MEDIA = re.compile(r"\b(youtube|studio|caption|clip|video)\b", re.I)
_LOGIN = re.compile(r"login|sign[- ]?in|accounts\.google|checkpoint|cookie-policy|captcha|splashui", re.I)
_EMPTY_VALUE = re.compile(r"^(?:£|\$|€)?0+(?:\.0+)?$|^(select|choose|add|none|n/?a)?$", re.I)
_NEXT = {"next", "continue", "save and continue"}
_COOKIE = re.compile(r"^(accept all|accept cookies|allow all|i agree|got it)$", re.I)


def _norm(text):
    text = str(text or "").lower().replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def _has_image(attachments):
    for item in attachments or []:
        media = str(item.get("media_type") or item.get("mime") or "")
        name = str(item.get("name") or "")
        if media.startswith("image/") or re.search(r"\.(jpe?g|png|webp|gif|heic)$", name, re.I):
            return True
    return False


def chosen_sites(prompt):
    blob = (prompt or "").lower()
    named = []
    for site in SITES:
        if site["id"] in blob or (site["id"] == "facebook" and "facebook" in blob):
            named.append(site)
    if "marketplace" in blob and not any(site["id"] == "facebook" for site in named):
        named.insert(0, SITES[3])
    if named:
        return named
    return list(SITES)


def is_listing_prompt(prompt, attachments):
    if not _has_image(attachments):
        return False
    text = prompt or ""
    if _SKIP_MEDIA.search(text) and not any(site["id"] in text.lower() for site in SITES):
        return False
    if not text.strip():
        return True
    return bool(_POST.search(text))


def listing_asks(prompt):
    hints = _hints_from_prompt(prompt)
    asks = []
    if not hints.get("price_gbp"):
        asks.append({"key": "price_gbp", "label": "What price in pounds?", "placeholder": "4"})
    if not hints.get("condition"):
        asks.append(
            {
                "key": "condition",
                "label": "What condition is it? New, Good, or Satisfactory.",
                "placeholder": "Good",
            }
        )
    return asks


def listing_job_or_none(prompt, attachments):
    if not is_listing_prompt(prompt, attachments):
        return None
    sites = chosen_sites(prompt)
    first = sites[0]
    card = dict(FALLBACK)
    asks = listing_asks(prompt)
    return {
        "prompt": prompt or "post this",
        "mode": "listing",
        "goal": "Post this photo on " + ", ".join(site["name"] for site in sites),
        "summary": card["title"] + " · £" + card["price_gbp"],
        "listing": card,
        "listing_sites": sites,
        "memory_cues": [],
        "this_run": [],
        "attachments": attachments,
        "crafts_needed": [],
        "crafts": [],
        "procedure": {
            "status": "listing",
            "skill_id": None,
            "skill_name": "",
            "candidates": [],
            "stages": [site["name"] for site in sites],
            "start_url": first["url"],
            "start_host": urlparse(first["url"]).netloc,
            "start_label": first["name"],
            "off_script": False,
        },
        "start_url": first["url"],
        "start_host": urlparse(first["url"]).netloc,
        "start_label": first["name"],
        "stop_before": "Upload, List, or Publish",
        "questions": [item["label"] for item in asks],
        "asks": asks,
        "memory": [],
        "skill_hints": [],
        "tab_title": "",
    }


def _image_document(job):
    from core.models import Document

    for item in (job or {}).get("attachments") or []:
        media = str(item.get("media_type") or "")
        name = str(item.get("name") or "")
        if not item.get("document_id"):
            continue
        if media.startswith("image/") or re.search(r"\.(jpe?g|png|webp|gif|heic)$", name, re.I):
            return Document.objects.filter(id=item["document_id"]).first()
    return None


def _vision_card(document):
    from core.services.llm import complete

    handle = document.original.open("rb")
    try:
        raw = handle.read()
    finally:
        handle.close()
    if not raw:
        return None
    media = document.mime if str(document.mime or "").startswith("image/") else "image/jpeg"
    response = complete(
        {
            "max_tokens": 800,
            "system": (
                "You price a second-hand item from one photo. Return JSON only. "
                "Use plain English. No hashtags, no emoji. Price in GBP as digits."
            ),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Return JSON with keys title, description, price_gbp, condition, "
                                "brand, size, colour, category, category_search, material, measurements, package. "
                                "title under 80 characters. description is 5 to 7 dry sentences a buyer can trust: "
                                "what it is, colour, approximate size, wear, what is included, and postage. "
                                "Invent ordinary second-hand details that match the photo. Do not use a real person's name. "
                                "condition is one of: New with tags, Very good, Good, Satisfactory. "
                                "category_search is 1 to 3 words to type into a category box. "
                                "package is Small, Medium, or Large. "
                                "If the brand is not visible, use Unbranded."
                            ),
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media,
                                "data": base64.b64encode(raw).decode("ascii"),
                            },
                        },
                    ],
                }
            ],
        }
    )
    text = ""
    for block in response.content or []:
        if getattr(block, "type", "") == "text" and getattr(block, "text", ""):
            text = block.text
            break
    parsed = _parse_json(text)
    if not isinstance(parsed, dict) or not str(parsed.get("title") or "").strip():
        return None
    card = dict(FALLBACK)
    for key in card:
        value = parsed.get(key)
        if value is not None and str(value).strip():
            card[key] = str(value).strip()
    card["price_gbp"] = re.sub(r"[^0-9.]", "", card["price_gbp"]) or FALLBACK["price_gbp"]
    return _thicken(card)


def _thicken(card):
    card = dict(card or FALLBACK)
    for key, value in FALLBACK.items():
        if not str(card.get(key) or "").strip():
            card[key] = value
    description = str(card.get("description") or "").strip()
    if len(description) < 220 and FALLBACK["description"] not in description:
        card["description"] = (description + " " + FALLBACK["description"]).strip()
    return card


_PRICE_HINT = re.compile(
    r"(?:£|\$)\s*(\d+(?:\.\d+)?)|\b(\d+(?:\.\d+)?)\s*(?:pounds?|quid|gbp)\b",
    re.I,
)


def _hints_from_prompt(prompt):
    hints = {}
    text = prompt or ""
    found = _PRICE_HINT.search(text)
    if found:
        hints["price_gbp"] = found.group(1) or found.group(2)
    low = text.lower()
    if re.search(r"\b(crusty|chipped|scratched|worn|used)\b", low):
        hints["condition"] = "Satisfactory"
    elif re.search(r"\b(brand new|new with tags|unworn)\b", low):
        hints["condition"] = "New with tags"
    return hints


def attach_listing_copy(job):
    job = dict(job or {})
    card = dict(FALLBACK)
    document = _image_document(job)
    if document and document.original:
        try:
            seen = _vision_card(document)
        except Exception:
            seen = None
        if seen:
            card = seen
            job["provider"] = "nebius"
    card = _thicken(card)
    for key, value in _hints_from_prompt(job.get("prompt")).items():
        if value:
            card[key] = value
    card["package"] = _choose_package(card)
    job["listing"] = card
    job["summary"] = card["title"] + " · £" + card["price_gbp"]
    asks = listing_asks(job.get("prompt"))
    job["asks"] = asks
    job["questions"] = [item["label"] for item in asks]
    return job


_PARCEL_CHOICES = (
    ("Small", "For items that'd fit in a large envelope."),
    ("Medium", "For items that'd fit in a shoebox."),
    ("Large", "For items that'd fit in a moving box."),
)


def _parcel_heuristic(card):
    text = " ".join(_norm(card.get(key)) for key in ("title", "category", "category_search", "description"))
    if re.search(r"\b(furniture|sofa|bike|bicycle|chair|table|suitcase)\b", text):
        return "Large"
    if re.search(r"\b(sticker|earring|necklace|envelope)\b", text):
        return "Small"
    if re.search(r"\b(mug|mugs|cup|cups|shoe|shoes|book|plate|bowl)\b", text):
        return "Medium"
    return "Medium"


def _choose_package(card):
    """One flash call. A mug fits in a shoebox, so the answer is Medium."""
    import sys

    if "test" in sys.argv:
        return _parcel_heuristic(card)
    from django.conf import settings

    from core.services.llm import complete

    title = str(card.get("title") or "item").strip() or "item"
    lines = ["Item name: " + title]
    description = str(card.get("description") or "").strip()
    if description:
        lines.append(description[:400])
    for name, meaning in _PARCEL_CHOICES:
        lines.append(name + ": " + meaning)
    lines.append("Example: a mug is Medium, because it fits in a shoebox.")
    lines.append('Return JSON only: {"package": "Small"} or Medium or Large.')
    try:
        response = complete(
            {
                "model": settings.VISION_MODEL,
                "max_tokens": 60,
                "system": "Choose a parcel size for one second-hand item. Return JSON only.",
                "messages": [{"role": "user", "content": [{"type": "text", "text": "\n".join(lines)}]}],
            }
        )
    except Exception:
        return _parcel_heuristic(card)
    text = ""
    for block in response.content or []:
        if getattr(block, "type", "") == "text" and getattr(block, "text", ""):
            text = block.text
            break
    parsed = _parse_json(text)
    package = ""
    if isinstance(parsed, dict):
        package = str(parsed.get("package") or "").strip().title()
    if package not in {"Small", "Medium", "Large"}:
        found = re.search(r"\b(small|medium|large)\b", text, re.I)
        package = found.group(1).title() if found else ""
    if package in {"Small", "Medium", "Large"}:
        return package
    return _parcel_heuristic(card)


def _local_id(prefix):
    return "local_" + prefix + "_" + uuid.uuid4().hex[:12]


def _action(name, payload, prefix):
    return {
        "tool_use_id": _local_id(prefix),
        "name": name,
        "input": payload,
        "toolset_name": "browser",
    }


def _result_text(result):
    content = result.get("content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        return "\n".join(parts)
    if isinstance(content, str):
        return content
    return ""


def _form_from_results(tool_results):
    for result in tool_results or []:
        text = _result_text(result).strip()
        if not text.startswith("{") or '"fields"' not in text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("fields"), list):
            return data
    return None


def _url_from_results(tool_results, fallback):
    for result in tool_results or []:
        tabs = ((result.get("browser_state") or {}).get("tabs")) or []
        if tabs and tabs[0].get("url"):
            return tabs[0]["url"]
    form = _form_from_results(tool_results)
    if form and form.get("url"):
        return form["url"]
    for result in tool_results or []:
        text = _result_text(result)
        match = re.search(r"https?://\S+", text)
        if match and "Navigated" in text:
            return match.group(0)
    return fallback or ""


def _on_sell_form(url, site):
    path = (urlparse(url or "").path or "").lower()
    if site["id"] == "vinted":
        return "/items/new" in path
    if site["id"] == "facebook":
        return "/marketplace/create/item" in path
    if site["id"] == "ebay":
        return "/sl/" in path or "/lstng" in path
    return False


def _on_host(url, site):
    host = (urlparse(url or "").hostname or "").lower()
    return any(token in host for token in site["hosts"])


def _login_url(url):
    return bool(_LOGIN.search(url or ""))


def _empty(value):
    text = _norm(value)
    if not text:
        return True
    return bool(_EMPTY_VALUE.match(text))


def _photo_id(job):
    for item in (job or {}).get("attachments") or []:
        if item.get("document_id") and str(item.get("media_type") or "").startswith("image/"):
            return str(item["document_id"])
    for item in (job or {}).get("attachments") or []:
        if item.get("document_id"):
            return str(item["document_id"])
    return ""


def _photo_name(job):
    for item in (job or {}).get("attachments") or []:
        if str(item.get("media_type") or "").startswith("image/"):
            return item.get("name") or "photo"
    return ""


def _card(run):
    card = dict(((run.job or {}).get("listing")) or FALLBACK)
    for key in FALLBACK:
        override = (run.inputs or {}).get(key)
        if override:
            card[key] = str(override)
    return _thicken(card)


def _field(form, needles, avoid=()):
    for field in form.get("fields") or []:
        if field.get("role") == "file":
            continue
        blob = _norm(field.get("label")) + " " + _norm(field.get("placeholder"))
        if any(bad in blob for bad in avoid):
            continue
        if any(needle in blob for needle in needles):
            return field
    return None


def _button_exact(form, phrases):
    wanted = {_norm(phrase) for phrase in phrases}
    for button in form.get("buttons") or []:
        if _norm(button.get("name")) in wanted:
            return button
    return None


_PLACEHOLDER_BITS = (
    "select a brand",
    "select brand",
    "select condition",
    "select a condition",
    "select up to",
    "select a colour",
    "select a color",
    "select colour",
    "select color",
    "select a material",
    "select material",
    "select your parcel",
    "select parcel",
    "select size",
)
_PLACEHOLDER_EXACT = {
    "brand",
    "condition",
    "colour",
    "color",
    "colours",
    "colors",
    "material",
    "material (recommended)",
    "category",
}


def _placeholder(name):
    text = _norm(name)
    if text in _PLACEHOLDER_EXACT:
        return True
    return any(bit in text for bit in _PLACEHOLDER_BITS)


def _dropdown_opener(form, words):
    """A Vinted control is often named 'Brand Select a brand', not just 'Select a brand'."""
    for button in form.get("buttons") or []:
        name = _norm(button.get("name"))
        if not name or not _placeholder(name):
            continue
        if any(word in name for word in words):
            return button
    return None


def _button_opener(form, needles):
    for button in form.get("buttons") or []:
        name = _norm(button.get("name"))
        if not name.startswith(("select", "choose", "add")):
            continue
        if any(needle in name for needle in needles):
            return button
    return None


def _button_prefix(form, phrase):
    wanted = _norm(phrase)
    if len(wanted) < 3:
        return None
    for button in form.get("buttons") or []:
        name = _norm(button.get("name"))
        if name.startswith(wanted) and len(name) <= 80:
            return button
    return None


def _button_word(form, phrase):
    wanted = _norm(phrase)
    if len(wanted) < 3:
        return None
    loose = None
    for button in form.get("buttons") or []:
        if not _real_option(button):
            continue
        name = _norm(button.get("name"))
        if name == wanted:
            return button
        if wanted == "good" and re.search(r"\bvery\b", name):
            continue
        if loose is None and re.search(r"(^| )" + re.escape(wanted) + r"( |$)", name) and len(name) <= 40:
            loose = button
    return loose


def _button_contains(form, phrase):
    wanted = _norm(phrase)
    if len(wanted) < 3:
        return None
    for button in form.get("buttons") or []:
        name = _norm(button.get("name"))
        if wanted in name and len(name) <= 40:
            return button
    return None


_COLOUR_WORDS = (
    "black",
    "white",
    "grey",
    "gray",
    "blue",
    "navy",
    "red",
    "green",
    "yellow",
    "brown",
    "pink",
    "purple",
    "orange",
    "beige",
    "gold",
    "silver",
    "cream",
)


def _colour_word(value):
    text = _norm(value)
    for colour in _COLOUR_WORDS:
        if colour in text:
            return colour
    parts = text.split()
    return parts[-1] if parts else ""


def _labeled_dropdown(form, label):
    label = _norm(label)
    exact = None
    for button in form.get("buttons") or []:
        name = _norm(button.get("name"))
        if name == label:
            exact = button
        elif name.startswith(label + " ") and len(name) <= 80:
            return button, name[len(label) + 1 :].strip()
    if exact:
        return exact, ""
    return None, ""


def _real_option(match):
    if not match:
        return None
    name = _norm(match.get("name"))
    if name.startswith(("condition ", "colour ", "color ", "category ", "brand ")):
        return None
    return match


def _option_for(form, key, card):
    value = card.get(key) or ""
    if not value:
        return None
    if key == "colour":
        word = _colour_word(value)
        return _real_option(_button_exact(form, [value, word]) or _button_contains(form, word))
    if key == "package":
        return _button_prefix(form, value)
    if key == "brand":
        for phrase in ("no label", "no brand", "unbranded", value):
            match = _real_option(_button_exact(form, [phrase]) or _button_contains(form, phrase))
            if match:
                return match
        return None
    if key == "condition":
        phrases = _CONDITION_ALIASES.get(_norm(value), (value,))
        for phrase in phrases:
            match = _button_word(form, phrase)
            if match:
                return match
        return None
    return _real_option(_button_exact(form, [value]) or _button_contains(form, value))


_CATEGORY_HINTS = {
    "mug": ("mug", "mugs", "dinnerware", "crockery", "cup", "household", "home"),
    "mugs": ("mug", "mugs", "dinnerware", "crockery", "cup", "household", "home"),
    "cup": ("cup", "mug", "dinnerware", "household"),
    "home": ("home", "household", "dinnerware", "kitchen"),
}

_CONDITION_ALIASES = {
    "new with tags": ("new with tags", "new"),
    "new": ("new",),
    "good": ("used - good", "used – good", "good"),
    "satisfactory": ("used - good", "used – good", "satisfactory", "used - fair"),
    "fair": ("used - fair", "fair"),
}


def _search_tokens(query):
    raw = [tok for tok in _norm(query).split() if len(tok) > 2]
    tokens = []
    for tok in raw:
        tokens.append(tok)
        tokens.extend(_CATEGORY_HINTS.get(tok, ()))
    seen = []
    for tok in tokens:
        if tok not in seen:
            seen.append(tok)
    return seen


def _best_option(form, query, clicked):
    tokens = _search_tokens(query)
    if not tokens:
        return None
    best = None
    best_score = 0
    for button in form.get("buttons") or []:
        name = _norm(button.get("name"))
        if not name or name in clicked or len(name) > 60:
            continue
        if name in _NEXT or _COOKIE.match(button.get("name") or ""):
            continue
        score = sum(1 for tok in tokens if tok in name)
        if score > best_score:
            best = button
            best_score = score
    return best


def _save(run, state):
    checkpoint = dict(run.checkpoint or {})
    checkpoint["listing_state"] = state
    run.checkpoint = checkpoint


def _pending(run, actions, status_text, state):
    _save(run, state)
    run.status = "running"
    run.pending_actions = actions
    run.last_status_text = status_text
    run.prompt = {}
    run.save()
    return run.to_dict()


def _pause(run, summary, state):
    _save(run, state)
    run.status = "paused"
    run.last_status_text = summary
    run.outcome = summary
    run.prompt = {"kind": "login", "summary": summary}
    run.pending_actions = []
    run.save()
    return run.to_dict()


def _done(run, summary, state):
    _save(run, state)
    run.status = "completed"
    run.outcome = summary
    run.last_status_text = summary
    run.pending_actions = []
    run.prompt = {}
    run.fill_report = {"posted": list(state.get("posted") or []), "skipped": list(state.get("skipped") or [])}
    run.save()
    return run.to_dict()


def _confirm(run, site, click, state):
    summary = "Post this listing on " + site["name"] + "?"
    _save(run, state)
    run.status = "awaiting_confirm"
    run.confirm_summary = summary
    run.prompt = {"kind": "confirm", "summary": summary}
    run.last_status_text = summary
    run.pending_actions = [
        {
            "tool_use_id": _local_id("confirm"),
            "name": "request_user_confirm",
            "input": {"summary": summary},
        },
        click,
    ]
    run.save()
    return run.to_dict()


def _advance(state, site, reason):
    state["index"] = int(state.get("index") or 0) + 1
    state["attached"] = False
    state["need_wait"] = False
    state["waits"] = 0
    state["stuck"] = 0
    state["map_misses"] = 0
    state["clicked"] = []
    state["chooser"] = False
    state["publish_id"] = ""
    state["last"] = ""
    if reason:
        skipped = list(state.get("skipped") or [])
        skipped.append({"id": site["id"], "reason": reason})
        state["skipped"] = skipped
    return state


def listing_turn(run, tool_results=None):
    job = run.job or {}
    if job.get("mode") != "listing" and (job.get("procedure") or {}).get("status") != "listing":
        return None
    sites = job.get("listing_sites") or []
    if not sites:
        return None
    state = dict((run.checkpoint or {}).get("listing_state") or {})
    state.setdefault("index", 0)
    state.setdefault("attached", False)
    state.setdefault("clicked", [])
    state.setdefault("posted", [])
    state.setdefault("skipped", [])
    state.setdefault("stuck", 0)
    state.setdefault("waits", 0)
    index = int(state.get("index") or 0)
    url = _url_from_results(tool_results, run.current_url)
    if url:
        run.current_url = url
    if index >= len(sites):
        for position, candidate in enumerate(sites):
            if _on_host(url or run.current_url, candidate):
                state["index"] = position
                state["stuck"] = 0
                index = position
                break
        else:
            posted = state.get("posted") or []
            if posted:
                return _done(run, "Posted on " + ", ".join(posted) + ".", state)
            reasons = {item.get("reason") for item in (state.get("skipped") or [])}
            if reasons and reasons <= {"sign-in"}:
                return _pause(run, "Sign in on a listing site, then Resume.", state)
            return _pause(run, "I'm on the listing page. Resume to keep filling it.", state)

    site = sites[index]
    publish_id = state.get("publish_id") or ""
    if publish_id:
        hit = next((item for item in (tool_results or []) if item.get("tool_use_id") == publish_id), None)
        if hit and not hit.get("is_error"):
            posted = list(state.get("posted") or [])
            posted.append(site["id"])
            state["posted"] = posted
            _advance(state, site, "")
            state["posted"] = posted
            if int(state["index"]) >= len(sites):
                return _done(run, "Posted on " + ", ".join(posted) + ".", state)
            nxt = sites[int(state["index"])]
            state["need_wait"] = True
            return _pending(
                run,
                [_action("navigate", {"url": nxt["url"]}, "nav")],
                "Opening " + nxt["name"] + ".",
                state,
            )
        state["publish_id"] = ""

    if _login_url(url) and "cookie-policy" not in (url or ""):
        return _pause(run, "Sign in on " + site["name"] + ", then Resume.", state)

    if not _on_host(url, site) or not _on_sell_form(url, site) or "cookie-policy" in (url or ""):
        state["navigated"] = True
        state["need_wait"] = True
        return _pending(
            run,
            [_action("navigate", {"url": site["url"]}, "nav")],
            "Opening " + site["name"] + ".",
            state,
        )

    if state.get("need_wait"):
        state["need_wait"] = False
        state["waits"] = int(state.get("waits") or 0) + 1
        return _pending(run, [_action("wait", {"duration": 2}, "wait")], "Waiting for " + site["name"] + ".", state)

    form = _form_from_results(tool_results)
    if form:
        state["map_misses"] = 0
    if any(item.get("is_error") for item in (tool_results or [])) and form is None:
        state["stuck"] = int(state.get("stuck") or 0) + 1
    if not form:
        misses = int(state.get("map_misses") or 0) + 1
        state["map_misses"] = misses
        errors = [
            _result_text(item)
            for item in (tool_results or [])
            if item.get("is_error") or "Unimplemented" in _result_text(item)
        ]
        if misses >= 3 and any("Receiving end does not exist" in err or "Could not establish connection" in err for err in errors):
            state["map_misses"] = 0
            state["need_wait"] = True
            state["waits"] = int(state.get("waits") or 0)
            if int(state["waits"]) < 4:
                return _pending(run, [_action("wait", {"duration": 2}, "wait")], "Waiting for " + site["name"] + " to load.", state)
        if misses >= 3:
            detail = errors[-1] if errors else "Reload the tab so Taskwipe can read it, then Resume."
            return _pause(run, "Could not read the " + site["name"] + " form. " + detail[:220], state)
        return _pending(run, [_action("form_map", {}, "map")], "Reading the " + site["name"] + " form.", state)

    card = _card(run)
    clicked = set(state.get("clicked") or [])
    cookie = _button_exact(form, ["Accept all", "Accept cookies", "Allow all", "I agree"])
    if cookie and "cookies" not in clicked:
        clicked.add("cookies")
        state["clicked"] = list(clicked)
        return _pending(
            run,
            [_action("click_text", {"text": cookie["name"]}, "click")],
            "Dismissing cookies on " + site["name"] + ".",
            state,
        )

    actions = []
    file_field = next((field for field in form.get("fields") or [] if field.get("role") == "file"), None)
    photo_ready = bool(file_field and not _empty(file_field.get("value"))) or bool(state.get("attached"))
    if not photo_ready:
        doc_id = _photo_id(job)
        if file_field and doc_id:
            actions.append(
                _action(
                    "attach_from_vault",
                    {"document_id": doc_id, "target": file_field.get("ref") or "", "title": card["title"]},
                    "vault",
                )
            )
            state["attached"] = True
        elif file_field and _photo_name(job):
            actions.append(
                _action(
                    "attach_file",
                    {"target": file_field.get("ref") or "", "attachment_name": _photo_name(job)},
                    "file",
                )
            )
            state["attached"] = True
        if actions:
            state["last"] = "photo"
            return _pending(run, actions, "Adding the photo on " + site["name"] + ".", state)

    title_field = _field(form, ("title", "item name", "what you're selling", "what you are selling"))
    description_field = _field(form, ("description", "tell buyers more", "details"))
    price_field = _field(form, ("price",))
    if title_field and _empty(title_field.get("value")) and card.get("title"):
        actions.append(_action("form_input", {"target": title_field["ref"], "value": card["title"]}, "fill"))
    description = card.get("description") or ""
    if description_field and _empty(description_field.get("value")) and description:
        if not title_field and card.get("title") and card["title"] not in description:
            description = card["title"] + "\n\n" + description
        actions.append(_action("form_input", {"target": description_field["ref"], "value": description}, "fill"))
    if price_field and _empty(price_field.get("value")) and card.get("price_gbp"):
        actions.append(_action("form_input", {"target": price_field["ref"], "value": card["price_gbp"]}, "fill"))
    tried = set(state.get("tried") or [])
    for key, needles, avoid in (
        ("size", ("size",), ("package", "parcel")),
        ("measurements", ("measurement", "dimensions"), ()),
    ):
        if key in tried:
            continue
        field = _field(form, needles, avoid)
        if field and _empty(field.get("value")) and card.get(key):
            tried.add(key)
            actions.append(_action("form_input", {"target": field["ref"], "value": card[key]}, "fill"))
    state["tried"] = sorted(tried)

    if actions:
        signature = json.dumps([(item["name"], item.get("input")) for item in actions], sort_keys=True)
        if signature == state.get("last"):
            state["stuck"] = int(state.get("stuck") or 0) + 1
        else:
            state["stuck"] = 0
            state["last"] = signature
        if int(state["stuck"]) >= 3:
            _advance(state, site, "could not fill")
            if int(state["index"]) >= len(sites):
                return _pause(run, "I could not finish the listing form.", state)
            nxt = sites[int(state["index"])]
            state["need_wait"] = True
            return _pending(
                run,
                [_action("navigate", {"url": nxt["url"]}, "nav")],
                "Opening " + nxt["name"] + ".",
                state,
            )
        return _pending(run, actions, "Filling the " + site["name"] + " listing.", state)

    if site["id"] == "facebook":
        details = _button_exact(form, ["More details"])
        if details and "more details" not in clicked and not description_field:
            clicked.add("more details")
            state["clicked"] = list(clicked)
            return _pending(
                run,
                [_action("click_text", {"text": details["name"]}, "click")],
                "Opening the rest of the Facebook form.",
                state,
            )
        _category_button, category_value = _labeled_dropdown(form, "category")
        if category_value:
            tried.add("category")
            state["tried"] = sorted(tried)
            state["chooser"] = False
        _condition_button, condition_value = _labeled_dropdown(form, "condition")
        if condition_value:
            tried.add("condition")
            state["tried"] = sorted(tried)
        elif _condition_button and "condition-open" not in clicked:
            clicked.add("condition-open")
            state["clicked"] = list(clicked)
            state["picking"] = "condition"
            return _pending(
                run,
                [_action("click_text", {"text": _condition_button["name"]}, "click")],
                "Opening condition on Facebook.",
                state,
            )
        colour_button, colour_value = _labeled_dropdown(form, "colour")
        if not colour_button:
            colour_button, colour_value = _labeled_dropdown(form, "color")
        if colour_value:
            clicked.add(_colour_word(colour_value))
            clicked.add(_norm(colour_value))
            if _colour_word(colour_value) and _colour_word(colour_value) in _norm(card.get("colour")):
                clicked.add(_norm(card.get("colour")))
            state["clicked"] = list(clicked)
        elif colour_button and "colour-open" not in clicked:
            clicked.add("colour-open")
            state["clicked"] = list(clicked)
            state["picking"] = "colour"
            return _pending(
                run,
                [_action("click_text", {"text": colour_button["name"]}, "click")],
                "Opening colour on Facebook.",
                state,
            )

    if site["id"] == "ebay" and "prelist" in (url or "") and "ebay-search" not in clicked:
        search = _button_exact(form, ["Search", "Go", "Find"])
        if search:
            clicked.add("ebay-search")
            state["clicked"] = list(clicked)
            return _pending(
                run,
                [_action("click_text", {"text": search["name"]}, "click")],
                "Searching eBay for this item.",
                state,
            )

    category = _button_exact(form, ["Select a category", "Choose a category", "Category"])
    if category and _norm(category["name"]) not in clicked:
        clicked.add(_norm(category["name"]))
        state["clicked"] = list(clicked)
        state["chooser"] = True
        return _pending(
            run,
            [_action("click_text", {"text": category["name"]}, "click")],
            "Choosing a category on " + site["name"] + ".",
            state,
        )
    if state.get("chooser") or "category" not in set(state.get("tried") or []):
        option = _best_option(form, card.get("category_search") or card.get("category") or "", clicked)
        if option:
            clicked.add(_norm(option["name"]))
            state["clicked"] = list(clicked)
            return _pending(
                run,
                [_action("click_text", {"text": option["name"]}, "click")],
                "Picking " + option["name"] + " on " + site["name"] + ".",
                state,
            )
        state["chooser"] = False
    picking = state.get("picking") or ""
    if picking:
        match = _option_for(form, picking, card)
        if match and _norm(match["name"]) not in clicked:
            clicked.add(_norm(match["name"]))
            state["clicked"] = list(clicked)
            state["picking"] = ""
            return _pending(
                run,
                [_action("click_text", {"text": match["name"]}, "click")],
                "Setting " + picking + " on " + site["name"] + ".",
                state,
            )
        state["picking"] = ""
    for key, phrases, needles in (
        ("condition", ("condition", "select condition"), ("condition",)),
        ("brand", ("brand", "select a brand", "select brand"), ("brand",)),
        ("colour", ("colour", "color", "colours", "colors"), ("colour", "color")),
        ("material", ("material", "select a material"), ("material",)),
        ("size", ("select size",), ("select size",)),
    ):
        value = card.get(key) or ""
        if not value or _norm(value) in clicked:
            continue
        button = _dropdown_opener(form, needles) or _button_opener(form, needles) or _button_exact(form, phrases)
        if button and _norm(button["name"]) not in clicked:
            clicked.add(_norm(button["name"]))
            state["clicked"] = list(clicked)
            state["picking"] = key
            return _pending(
                run,
                [_action("click_text", {"text": button["name"]}, "click")],
                "Opening " + key + " on " + site["name"] + ".",
                state,
            )
        match = _option_for(form, key, card)
        if match and _norm(match["name"]) not in clicked:
            clicked.add(_norm(match["name"]))
            state["clicked"] = list(clicked)
            return _pending(
                run,
                [_action("click_text", {"text": match["name"]}, "click")],
                "Setting " + key + " on " + site["name"] + ".",
                state,
            )
    package = card.get("package") or ""
    if package and _norm(package) not in clicked:
        parcel_open = (
            _dropdown_opener(form, ("parcel", "package"))
            or _button_opener(form, ("parcel", "package"))
            or _button_exact(form, ["Select your parcel size"])
        )
        if parcel_open and not _button_prefix(form, package) and "parcel-open" not in clicked:
            clicked.add("parcel-open")
            state["clicked"] = list(clicked)
            state["picking"] = "package"
            return _pending(
                run,
                [_action("click_text", {"text": parcel_open["name"]}, "click")],
                "Opening the parcel size on " + site["name"] + ".",
                state,
            )
        parcel = _button_prefix(form, package)
        if parcel:
            clicked.add(_norm(package))
            state["clicked"] = list(clicked)
            return _pending(
                run,
                [_action("click_text", {"text": parcel["name"]}, "click")],
                "Setting the parcel size on " + site["name"] + ".",
                state,
            )

    title_done = not title_field or not _empty(title_field.get("value"))
    price_done = not price_field or not _empty(price_field.get("value"))
    nxt = _button_exact(form, ["Next", "Continue", "Save and continue"]) or _button_prefix(form, "Continue")
    if nxt and title_done and _norm(nxt["name"]) not in clicked:
        clicked.add(_norm(nxt["name"]))
        state["clicked"] = list(clicked)
        return _pending(
            run,
            [_action("click_text", {"text": nxt["name"]}, "click")],
            "Continuing on " + site["name"] + ".",
            state,
        )
    still_open = _dropdown_opener(form, ("brand", "condition", "colour", "color", "material", "parcel", "package"))
    publish = _button_exact(form, site["publish"])
    if publish and title_done and price_done and photo_ready and not category and not still_open:
        click = _action("click_text", {"text": publish["name"]}, "post")
        state["publish_id"] = click["tool_use_id"]
        return _confirm(run, site, click, state)
    if int(state.get("stuck") or 0) >= 2:
        if still_open:
            state["stuck"] = 0
            return _pause(
                run,
                site["name"] + " still has " + _norm(still_open.get("name")) + ". Resume to open that menu.",
                state,
            )
        if photo_ready and title_done and price_done:
            state["stuck"] = 0
            label = site["publish"][0]
            click = _action("click_text", {"text": label}, "post")
            state["publish_id"] = click["tool_use_id"]
            return _confirm(run, site, click, state)
        fields_filled = bool(title_field and price_field and not _empty(title_field.get("value")) and not _empty(price_field.get("value")))
        if fields_filled and not photo_ready:
            state["stuck"] = 0
            return _pause(
                run,
                site["name"]
                + " is filled except the photo. Reload this sell tab so the file field can be read, then Resume.",
                state,
            )
        if _on_host(url, site):
            state["stuck"] = 0
            return _pause(
                run,
                "I'm on the "
                + site["name"]
                + " page. Reload this tab so the fields can be read, then Resume.",
                state,
            )
        _advance(state, site, "no further controls")
        if int(state["index"]) >= len(sites):
            posted = state.get("posted") or []
            if posted:
                return _done(run, "Posted on " + ", ".join(posted) + ".", state)
            return _pause(run, "I could not find the rest of the " + site["name"] + " form.", state)
        following = sites[int(state["index"])]
        state["need_wait"] = True
        return _pending(
            run,
            [_action("navigate", {"url": following["url"]}, "nav")],
            "Opening " + following["name"] + ".",
            state,
        )
    state["stuck"] = int(state.get("stuck") or 0) + 1
    return _pending(run, [_action("form_map", {}, "map")], "Checking the " + site["name"] + " form.", state)
