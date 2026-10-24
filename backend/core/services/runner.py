import re
import uuid
from urllib.parse import urlparse

from core.models import Document
from core.services.memory import memory_for_run
from core.services.stages import advance_stage, current_stage, same_site, stages_for, url_matches_pattern

DANGEROUS_CLICK = re.compile(
    r"\b(submit|pay|purchase|buy now|place order|publish|send|delete account|confirm payment)\b",
    re.I,
)
LINE_RE = re.compile(
    r'^(?P<role>\S+)\s+"(?P<name>[^"]*)"\s+\[(?P<ref>[^\]]+)\](?:\s+value="(?P<value>[^"]*)")?'
)
LOGIN_HINT = re.compile(r"\b(sign in|log in|login|password|passcode)\b", re.I)
SKIP_FRAME = re.compile(
    r"recaptcha|hcaptcha|captcha|challenges\.cloudflare|about:blank|googletagmanager",
    re.I,
)


def _norm_label(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def is_skip_url(url):
    return bool(SKIP_FRAME.search(url or ""))


def parse_page(dump):
    text = dump if isinstance(dump, str) else ""
    lines = []
    url = ""
    title = ""
    skip = False
    for raw in text.splitlines():
        if raw.startswith("frame "):
            skip = is_skip_url(raw)
            continue
        if raw.startswith("url:"):
            candidate = raw.split(":", 1)[1].strip()
            if is_skip_url(candidate):
                skip = True
                continue
            skip = False
            if not url:
                url = candidate
            continue
        if skip:
            continue
        if raw.startswith("title:") and not title:
            title = raw.split(":", 1)[1].strip()
            continue
        match = LINE_RE.match(raw.strip())
        if match:
            name = match.group("name") or ""
            if is_skip_url(name) or re.search(r"recaptcha|i.?m not a robot", name, re.I):
                continue
            lines.append(match.groupdict())
    return {"url": url, "title": title, "controls": lines, "text": text}


def useful_page_dump(dump, limit=8000):
    if not dump:
        return ""
    header = []
    form_lines = []
    other = []
    skip = False
    for raw in str(dump).splitlines():
        if raw.startswith("frame "):
            skip = is_skip_url(raw)
            if not skip and not any(line.startswith("frame ") for line in header):
                header.append(raw)
            continue
        if raw.startswith("url:"):
            candidate = raw.split(":", 1)[1].strip()
            if is_skip_url(candidate):
                skip = True
                continue
            skip = False
            if not any(line.startswith("url:") for line in header):
                header.append(raw)
            continue
        if skip:
            continue
        if raw.startswith("title:") or raw.startswith("frame:"):
            if raw not in header:
                header.append(raw)
            continue
        match = LINE_RE.match(raw.strip())
        if not match:
            continue
        role = (match.group("role") or "").lower()
        name = match.group("name") or ""
        if "[offscreen]" in raw and role == "link":
            continue
        if role in ("textbox", "combobox", "checkbox", "radio", "file", "listbox"):
            form_lines.append(raw)
        elif role == "button" and re.search(
            r"select|submit|yes|no|available|continue|next|reject|accept",
            name,
            re.I,
        ):
            form_lines.append(raw)
        else:
            other.append(raw)
    return "\n".join(header + form_lines + other)[:limit]


def page_dump_from_results(tool_results):
    for result in tool_results or []:
        content = result.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text") or ""
                    if " [" in text and '"' in text:
                        return text
                extra = block.get("url") if isinstance(block, dict) else ""
                if extra:
                    continue
        elif isinstance(content, str) and ("textbox" in content or "url:" in content or "[" in content):
            if " [" in content or content.startswith("url:"):
                return content
    return ""


def url_from_results(tool_results, fallback=""):
    for result in tool_results or []:
        state = result.get("browser_state") or {}
        tabs = state.get("tabs") or []
        if tabs and tabs[0].get("url") and not is_skip_url(tabs[0].get("url")):
            return tabs[0]["url"]
        content = result.get("content")
        if isinstance(content, str) and content.startswith("url:"):
            parsed = parse_page(content)
            if parsed.get("url"):
                return parsed["url"]
    dump = page_dump_from_results(tool_results)
    if dump:
        parsed = parse_page(dump)
        if parsed.get("url"):
            return parsed["url"]
    return fallback


def looks_like_login(page):
    blob = (page.get("text") or "") + " " + " ".join(
        item.get("name") or "" for item in page.get("controls") or []
    )
    if LOGIN_HINT.search(blob):
        roles = {item.get("name", "").lower() for item in page.get("controls") or []}
        if any("password" in name for name in roles):
            return True
        if "sign in" in blob.lower() or "log in" in blob.lower() or "login" in blob.lower():
            return True
    return False


def is_dangerous_name(name):
    return bool(DANGEROUS_CLICK.search(name or ""))


def leftover_work(page_or_dump):
    page = page_or_dump if isinstance(page_or_dump, dict) else parse_page(page_or_dump or "")
    for item in page.get("controls") or []:
        name = (item.get("name") or "").strip()
        role = (item.get("role") or "").lower()
        value = (item.get("value") or "").strip()
        if is_dangerous_name(name):
            continue
        if re.search(r"select an option|select your |^select$|^choose$|^choose one$", name, re.I) and not value:
            return True
        if role in ("combobox", "listbox"):
            if not value or re.search(r"^choose|^select", value, re.I):
                return True
        if role == "button" and re.search(r"select", name, re.I) and not value:
            return True
        if role in ("textbox", "combobox", "file") and not name:
            return True
        if role == "file" and not value:
            return True
    return False


SKIP_FILL = re.compile(
    r"\b(why|essay|motivation|cover letter|password|card|cvv|captcha|signature)\b",
    re.I,
)
LABEL_KEYS = [
    (("school email", "university email", "college email", "student email", "primary college"), ("school_email", "email")),
    (("email confirmation", "confirm email", "re-enter email"), ("email", "school_email")),
    (("email",), ("email", "school_email")),
    (("legal first name", "first name", "given name", "forename"), ("first_name", "preferred_name")),
    (("legal last name", "last name", "surname", "family name"), ("last_name",)),
    (("preferred first name", "preferred name"), ("preferred_name", "first_name")),
    (("full legal name", "full name", "legal name"), ("full_name",)),
    (("address line 1", "address line1", "street address", "line 1"), ("shipping.line1",)),
    (("city", "town"), ("shipping.city", "city")),
    (("postcode", "post code", "zip"), ("shipping.postcode",)),
    (("phone", "mobile", "telephone", "cell"), ("phone",)),
    (("university", "college", "school", "institution"), ("university",)),
    (("degree", "qualification"), ("degree", "course")),
    (("major", "subject", "course", "programme", "program"), ("major", "course")),
    (("start year", "year of study", "graduation year", "graduating"), ("study.year", "study.start_year", "study.end_year")),
]


def _memory_value(memory, keys):
    by_key = {}
    for item in memory or []:
        key = item.get("key")
        if key and item.get("value") and key not in by_key:
            by_key[key] = item.get("value")
    if by_key.get("legal_name") and not by_key.get("full_name"):
        by_key["full_name"] = by_key["legal_name"]
    if by_key.get("year_of_study") and not by_key.get("study.year"):
        by_key["study.year"] = by_key["year_of_study"]
    for key in keys:
        if by_key.get(key):
            return by_key[key]
    full = by_key.get("full_name") or by_key.get("legal_name") or ""
    parts = [part for part in str(full).split() if part]
    if "first_name" in keys and parts:
        return parts[0]
    if "last_name" in keys and len(parts) > 1:
        return " ".join(parts[1:])
    if "email" in keys or "school_email" in keys:
        return by_key.get("email") or by_key.get("school_email") or ""
    return ""


def _keys_for_label(label):
    name = _norm_label(label)
    if not name or SKIP_FILL.search(name):
        return ()
    for needles, keys in LABEL_KEYS:
        if any(needle in name for needle in needles):
            return keys
    return ()


def job_vault_ids(run):
    ids = []
    for item in (run.job or {}).get("attachments") or []:
        doc_id = item.get("document_id")
        if doc_id and str(doc_id) not in ids:
            ids.append(str(doc_id))
    return ids


def local_fill_from_memory(run, page, memory):
    actions = []
    used = set()
    for control in page.get("controls") or []:
        role = (control.get("role") or "").lower()
        name = control.get("name") or ""
        if is_dangerous_name(name):
            continue
        if role == "file" or "resume" in _norm_label(name) or "cv" in _norm_label(name) or "transcript" in _norm_label(name):
            continue
        keys = _keys_for_label(name)
        if not keys:
            continue
        value = _memory_value(memory, keys)
        if not value:
            continue
        ref = control.get("ref")
        if not ref or ref in used:
            continue
        used.add(ref)
        actions.append(
            {
                "tool_use_id": _local_id("fill"),
                "name": "form_input",
                "input": {"target": ref, "value": value},
                "toolset_name": "browser",
            }
        )
    file_control = next(
        (
            item
            for item in page.get("controls") or []
            if (item.get("role") or "").lower() == "file"
            or "resume" in _norm_label(item.get("name"))
            or "cv" in _norm_label(item.get("name"))
            or "transcript" in _norm_label(item.get("name"))
        ),
        None,
    )
    for doc_id in job_vault_ids(run):
        if not file_control:
            break
        document = Document.objects.filter(id=doc_id).first()
        actions.append(
            {
                "tool_use_id": _local_id("vault"),
                "name": "attach_from_vault",
                "input": {
                    "document_id": str(doc_id),
                    "target": file_control["ref"],
                    "title": document.title if document else "",
                },
            }
        )
        break
    if actions and not _open_choice_control(page):
        control = _continue_control(page)
        ref = control.get("ref") if control else ""
        used_refs = {item.get("input", {}).get("target") for item in actions}
        if ref and ref not in used_refs:
            actions.append(
                {
                    "tool_use_id": _local_id("click"),
                    "name": "left_click",
                    "input": {"target": ref},
                    "toolset_name": "browser",
                }
            )
    return actions


def _open_choice_control(page):
    for item in page.get("controls") or []:
        role = (item.get("role") or "").lower()
        name = _norm_label(item.get("name"))
        if role in ("combobox", "listbox") or (role == "select"):
            return item
        if re.search(r"select an option|choose one|^select$|^choose$", name, re.I):
            return item
    return None


def _continue_control(page):
    for item in page.get("controls") or []:
        name = _norm_label(item.get("name"))
        if is_dangerous_name(name):
            continue
        if re.search(r"^(continue|next|save and continue)$", name, re.I):
            return item
    return None


def _continue_action(page):
    control = _continue_control(page)
    ref = control.get("ref") if control else ""
    if not ref:
        return None
    return {
        "tool_use_id": _local_id("click"),
        "name": "left_click",
        "input": {"target": ref},
        "toolset_name": "browser",
    }


def memory_for_stage(run, stage):
    skill = run.skill if run.skill_id else None
    labels = ""
    if stage:
        labels = " ".join([stage.name or "", stage.page_kind or ""] + list(stage.memory_keys or []))
    facts = memory_for_run(
        skill,
        current_url=run.current_url,
        tab_title=run.tab_title,
        goal=run.intent or (skill.goal if skill else ""),
        labels=labels,
        broad=bool((run.checkpoint or {}).get("off_script")),
    )
    extra = list((run.job or {}).get("memory") or [])
    seen = {(item.get("key"), item.get("value")) for item in facts}
    for item in extra:
        key = (item.get("key"), item.get("value"))
        if not item.get("key") or not item.get("value") or key in seen:
            continue
        seen.add(key)
        facts.append(item)
        if len(facts) >= 30:
            break
    if stage and stage.memory_keys and not (run.checkpoint or {}).get("off_script"):
        wanted = set(stage.memory_keys)
        sliced = [item for item in facts if item.get("key") in wanted]
        if sliced and len(sliced) >= max(2, (len(wanted) + 1) // 2):
            return sliced
    return facts


def _value_for_step(step, run, memory):
    source = step.get("source")
    key = step.get("key") or ""
    if source == "memory" or (source != "input" and key):
        for item in memory:
            if item.get("key") == key:
                return item.get("value") or ""
        keys = _keys_for_label(step.get("field") or "")
        return _memory_value(memory, keys) if keys else ""
    if source == "input" or key in (run.inputs or {}):
        return str((run.inputs or {}).get(key) or "")
    return str(step.get("demo_value") or "")


def _find_control(page, field):
    wanted = _norm_label(field)
    if not wanted:
        return None
    for item in page.get("controls") or []:
        if _norm_label(item.get("name")) == wanted:
            return item
    for item in page.get("controls") or []:
        name = _norm_label(item.get("name"))
        if wanted in name or name in wanted:
            return item
    return None


def match_stage_fields(stage, page):
    fills = [step for step in (stage.demonstration or []) if step.get("action") == "fill"]
    matched = []
    missing = []
    for step in fills:
        control = _find_control(page, step.get("field"))
        if control:
            matched.append((step, control))
        else:
            missing.append(step)
    return matched, missing


def _local_id(prefix):
    return "local_" + prefix + "_" + uuid.uuid4().hex[:12]


def vault_ids_for_stage(run, stage):
    ids = [str(item) for item in (stage.vault_file_ids or []) if item]
    extra = ((run.checkpoint or {}).get("stage_vault") or {}).get(str(stage.index)) or []
    for item in extra:
        text = str(item)
        if text and text not in ids:
            ids.append(text)
    return ids


_FILE_PICKER = re.compile(r"^upload( photos)?$", re.I)


def _file_picker(name):
    return bool(_FILE_PICKER.match((name or "").strip()))


def local_actions(run, stage, page, memory):
    actions = []
    matched, missing = match_stage_fields(stage, page)
    for step, control in matched:
        value = _value_for_step(step, run, memory)
        if not value:
            if step.get("source") == "input":
                continue
            missing.append(step)
            continue
        if (control.get("role") or "").lower() == "file" or _file_picker(step.get("field")) or _file_picker(control.get("name")):
            continue
        actions.append(
            {
                "tool_use_id": _local_id("fill"),
                "name": "form_input",
                "input": {"target": control["ref"], "value": value},
                "toolset_name": "browser",
            }
        )
    vault_ids = vault_ids_for_stage(run, stage)
    file_control = next(
        (
            item
            for item in page.get("controls") or []
            if item.get("role") == "file"
            or "file" in (item.get("name") or "").lower()
            or "transcript" in (item.get("name") or "").lower()
        ),
        None,
    )
    if vault_ids and not file_control:
        missing.append({"action": "attach", "field": "file"})
    gaps = [step for step in missing if step.get("action") != "fill" or _find_control(page, step.get("field"))]
    for doc_id in vault_ids:
        if not file_control:
            break
        document = Document.objects.filter(id=doc_id).first()
        actions.append(
            {
                "tool_use_id": _local_id("vault"),
                "name": "attach_from_vault",
                "input": {
                    "document_id": str(doc_id),
                    "target": file_control["ref"],
                    "title": document.title if document else "",
                },
            }
        )
    if gaps:
        return actions, gaps
    actions.extend(_next_demo_click(run, stage, page, actions))
    return actions, gaps


def demo_clicks_pending(run, stage):
    done = set((run.checkpoint or {}).get("demo_clicks") or [])
    for index, step in enumerate(stage.demonstration or []):
        if step.get("action") != "click":
            continue
        if str(index) + ":" + (step.get("field") or "") not in done:
            return True
    return False


def _next_demo_click(run, stage, page, actions):
    done = list((run.checkpoint or {}).get("demo_clicks") or [])
    done_set = set(done)
    waits = dict((run.checkpoint or {}).get("demo_click_waits") or {})
    seen_refs = {item["input"]["target"] for item in actions if item.get("input", {}).get("target")}
    for index, step in enumerate(stage.demonstration or []):
        if step.get("action") != "click":
            continue
        name = step.get("field") or ""
        token = str(index) + ":" + name
        if token in done_set:
            continue
        if is_dangerous_name(name) or re.search(r"select an option", name, re.I) or _file_picker(name):
            done.append(token)
            done_set.add(token)
            waits.pop(token, None)
            continue
        control = _find_control(page, name)
        blocked = (
            not control
            or is_dangerous_name(control.get("name"))
            or _file_picker(control.get("name"))
            or (control.get("role") or "").lower() == "file"
            or re.search(r"select an option", control.get("name") or "", re.I)
        )
        ref = control.get("ref") if control else ""
        if blocked or not ref or ref in seen_refs:
            count = int(waits.get(token) or 0) + 1
            if count < 2:
                waits[token] = count
                _save_checkpoint(run, demo_clicks=done, demo_click_waits=waits)
                return []
            done.append(token)
            done_set.add(token)
            waits.pop(token, None)
            continue
        done.append(token)
        waits.pop(token, None)
        _save_checkpoint(run, demo_clicks=done, demo_click_waits=waits)
        return [
            {
                "tool_use_id": _local_id("click"),
                "name": "left_click",
                "input": {"target": ref},
                "toolset_name": "browser",
            }
        ]
    _save_checkpoint(run, demo_clicks=done, demo_click_waits=waits)
    return []


def unexpected_page(run, url):
    stages = stages_for(run.skill if run.skill_id else None)
    if not stages:
        return False
    current = current_stage(run)
    if current and (url_matches_pattern(url, current.url_pattern) or url_matches_pattern(url, current.exit)):
        return False
    index = run.stage_index or 0
    for stage in stages:
        if stage.index >= index and url_matches_pattern(url, stage.url_pattern):
            return False
    skill = run.skill if run.skill_id else None
    if skill and same_site(url, skill.site_host or skill.start_url):
        return False
    return True


def _save_checkpoint(run, **fields):
    checkpoint = dict(run.checkpoint or {})
    checkpoint.update(fields)
    checkpoint["stage_index"] = run.stage_index or 0
    checkpoint["url"] = run.current_url
    run.checkpoint = checkpoint


def bump_model_calls(run):
    checkpoint = dict(run.checkpoint or {})
    checkpoint["model_calls"] = int(checkpoint.get("model_calls") or 0) + 1
    run.checkpoint = checkpoint


def model_call_count(run):
    return int((run.checkpoint or {}).get("model_calls") or 0)


def _pause(run, summary, kind="unexpected"):
    run.status = "paused"
    run.last_status_text = summary
    run.outcome = summary
    run.prompt = {"kind": kind, "summary": summary}
    run.pending_actions = []
    _save_checkpoint(run)
    run.save()
    return run.to_dict()


def _pending(run, actions, status_text):
    run.status = "running"
    run.pending_actions = actions
    run.last_status_text = status_text
    run.prompt = {}
    _save_checkpoint(run)
    run.save()
    return run.to_dict()


def _dismiss_blocker(run, page):
    from core.services.pagekind import blocker_control

    control = blocker_control(page)
    if not control:
        return None
    _save_checkpoint(run, dismissed_blocker=True)
    return _pending(
        run,
        [
            {
                "tool_use_id": _local_id("blocker"),
                "name": "left_click",
                "input": {"target": control["ref"]},
                "toolset_name": "browser",
            }
        ],
        "Dismissing a cookie or modal overlay.",
    )


def apply_stage_turn(run, tool_results=None):
    from core.services.listing import listing_turn
    from core.services.pagekind import classify_page

    listed = listing_turn(run, tool_results)
    if listed is not None:
        return listed
    dump = page_dump_from_results(tool_results)
    url = url_from_results(tool_results, run.current_url)
    if url:
        run.current_url = url
    advanced = advance_stage(run, run.current_url)
    stage = current_stage(run)
    off_script = bool((run.checkpoint or {}).get("off_script"))
    if dump:
        dump = useful_page_dump(dump)
        _save_checkpoint(run, last_dump=dump)
        page = parse_page(dump)
        if page.get("url"):
            run.current_url = page["url"]
            advanced = advance_stage(run, run.current_url) or advanced
            stage = current_stage(run)
        kind, _used_haiku = classify_page(run, page)
        if kind == "login":
            return _pause(
                run,
                "This looks like a sign-in page. Sign in on the tab, then Resume. Taskwipe does not keep the session.",
                kind="login",
            )
        if kind == "wrong":
            if off_script:
                return None
            expected = (stage.name if stage else "") or (stage.page_kind if stage else "") or "the next page"
            return _pause(run, "This page is not the next step. I expected " + expected + ".", kind="unexpected")
        if kind == "blocker":
            dismissed = _dismiss_blocker(run, page)
            if dismissed:
                return dismissed
            return None
        if kind == "next_stage":
            advance_stage(run, run.current_url)
            stage = current_stage(run)
            if off_script:
                return None
            return _pending(run, [_read_action(stage)], "Reading the next page.")
        memory = memory_for_stage(run, stage)
        if run.skill_id and not off_script and not (run.needs_plan and not run.plan_approved) and stage:
            actions, missing = local_actions(run, stage, page, memory)
            if missing:
                return None
            if actions:
                _save_checkpoint(run, filled_stage=stage.index)
                return _pending(run, actions, "Filling " + (stage.name or "this page") + " from the demonstration.")
            if demo_clicks_pending(run, stage):
                return _pending(run, [_read_action(stage)], "Opening the next menu.")
            fills = [step for step in (stage.demonstration or []) if step.get("action") == "fill"]
            if fills or vault_ids_for_stage(run, stage) or stage.index >= len(stages_for(run.skill)) - 1:
                return _finish_or_confirm(run, stage)
            return None
        page_url = page.get("url") or run.current_url or ""
        already = (run.checkpoint or {}).get("filled_url") == page_url
        actions = [] if already else local_fill_from_memory(run, page, memory)
        if actions:
            _save_checkpoint(run, filled_from_memory=True, filled_url=page_url)
            return _pending(run, actions, "Filling this page from stored details.")
        if leftover_work(page):
            return None
        nxt = _continue_action(page)
        if nxt:
            return _pending(run, [nxt], "Continuing to the next page.")
        return None
    if run.skill_id and not off_script and not (run.needs_plan and not run.plan_approved):
        if not stage:
            return None
        if unexpected_page(run, run.current_url) and not advanced:
            expected = stage.name or stage.page_kind or stage.url_pattern or "the next page"
            return _pause(run, "This page is not the next step. I expected " + expected + ".", kind="unexpected")
        if advanced:
            return _pending(run, [_read_action(stage)], "Reading " + (stage.name or "this page") + ".")
        leftover = leftover_work((run.checkpoint or {}).get("last_dump") or "")
        if stage and demo_clicks_pending(run, stage):
            return _pending(run, [_read_action(stage)], "Opening the next menu.")
        if (run.checkpoint or {}).get("filled_stage") == (run.stage_index or 0) and not leftover:
            return _finish_or_confirm(run, stage)
        if (run.checkpoint or {}).get("last_dump"):
            return None
        return _pending(run, [_read_action(stage)], "Reading " + (stage.name or "this page") + ".")
    if _awaiting_local_read(run):
        return _pending(run, [_read_action(stage)], "Reading this page.")
    return None


def _awaiting_local_read(run):
    job = run.job or {}
    blob = " ".join(
        [
            job.get("start_url") or "",
            run.current_url or "",
            ((job.get("procedure") or {}).get("start_url") or ""),
        ]
    )
    if "/demo/form-a/" not in blob:
        return False
    if (run.checkpoint or {}).get("last_dump"):
        return False
    host = (urlparse(run.current_url or job.get("start_url") or "").hostname or "").lower()
    return host in {"", "127.0.0.1", "localhost"}


def _absolute_url(current, target):
    target = str(target or "").strip()
    if not target:
        return ""
    if target.startswith("http://") or target.startswith("https://"):
        return target
    parsed = urlparse(current or "")
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = target if target.startswith("/") else "/" + target
    return parsed.scheme + "://" + parsed.netloc + path


def _read_action(stage):
    return {
        "tool_use_id": _local_id("read"),
        "name": "read_page",
        "input": {"filter": "interactive"},
        "toolset_name": "browser",
    }


def _keep_going(run, stage):
    index = stage.index if stage else 0
    exit_url = _absolute_url(run.current_url, stage.exit if stage else "")
    if exit_url and (run.checkpoint or {}).get("continued_stage") != index:
        _save_checkpoint(run, continued_stage=index)
        return _pending(
            run,
            [
                {
                    "tool_use_id": _local_id("nav"),
                    "name": "navigate",
                    "input": {"url": exit_url},
                    "toolset_name": "browser",
                }
            ],
            "Continuing to " + (stage.exit or "the next page") + ".",
        )
    return _pending(run, [_read_action(stage)], "Reading the next page.")


def _start_next_skill(run):
    queue = [str(item) for item in ((run.checkpoint or {}).get("skill_queue") or []) if item]
    if not queue:
        return None
    from core.models import Skill

    next_id = queue.pop(0)
    skill = Skill.objects.filter(id=next_id, ready=True).first()
    checkpoint = dict(run.checkpoint or {})
    checkpoint["skill_queue"] = queue
    if not skill:
        run.checkpoint = checkpoint
        run.save(update_fields=["checkpoint", "updated_at"])
        return _start_next_skill(run) if queue else None
    run.skill = skill
    run.stage_index = 0
    run.messages = []
    run.nudged = False
    run.needs_plan = False
    run.plan_approved = True
    checkpoint.pop("filled_stage", None)
    checkpoint.pop("continued_stage", None)
    checkpoint.pop("awaiting_submit", None)
    checkpoint.pop("after_actions_confirm", None)
    checkpoint.pop("after_actions_clicks", None)
    checkpoint["stage_index"] = 0
    run.checkpoint = checkpoint
    from core.services.job import bind_vault_to_run

    bind_vault_to_run(run)
    url = skill.start_url or ""
    label = skill.name or "the next skill"
    if not url:
        return _pending(run, [_read_action(None)], "Starting " + label + ".")
    return _pending(
        run,
        [
            {
                "tool_use_id": _local_id("nav"),
                "name": "navigate",
                "input": {"url": url},
                "toolset_name": "browser",
            }
        ],
        "Starting " + label + ".",
    )


def _finish_or_confirm(run, stage):
    stages = stages_for(run.skill if run.skill_id else None)
    last = not stages or (stage and stage.index >= len(stages) - 1)
    if not last:
        return _keep_going(run, stage)
    nxt = _start_next_skill(run)
    if nxt:
        return nxt
    summary = "This page is filled. Submit, Pay, Place order, and Publish stay blocked until you confirm."
    run.status = "awaiting_confirm"
    run.confirm_summary = summary
    run.last_status_text = summary
    run.prompt = {"kind": "confirm", "summary": summary}
    run.pending_actions = [
        {
            "tool_use_id": _local_id("confirm"),
            "name": "request_user_confirm",
            "input": {"summary": summary},
        }
    ]
    _save_checkpoint(run, filled_stage=stage.index if stage else 0, awaiting_submit=True)
    run.save()
    return run.to_dict()


def _scrub_extracted(text):
    if not isinstance(text, str):
        return text
    if "extracted_text" not in text and "document body" not in text.lower():
        return text
    return re.sub(r'"extracted_text"\s*:\s*".*?"', '"extracted_text": ""', text, flags=re.S)


def _strip_history_content(content):
    if isinstance(content, str):
        text = _scrub_extracted(content)
        if text.startswith("url:") or ("textbox" in text and "[" in text and len(text) > 400):
            return "Earlier page dump omitted."
        return text
    if not isinstance(content, list):
        return content
    cleaned = []
    for block in content:
        if not isinstance(block, dict):
            cleaned.append(block)
            continue
        if block.get("type") == "image":
            continue
        if block.get("type") == "tool_result":
            cleaned.append({**block, "content": _strip_history_content(block.get("content"))})
            continue
        if block.get("type") == "text":
            text = _strip_history_content(block.get("text") or "")
            cleaned.append({**block, "text": text})
            continue
        if block.get("source") and block["source"].get("type") == "base64":
            continue
        cleaned.append(block)
    return cleaned


def _scrub_current_content(content):
    if isinstance(content, str):
        return _scrub_extracted(content)
    if not isinstance(content, list):
        return content
    cleaned = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            cleaned.append({**block, "content": _scrub_current_content(block.get("content"))})
        elif isinstance(block, dict) and block.get("type") == "text":
            cleaned.append({**block, "text": _scrub_extracted(block.get("text") or "")})
        else:
            cleaned.append(block)
    return cleaned


def compact_messages(messages):
    if not messages:
        return messages
    last = len(messages) - 1
    compacted = []
    for index, message in enumerate(messages):
        content = message.get("content")
        if index == last:
            content = _scrub_current_content(content)
        else:
            content = _strip_history_content(content)
        compacted.append({**message, "content": content})
    return compacted


def payload_has_earlier_screenshot(messages):
    last = len(messages) - 1
    for index, message in enumerate(messages):
        if index == last:
            continue
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "image":
                return True
            source = block.get("source") if isinstance(block, dict) else None
            if isinstance(source, dict) and source.get("type") == "base64":
                return True
    return False


def payload_has_document_body(messages):
    blob = str(messages)
    return "extracted_text" in blob and re.search(r'"extracted_text"\s*:\s*"[^"]+', blob) is not None
