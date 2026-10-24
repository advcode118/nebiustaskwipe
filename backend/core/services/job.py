import json
import re
import sys
from urllib.parse import urlparse

from django.conf import settings

from pathlib import Path

from core.models import CraftExample, Document, Skill
from core.services.attempt import _host_related, _tokens

_JOB_STOP = {
    "this",
    "that",
    "onto",
    "into",
    "from",
    "your",
    "you",
    "just",
    "please",
    "they",
    "then",
    "them",
    "why",
    "out",
    "plus",
    "full",
    "want",
    "have",
    "here",
    "with",
}
from core.services.compile import _parse_json
from core.services.memory import memory_for_run
from core.services.vault import attachments_from_vault, facts_from_vault

MEMORY_CUE_RE = re.compile(
    r"\b(suitable|usual|the usual|like last time|in my voice|my style|as before|how i usually|how i write)\b",
    re.I,
)
ACTION_RE = re.compile(
    r"\b(upload|fill|send|book|post|publish|order|submit|apply|create|schedule|email|compose|buy|pay|deal with)\b",
    re.I,
)
RULE_RE = re.compile(
    r"\b(never|always|don't|do not|should not|shouldn't|must not|from now on|my captions should)\b",
    re.I,
)
CRAFT_PATTERNS = [
    (re.compile(r"\b(captions?|descriptions?)\b", re.I), "caption"),
    (re.compile(r"\b(titles?|headlines?)\b", re.I), "title"),
    (re.compile(r"\b(community posts?|tweets?)\b", re.I), "post"),
]
START_HINTS = [
    (["youtube", "studio"], "https://studio.youtube.com", "studio.youtube.com", "YouTube Studio"),
    (["youtube", "upload"], "https://studio.youtube.com", "studio.youtube.com", "YouTube Studio"),
    (["youtube"], "https://studio.youtube.com", "studio.youtube.com", "YouTube Studio"),
    (["gmail", "mail"], "https://mail.google.com", "mail.google.com", "Gmail"),
    (["calendar"], "https://calendar.google.com", "calendar.google.com", "Google Calendar"),
    (["twitter", "tweet"], "https://x.com/compose/post", "x.com", "X compose"),
    (["linkedin"], "https://www.linkedin.com/feed/", "www.linkedin.com", "LinkedIn"),
]
CRAFT_PROMPT = """You write in the human's voice from their kept examples.

Return JSON only:
{
  "title": "short title if the job needs one, else empty",
  "text": "the caption / description / post",
  "notes": "one line on what you copied from their examples"
}

Match their length, whether they put the topic in the first line, hashtags, and tone.
Obey any rules. Use THIS RUN (file names, prompt) for facts that change.
If there are no examples, draft plainly and say so in notes.
"""


def _mentions(text, words):
    blob = (text or "").lower()
    return any(word in blob for word in words)


def _local_origin(url):
    parsed = urlparse(url or "")
    host = parsed.netloc
    if not host:
        return ""
    if host.startswith("127.0.0.1") or host.startswith("localhost"):
        return (parsed.scheme or "http") + "://" + host
    return ""


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def _prompt_url(text):
    match = _URL_RE.search(text or "")
    if not match:
        return ""
    return match.group(0).rstrip(".,);]")


def _is_application(text):
    return _mentions(text, ["apply", "application", "internship", "vacancy", "career", "cv", "resume", "job"])


def infer_start(goal, current_url=""):
    linked = _prompt_url(goal)
    if linked:
        parsed = urlparse(linked)
        return linked, parsed.netloc, parsed.netloc or "linked page"
    origin = _local_origin(current_url)
    apply_flow = _mentions(goal, ["apply", "internship", "transcript", "cv", "application"])
    if origin and apply_flow:
        return origin + "/demo/form-a/", urlparse(origin).netloc, "Imperial internship"
    if origin and _mentions(goal, ["youtube", "upload", "video", "caption", "studio"]):
        return origin + "/demo/studio/", urlparse(origin).netloc, "local creator studio"
    blob = (goal or "").lower()
    for words, url, host, label in START_HINTS:
        if all(word in blob for word in words) or (len(words) == 1 and words[0] in blob):
            return url, host, label
    parsed = urlparse(current_url or "")
    if parsed.netloc:
        return current_url, parsed.netloc, parsed.netloc
    return "", "", ""


def needed_crafts(prompt):
    kinds = []
    seen = set()
    for pattern, kind in CRAFT_PATTERNS:
        if pattern.search(prompt or "") and kind not in seen:
            kinds.append(kind)
            seen.add(kind)
    return kinds


def is_remember_only(prompt):
    text = (prompt or "").strip()
    if not text:
        return False
    if ACTION_RE.search(text) and not RULE_RE.search(text):
        return False
    if RULE_RE.search(text) and not ACTION_RE.search(text):
        return True
    if RULE_RE.search(text) and not _mentions(text, ["upload", "fill", "send", "publish"]):
        return True
    return False


def classify_attachment(item, prompt=""):
    name = str(item.get("name") or "file")
    media = str(item.get("media_type") or "")
    size = int(item.get("size") or 0)
    role = item.get("role") or ""
    if not role:
        if media.startswith("video/") or re.search(r"\.(mp4|mov|webm|mkv)$", name, re.I):
            role = "this_run"
        elif media.startswith("image/") or media.startswith("text/") or name.lower().endswith(".txt"):
            role = "craft_context" if needed_crafts(prompt) else "this_run"
        else:
            role = "this_run"
    if "injectable" in item:
        injectable = bool(item.get("injectable"))
    else:
        injectable = bool(item.get("data")) and size <= 8 * 1024 * 1024
    payload = {
        "name": name,
        "media_type": media,
        "size": size,
        "role": role,
        "injectable": injectable,
    }
    if item.get("document_id"):
        payload["document_id"] = str(item["document_id"])
        payload["title"] = item.get("title") or name
    elif item.get("text"):
        payload["text"] = item.get("text")
    return payload


def _is_local_host(host):
    host = (host or "").lower().split(":")[0]
    return host in {"127.0.0.1", "localhost"}


def _job_tokens(text):
    return {tok for tok in _tokens(text) if tok not in _JOB_STOP}


def match_skills_for_goal(goal, current_url=""):
    goal_tokens = _job_tokens(goal)
    _url, inferred_host, _label = infer_start(goal, current_url)
    matches = []
    for skill in Skill.objects.filter(ready=True):
        score = 0
        reasons = []
        kind_tokens = _job_tokens(" ".join([skill.name, skill.goal, skill.page_kind, skill.site_host]))
        overlap = goal_tokens & kind_tokens
        if overlap:
            score += min(5, len(overlap) * 2)
            reasons.append("matches " + (skill.name or skill.page_kind or "skill"))
        if inferred_host and skill.site_host:
            same = (
                skill.site_host == inferred_host
                or inferred_host in skill.site_host
                or skill.site_host in inferred_host
            )
            related = _host_related(skill.site_host, inferred_host)
            if same or related:
                if _is_local_host(inferred_host) or _is_local_host(skill.site_host):
                    if overlap:
                        score += 1
                        reasons.append("same local site")
                elif same:
                    score += 4
                    reasons.append("same site")
                else:
                    score += 2
                    reasons.append("related site")
        apply_flow = _mentions(goal, ["apply", "internship", "transcript", "cv", "application"])
        skill_apply = "form-a" in (skill.start_url or "") or _mentions(
            " ".join([skill.name, skill.goal, skill.page_kind]),
            ["internship", "apply", "application"],
        )
        if apply_flow and skill_apply:
            score += 4
            reasons.append("application flow")
        if not overlap and not (apply_flow and skill_apply):
            score = 0
        if not score:
            continue
        data = skill.to_dict()
        data["match_score"] = score
        data["match_reason"] = ", ".join(reasons) or "related"
        matches.append(data)
    matches.sort(key=lambda item: (-item["match_score"], item["name"]))
    return matches[:6]


_CLAUSE_SPLIT = re.compile(r"\b(?:and then|then|also|plus)\b|\band\b|,", re.I)


def _clauses(prompt):
    parts = [part.strip(" .") for part in _CLAUSE_SPLIT.split(prompt or "") if part.strip(" .")]
    return parts or [str(prompt or "").strip()]


def _skill_entry(skill_id, skill_name):
    skill = Skill.objects.filter(id=skill_id).first()
    stages = []
    start_url = ""
    if skill:
        start_url = skill.start_url or ""
        stages = [
            stage.name or stage.page_kind or ("Page " + str(stage.index + 1))
            for stage in skill.stages.order_by("index")
        ]
    return {
        "skill_id": str(skill_id),
        "skill_name": skill_name or (skill.name if skill else ""),
        "start_url": start_url,
        "stages": stages,
    }


def _sequence(chosen):
    skills = [_skill_entry(item["skill_id"], item["skill_name"]) for item in chosen]
    first = skills[0]
    return {
        "status": "sequence",
        "skill_id": first["skill_id"],
        "skill_name": first["skill_name"],
        "candidates": skills,
        "skills": skills,
        "start_url": first["start_url"],
        "start_host": "",
        "start_label": first["skill_name"],
        "stages": first["stages"],
    }


def _mix(matches):
    skills = [_skill_entry(item["id"], item["name"]) for item in matches]
    first = skills[0]
    names = [item["skill_name"] for item in skills if item.get("skill_name")]
    return {
        "status": "mix",
        "skill_id": None,
        "skill_name": " and ".join(names),
        "candidates": matches,
        "skills": skills,
        "start_url": first["start_url"],
        "start_host": "",
        "start_label": first["skill_name"],
        "stages": first["stages"],
    }


def pick_procedures(prompt, current_url=""):
    clauses = _clauses(prompt)
    chosen = []
    seen = set()
    mixed = None
    if len(clauses) > 1:
        for clause in clauses:
            picked = pick_procedure(match_skills_for_goal(clause, current_url))
            if picked["status"] == "known" and picked["skill_id"] not in seen:
                seen.add(picked["skill_id"])
                chosen.append(picked)
            elif picked["status"] == "mix" and mixed is None:
                mixed = picked
    if len(chosen) >= 2:
        return _sequence(chosen)
    whole = pick_procedure(match_skills_for_goal(prompt, current_url))
    if whole["status"] in {"known", "mix"} or mixed is None:
        return whole
    return mixed


def pick_procedure(matches):
    if not matches:
        return {"status": "learn", "skill_id": None, "skill_name": "", "candidates": []}
    top = matches[0]
    if len(matches) > 1 and matches[1]["match_score"] >= 3 and top["match_score"] - matches[1]["match_score"] <= 1:
        close = [
            item
            for item in matches
            if item["match_score"] >= 3 and top["match_score"] - item["match_score"] <= 1
        ]
        return _mix(close[:4])
    if top["match_score"] >= 3:
        return {
            "status": "known",
            "skill_id": top["id"],
            "skill_name": top["name"],
            "candidates": matches[:4],
        }
    return {"status": "learn", "skill_id": None, "skill_name": "", "candidates": matches[:4]}


def examples_for(kind, limit=8):
    return [item.to_dict() for item in CraftExample.objects.filter(kind=kind)[:limit]]


def rules_for_voice():
    return [item.to_dict() for item in CraftExample.objects.filter(kind="rule")[:20]]


def _heuristic_craft(kind, examples):
    if not examples:
        return {
            "kind": kind,
            "title": "",
            "text": "",
            "status": "draft",
            "source": "I'll draft one, nothing saved until you keep it",
            "notes": "",
        }
    latest = examples[0]
    text = latest.get("text") or ""
    title = ""
    first = text.split("\n", 1)[0].strip()
    if first and len(first) <= 80:
        title = first
    return {
        "kind": kind,
        "title": title,
        "text": text,
        "status": "from_examples",
        "source": "from your last " + kind + "s",
        "notes": latest.get("notes") or "",
    }


def _model_craft(kind, prompt, attachments, examples, rules):
    from core.services.compile import _client

    payload = {
        "kind": kind,
        "prompt": prompt,
        "attachments": [
            {"name": item.get("name"), "role": item.get("role")}
            for item in attachments
        ],
        "examples": examples,
        "rules": [item.get("text") for item in rules],
        "snippet_files": [
            {"name": item.get("name"), "text": item.get("text")}
            for item in attachments
            if item.get("text")
        ],
    }
    client = _client()
    response = client.messages.create(
        model=settings.COMPILE_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": CRAFT_PROMPT + "\n\n" + json.dumps(payload, indent=2)[:20000],
            }
        ],
    )
    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    parsed = _parse_json(text)
    status = "from_examples" if examples else "draft"
    source = "from your last " + kind + "s" if examples else "I'll draft one, nothing saved until you keep it"
    return {
        "kind": kind,
        "title": str(parsed.get("title") or "").strip(),
        "text": str(parsed.get("text") or "").strip(),
        "status": status,
        "source": source,
        "notes": str(parsed.get("notes") or "").strip(),
    }


def _allow_model():
    return bool(settings.NEBIUS_API_KEY or settings.ANTHROPIC_API_KEY) and "test" not in sys.argv


def draft_craft(kind, prompt, attachments, examples=None, rules=None):
    examples = examples if examples is not None else examples_for(kind)
    rules = rules if rules is not None else rules_for_voice()
    if _allow_model():
        try:
            return _model_craft(kind, prompt, attachments, examples, rules)
        except Exception:
            pass
    return _heuristic_craft(kind, examples)


def keep_craft(kind, text, notes="", title=""):
    text = str(text or "").strip()
    if not text:
        raise ValueError("Nothing to keep")
    kind = kind if kind in {item[0] for item in CraftExample.KIND_CHOICES} else "other"
    if title and not text.startswith(title):
        body = title.strip() + "\n\n" + text
    else:
        body = text
    example = CraftExample.objects.create(kind=kind, text=body, notes=str(notes or "").strip())
    return example.to_dict()


def remember_from_prompt(prompt, crafts=None):
    kinds = crafts or needed_crafts(prompt) or ["rule"]
    kind = "rule" if is_remember_only(prompt) else kinds[0]
    if kind != "rule" and is_remember_only(prompt):
        kind = "rule"
    example = CraftExample.objects.create(kind=kind, text=prompt.strip(), notes="from a prompt")
    return example.to_dict()


def _referenced_ids(procedure):
    ids = []
    for item in procedure.get("skills") or []:
        if item.get("skill_id"):
            ids.append(item.get("skill_id"))
    if procedure.get("skill_id"):
        ids.append(procedure.get("skill_id"))
    seen = set()
    ordered = []
    for skill_id in ids:
        key = str(skill_id)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(skill_id)
    return ordered


def _bare_page(url):
    path = (urlparse(url or "").path or "/").rstrip("/") or "/"
    return path in {"/", "/demo"}


def _same_site(skill, host):
    site = (skill.site_host or "").lower()
    host = (host or "").lower()
    if not site or not host:
        return False
    return site == host or site in host or host in site


def page_is_off_script(procedure, current_url):
    if procedure.get("status") not in {"known", "sequence", "mix"}:
        return False
    if not current_url or _bare_page(current_url):
        return False
    host = urlparse(current_url).netloc
    if not host:
        return False
    skills = [Skill.objects.filter(id=skill_id).first() for skill_id in _referenced_ids(procedure)]
    skills = [skill for skill in skills if skill]
    if not skills:
        return False
    return not any(_same_site(skill, host) for skill in skills)


def skill_hints_for(procedure):
    hints = []
    for skill_id in _referenced_ids(procedure):
        skill = Skill.objects.filter(id=skill_id).first()
        if not skill:
            continue
        hints.append(
            {
                "name": skill.name,
                "goal": skill.goal,
                "page_kind": skill.page_kind,
                "playbook": list(skill.playbook or [])[:12],
                "memory_keys": list(skill.memory_keys or [])[:20],
            }
        )
    return hints


def _memory_for_procedure(procedure, current_url, tab_title, goal):
    ids = []
    if procedure.get("status") in {"sequence", "mix"}:
        ids = [item.get("skill_id") for item in procedure.get("skills") or [] if item.get("skill_id")]
    elif procedure.get("skill_id"):
        ids = [procedure.get("skill_id")]
    if not ids:
        return memory_for_run(
            None,
            current_url=procedure.get("start_url") or current_url,
            tab_title=tab_title,
            goal=goal,
        )
    merged = []
    seen = set()
    for skill_id in ids:
        skill = Skill.objects.filter(id=skill_id).first()
        facts = memory_for_run(
            skill,
            current_url=procedure.get("start_url") or current_url,
            tab_title=tab_title,
            goal=goal,
        )
        for fact in facts:
            key = fact.get("id") or (fact.get("key"), fact.get("value"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(fact)
            if len(merged) >= 30:
                return merged
    return merged


def _merge_memory(base, extra):
    merged = []
    seen = set()
    for item in list(base or []) + list(extra or []):
        if not item.get("key") or not item.get("value"):
            continue
        fingerprint = (item.get("key"), str(item.get("value")).lower())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        merged.append(item)
        if len(merged) >= 30:
            break
    return merged


def vault_ids_named_in_prompt(prompt, vault_ids=None):
    ids = [str(item) for item in (vault_ids or []) if item]
    if ids:
        return ids
    blob = str(prompt or "").lower()
    wants_cv = bool(re.search(r"\b(cv|resume)\b", blob))
    wants_transcript = "transcript" in blob
    if not wants_cv and not wants_transcript:
        return []
    found = []
    for document in Document.objects.all():
        filename = Path(document.original.name).name if document.original else ""
        name = ((document.title or "") + " " + filename).lower()
        if wants_transcript and "transcript" in name:
            found.append(str(document.id))
        elif wants_cv and re.search(r"\b(cv|resume)\b", name):
            found.append(str(document.id))
    return found


def compile_job_heuristic(prompt, attachments=None, current_url="", tab_title="", vault_ids=None):
    prompt = str(prompt or "").strip()
    attachments = [classify_attachment(item, prompt) for item in (attachments or [])]
    attachments.extend(attachments_from_vault(vault_ids_named_in_prompt(prompt, vault_ids)))
    from core.services.listing import listing_job_or_none

    listed = listing_job_or_none(prompt, attachments)
    if listed:
        return listed
    if is_remember_only(prompt):
        return {
            "prompt": prompt,
            "mode": "remember",
            "goal": prompt,
            "memory_cues": [m.group(0) for m in MEMORY_CUE_RE.finditer(prompt)],
            "this_run": [],
            "attachments": attachments,
            "crafts": [],
            "procedure": {"status": "none", "skill_id": None, "skill_name": "", "candidates": []},
            "start_url": "",
            "start_host": "",
            "start_label": "",
            "stop_before": "",
            "questions": [],
        }
    goal = prompt or "Deal with the attached files"
    memory_cues = [m.group(0) for m in MEMORY_CUE_RE.finditer(prompt)]
    this_run = []
    for item in attachments:
        if item["role"] == "this_run":
            this_run.append(
                {
                    "name": re.sub(r"[^a-z0-9]+", "_", item["name"].rsplit(".", 1)[0].lower())[:40] or "file",
                    "label": item["name"],
                    "kind": "file",
                    "value": item["name"],
                }
            )
    unlisted = re.search(r"\b(unlisted|private|public)\b", prompt, re.I)
    if unlisted:
        this_run.append(
            {
                "name": "visibility",
                "label": "Visibility",
                "kind": "text",
                "value": unlisted.group(1).lower(),
            }
        )
    start_url, start_host, start_label = infer_start(goal, current_url)
    procedure = pick_procedures(goal, current_url)
    procedure["start_url"] = procedure.get("start_url") or start_url
    procedure["start_host"] = procedure.get("start_host") or start_host
    procedure["start_label"] = procedure.get("start_label") or start_label
    linked = _prompt_url(goal)
    off_script = page_is_off_script(procedure, linked or current_url)
    procedure["off_script"] = off_script
    if linked:
        procedure["start_url"] = linked
        procedure["start_host"] = urlparse(linked).netloc
        procedure["start_label"] = procedure["start_host"] or start_label
    elif off_script:
        procedure["start_url"] = current_url
        procedure["start_host"] = urlparse(current_url).netloc
        procedure["start_label"] = tab_title or procedure["start_host"]
    elif procedure["status"] == "mix":
        first = (procedure.get("skills") or [{}])[0]
        if first.get("start_url"):
            procedure["start_url"] = first["start_url"]
        skill = Skill.objects.filter(id=first.get("skill_id")).first() if first.get("skill_id") else None
        if skill:
            procedure["start_host"] = skill.site_host or start_host
            procedure["start_label"] = skill.name
            procedure["stages"] = [
                stage.name or stage.page_kind or ("Page " + str(stage.index + 1))
                for stage in skill.stages.order_by("index")
            ]
    elif procedure["status"] in {"known", "sequence"} and procedure["skill_id"]:
        skill = Skill.objects.filter(id=procedure["skill_id"]).first()
        if skill and skill.start_url:
            procedure["start_url"] = skill.start_url
            procedure["start_host"] = skill.site_host or start_host
            procedure["start_label"] = skill.name
        if skill:
            procedure["stages"] = [
                stage.name or stage.page_kind or ("Page " + str(stage.index + 1))
                for stage in skill.stages.order_by("index")
            ]
    if procedure["status"] == "learn" and "/demo/form-a/" in (procedure.get("start_url") or ""):
        procedure["stages"] = [
            "Personal details",
            "Education history",
            "Transcript upload",
            "Review",
        ]
    crafts_needed = needed_crafts(prompt)
    if re.search(r"stop before submit", goal, re.I):
        stop_before = "Submit"
    elif _mentions(goal, ["youtube", "upload", "video", "studio"]) and not _mentions(
        goal, ["apply", "internship", "transcript", "cv"]
    ):
        stop_before = "Publish"
    else:
        stop_before = "Submit/Pay"
    questions = []
    if any(item["role"] == "this_run" and item["size"] > 8 * 1024 * 1024 for item in attachments):
        questions.append("Large files stay on this machine. I'll ask you to pick them when the page opens.")
    return {
        "prompt": prompt,
        "mode": "run",
        "goal": goal,
        "memory_cues": memory_cues,
        "this_run": this_run,
        "attachments": attachments,
        "crafts_needed": crafts_needed,
        "crafts": [],
        "procedure": procedure,
        "start_url": procedure.get("start_url") or start_url,
        "start_host": procedure.get("start_host") or start_host,
        "start_label": procedure.get("start_label") or start_label,
        "stop_before": stop_before,
        "questions": questions,
        "tab_title": tab_title or "",
        "memory": _merge_memory(
            (
                memory_for_run(
                    None,
                    current_url=procedure.get("start_url") or current_url,
                    tab_title=tab_title,
                    goal=goal,
                    broad=True,
                )
                if procedure.get("off_script") and _is_application(goal + " " + (procedure.get("start_url") or ""))
                else _memory_for_procedure(procedure, current_url, tab_title, goal)
            ),
            facts_from_vault(
                [item.get("document_id") for item in attachments if item.get("document_id")],
                texts=[item.get("text") for item in attachments if item.get("text")],
            ),
        ),
        "skill_hints": skill_hints_for(procedure),
    }


def refresh_job_memory(job):
    job = dict(job or {})
    attachments = job.get("attachments") or []
    job["memory"] = _merge_memory(
        job.get("memory") or [],
        facts_from_vault(
            [item.get("document_id") for item in attachments if item.get("document_id")],
            texts=[item.get("text") for item in attachments if item.get("text")],
        ),
    )
    return job


def fill_crafts(job):
    crafts = []
    attachments = job.get("attachments") or []
    prompt = job.get("prompt") or ""
    for kind in job.get("crafts_needed") or []:
        crafts.append(draft_craft(kind, prompt, attachments))
    job["crafts"] = crafts
    return job


_RESEARCH_RE = re.compile(
    r"\b(what does|what do|requirements?|ask for|asks for|look up|find out|which of my|actually ask)\b",
    re.I,
)
_CARD_PROMPT = """You turn public page text into a Taskwipe job card.

Return JSON only:
{
  "goal": "one sentence of what the human asked",
  "summary": "what the public pages actually require, in plain language",
  "covered": [{"fact": "label of a saved fact", "covers": "which requirement it covers"}]
}

Use only the sources and the saved facts. Do not invent a requirement or a fact.
If a requirement is not covered, leave it out of covered. Never emit an empty fact.
When the sources describe several programmes, use the official Imperial College London page and ignore unrelated or third-party programmes.
Say what an applicant has to provide, then which saved facts already cover that.
"""


def task_needs_public_page(prompt):
    text = prompt or ""
    if not _RESEARCH_RE.search(text):
        return False
    if re.search(r"\bapply with\b|\bstop before submit\b", text, re.I):
        return False
    return True


def _draft_public_card(job):
    from core.services.llm import complete

    facts = [
        {"label": item.get("label") or item.get("key") or "", "value": item.get("value") or ""}
        for item in (job.get("memory") or [])[:30]
        if item.get("value")
    ]
    sources = [
        {"url": item.get("url") or "", "title": item.get("title") or "", "excerpt": item.get("excerpt") or ""}
        for item in (job.get("sources") or [])[:3]
    ]
    payload = {
        "prompt": job.get("prompt") or "",
        "facts": facts,
        "sources": sources,
    }
    response = complete(
        {
            "model": settings.COMPILE_MODEL,
            "max_tokens": 8192,
            "messages": [
                {
                    "role": "user",
                    "content": _CARD_PROMPT + "\n\n" + json.dumps(payload)[:20000],
                }
            ],
        }
    )
    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    provider = getattr(response, "provider", "") or ""
    try:
        parsed = _parse_json(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return {"goal": "", "summary": text.strip(), "covered": [], "provider": provider}
    return {
        "goal": str(parsed.get("goal") or "").strip(),
        "summary": str(parsed.get("summary") or "").strip(),
        "covered": parsed.get("covered") if isinstance(parsed.get("covered"), list) else [],
        "provider": provider,
    }


def enrich_public_job(job, current_url=""):
    from core.services.web import is_public_http, sources_for_prompt

    job = dict(job)
    try:
        packed = sources_for_prompt(job.get("prompt") or "", current_url)
    except Exception:
        questions = list(job.get("questions") or [])
        questions.append("Public lookup failed. I will read the page in the browser.")
        job["questions"] = questions
        job["sources"] = []
        return job
    job["sources"] = packed.get("sources") or []
    job["tavily_searched"] = bool(packed.get("searched"))
    if job["sources"]:
        first = job["sources"][0].get("url") or ""
        if first and not is_public_http(job.get("start_url")):
            procedure = dict(job.get("procedure") or {})
            procedure["status"] = "learn"
            procedure["skill_id"] = None
            procedure["skill_name"] = ""
            procedure["start_url"] = first
            procedure["off_script"] = True
            procedure["stages"] = []
            job["procedure"] = procedure
            job["start_url"] = first
            job["start_host"] = urlparse(first).netloc
            job["start_label"] = job["start_host"]
            job["stop_before"] = ""
    if not job["sources"]:
        return job
    try:
        card = _draft_public_card(job)
    except Exception:
        return job
    if card.get("goal"):
        job["goal"] = card["goal"]
    if card.get("summary"):
        job["summary"] = card["summary"]
    covered = [
        item
        for item in (card.get("covered") or [])
        if isinstance(item, dict) and str(item.get("fact") or "").strip() and str(item.get("covers") or "").strip()
    ]
    if covered:
        job["covered"] = covered
    if card.get("provider"):
        job["provider"] = card["provider"]
    return job


def compile_job(prompt, attachments=None, current_url="", tab_title="", vault_ids=None):
    job = compile_job_heuristic(prompt, attachments, current_url, tab_title, vault_ids=vault_ids)
    if job.get("mode") == "listing":
        from core.services.listing import attach_listing_copy

        return attach_listing_copy(job)
    if job["mode"] == "run" and task_needs_public_page(job.get("prompt") or prompt or ""):
        return enrich_public_job(job, current_url)
    if job["mode"] == "run":
        fill_crafts(job)
    return job


def job_inputs(job, extra=None):
    inputs = {}
    for item in job.get("this_run") or []:
        if item.get("name"):
            inputs[item["name"]] = item.get("value") or ""
    for craft in job.get("crafts") or []:
        kind = craft.get("kind") or "caption"
        if craft.get("text"):
            inputs[kind] = craft["text"]
        if craft.get("title"):
            inputs[kind + "_title"] = craft["title"]
            inputs.setdefault("title", craft["title"])
        if kind == "caption" and craft.get("text"):
            inputs.setdefault("description", craft["text"])
    if extra:
        inputs.update(extra)
    return inputs


def apply_job_picks(job, body):
    job = dict(job or {})
    procedure = dict(job.get("procedure") or {})
    skill_id = body.get("skill_id")
    sequence_ids = [str(item.get("skill_id")) for item in (procedure.get("skills") or []) if item.get("skill_id")]
    if procedure.get("status") in {"sequence", "mix"} and (not skill_id or str(skill_id) in sequence_ids):
        skill_id = None
    if skill_id:
        skill = Skill.objects.filter(id=skill_id, ready=True).first()
        if not skill:
            raise ValueError("That skill is not ready")
        procedure["status"] = "known"
        procedure["skill_id"] = str(skill.id)
        procedure["skill_name"] = skill.name
        if skill.start_url:
            procedure["start_url"] = skill.start_url
            procedure["start_host"] = skill.site_host
            procedure["start_label"] = skill.name
        procedure["stages"] = [
            stage.name or stage.page_kind or ("Page " + str(stage.index + 1))
            for stage in skill.stages.order_by("index")
        ]
        job["start_url"] = procedure.get("start_url") or job.get("start_url") or ""
        job["start_host"] = procedure.get("start_host") or job.get("start_host") or ""
        job["start_label"] = procedure.get("start_label") or skill.name
    crafts = body.get("crafts")
    if isinstance(crafts, list) and crafts:
        job["crafts"] = crafts
    job["procedure"] = procedure
    return job


def arm_skill_queue(run):
    procedure = (run.job or {}).get("procedure") or {}
    if procedure.get("status") != "sequence":
        return run
    ids = [str(item.get("skill_id")) for item in (procedure.get("skills") or []) if item.get("skill_id")]
    current = str(run.skill_id) if run.skill_id else ""
    if current in ids:
        rest = ids[ids.index(current) + 1 :]
    else:
        rest = ids[1:]
    checkpoint = dict(run.checkpoint or {})
    if rest:
        checkpoint["skill_queue"] = rest
    run.checkpoint = checkpoint
    return run


def bind_vault_to_run(run):
    if not run.skill_id:
        return run
    stages = list(run.skill.stages.order_by("index"))
    upload = None
    for stage in stages:
        blob = " ".join([stage.name or "", stage.page_kind or "", stage.url_pattern or ""])
        if re.search(r"upload|transcript|file", blob, re.I):
            upload = stage
            break
    transcript_ids = []
    cv_ids = []
    other_ids = []
    for item in (run.job or {}).get("attachments") or []:
        doc_id = item.get("document_id")
        if not doc_id:
            continue
        blob = (item.get("name") or "") + " " + (item.get("title") or "")
        if re.search(r"transcript", blob, re.I):
            transcript_ids.append(str(doc_id))
        elif re.search(r"\b(cv|resume|curriculum)\b", blob, re.I):
            cv_ids.append(str(doc_id))
        else:
            other_ids.append(str(doc_id))
    ids = transcript_ids or [item for item in other_ids if item not in cv_ids] or other_ids
    mapping = {}
    if upload and ids:
        mapping[str(upload.index)] = ids
    checkpoint = dict(run.checkpoint or {})
    checkpoint["stage_vault"] = mapping
    checkpoint["cv_ids"] = cv_ids
    run.checkpoint = checkpoint
    return run
