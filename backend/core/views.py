from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from core.http import api_view, error, json_body, ok
from core.models import CraftExample, Document, MemoryFact, Recording, Run, Skill, Stage
from core.services.agent import (
    host_mismatch,
    resolve_confirm,
    resolve_file_pick,
    resolve_plan,
    resolve_value,
    resume_run,
    turn,
)
from core.services.attempt import DEFAULT_INTENT, keep_from_run, match_skills
from core.services.commit import commit_skill
from core.services.compile import compile_recording
from core.services.activity import activity_dict, stamp_activity
from core.services.export import build_export_zip
from core.services.job import (
    apply_job_picks,
    arm_skill_queue,
    bind_vault_to_run,
    compile_job,
    job_inputs,
    keep_craft,
    refresh_job_memory,
    remember_from_prompt,
)
from core.services.memory import fact_for_key, is_forbidden, set_as_default
from core.services.stages import ensure_one_stage, next_stage_index, sync_skill_from_stage
from core.services.vault import accept_proposals, delete_document, extract_and_propose, reject_proposals


@api_view(["GET"])
def health(request):
    return ok(
        {
            "ok": True,
            "has_api_key": bool(settings.NEBIUS_API_KEY or settings.ANTHROPIC_API_KEY),
            "has_nebius_key": bool(settings.NEBIUS_API_KEY),
            "has_tavily_key": bool(settings.TAVILY_API_KEY),
            "has_anthropic_key": bool(settings.ANTHROPIC_API_KEY),
            "compile_model": settings.COMPILE_MODEL,
            "run_model": settings.RUN_MODEL,
            "vision_model": settings.VISION_MODEL,
        }
    )


@api_view(["GET", "POST"])
def skills_collection(request):
    if request.method == "GET":
        return ok({"skills": [skill.to_dict() for skill in Skill.objects.all()]})
    body = json_body(request)
    skill = Skill.objects.create(
        name=(body.get("name") or "Untitled skill")[:200],
        goal=body.get("goal") or "",
        playbook=body.get("playbook") or [],
        inputs_schema=body.get("inputs_schema") or [],
        memory_keys=body.get("memory_keys") or [],
        start_url=body.get("start_url") or "",
        site_host=body.get("site_host") or "",
        page_kind=body.get("page_kind") or "",
        demonstration=body.get("demonstration") or [],
    )
    ensure_one_stage(skill)
    return ok({"skill": skill.to_dict()}, 201)


@api_view(["GET", "DELETE"])
def skill_detail(request, skill_id):
    skill = get_object_or_404(Skill, id=skill_id)
    if request.method == "DELETE":
        skill.delete()
        return ok({"deleted": True})
    return ok({"skill": skill.to_dict()})


@api_view(["POST"])
def skill_commit(request, skill_id):
    skill = get_object_or_404(Skill, id=skill_id)
    body = json_body(request)
    result = commit_skill(skill, body)
    return ok(result)


@api_view(["GET"])
def skills_match(request):
    url = request.GET.get("url") or ""
    title = request.GET.get("title") or ""
    return ok({"skills": match_skills(url, title)})


def _apply_fact_meta(fact, body):
    if "label" in body and body["label"]:
        fact.label = body["label"]
    if "category" in body and body["category"] in MemoryFact.CATEGORIES:
        fact.category = body["category"]
    if "sensitivity" in body and body["sensitivity"] in MemoryFact.SENSITIVITIES:
        fact.sensitivity = body["sensitivity"]
    if "source" in body and body["source"] in MemoryFact.SOURCES:
        fact.source = body["source"]
    if "source_quote" in body:
        fact.source_quote = str(body.get("source_quote") or "")
    if "group_key" in body:
        fact.group_key = str(body.get("group_key") or "")[:80]
    if "confirmed" in body:
        fact.confirmed = bool(body["confirmed"])
    if "source_document_id" in body:
        doc_id = body.get("source_document_id")
        fact.source_document = Document.objects.filter(id=doc_id).first() if doc_id else None


@api_view(["GET", "POST"])
def memory_collection(request):
    if request.method == "GET":
        return ok({"facts": [fact.to_dict() for fact in MemoryFact.objects.all()]})
    body = json_body(request)
    key = (body.get("key") or "").strip()
    value = (body.get("value") or "").strip()
    if not key or not value:
        return error("key and value are required")
    if is_forbidden(key, value):
        return error("That fact looks like a secret and will not be stored")
    existing = fact_for_key(key)
    add_another = bool(body.get("add_another"))
    if existing and not add_another:
        same = existing.value.strip() == value or (
            key == "email" and existing.value.strip().lower() == value.lower()
        )
        if not same and not body.get("overwrite"):
            return error(
                "Already remember "
                + existing.value
                + " for "
                + key
                + ". Tick overwrite if you meant to replace it."
            )
        existing.value = value
        existing.confirmed = body.get("confirmed", True)
        _apply_fact_meta(existing, body)
        existing.save()
        if body.get("is_default"):
            set_as_default(existing)
        return ok({"fact": existing.to_dict()})
    category = body.get("category") or "other"
    if category not in MemoryFact.CATEGORIES:
        category = "other"
    fact = MemoryFact(
        key=key,
        label=body.get("label") or key,
        value=value,
        category=category,
        confirmed=body.get("confirmed", True),
        is_default=False if existing else True,
        source=body.get("source") if body.get("source") in MemoryFact.SOURCES else "typed",
    )
    _apply_fact_meta(fact, body)
    fact.save()
    if body.get("is_default") or not existing:
        set_as_default(fact)
    return ok({"fact": fact.to_dict()})


@api_view(["PATCH", "DELETE"])
def memory_detail(request, fact_id):
    fact = get_object_or_404(MemoryFact, id=fact_id)
    if request.method == "DELETE":
        fact.delete()
        return ok({"deleted": True})
    body = json_body(request)
    if "value" in body:
        value = str(body.get("value") or "").strip()
        if is_forbidden(fact.key, value):
            return error("That fact looks like a secret and will not be stored")
        fact.value = value
    _apply_fact_meta(fact, body)
    fact.save()
    if body.get("is_default"):
        set_as_default(fact)
    elif "is_default" in body and not body.get("is_default"):
        fact.is_default = False
        fact.save(update_fields=["is_default", "updated_at"])
    return ok({"fact": fact.to_dict()})


@api_view(["POST"])
def recordings_collection(request):
    body = json_body(request)
    events = body.get("events") or []
    if not isinstance(events, list) or not events:
        return error("events must be a non-empty list")
    recording = Recording.objects.create(
        intent=body.get("intent") or "",
        events=events,
    )
    screenshots = body.get("screenshots") or []
    try:
        compiled = compile_recording(recording, screenshots=screenshots)
    except Exception as exc:
        recording.status = "failed"
        recording.error = str(exc)
        recording.save(update_fields=["status", "error"])
        return error(str(exc), 500)
    return ok({"recording": recording.to_dict(), **compiled}, 201)


@api_view(["GET", "POST"])
def runs_collection(request):
    if request.method == "GET":
        runs = Run.objects.select_related("skill").prefetch_related("skill__stages").order_by("-updated_at")[:50]
        return ok({"runs": [activity_dict(run) for run in runs]})
    body = json_body(request)
    skill = None
    skill_id = body.get("skill_id")
    current_url = body.get("current_url") or ""
    intent = (body.get("intent") or "").strip()
    if skill_id:
        skill = get_object_or_404(Skill, id=skill_id)
        if not skill.ready:
            return error("Finish the compile review before running this skill")
        if host_mismatch(skill, current_url) and not body.get("force"):
            return error(
                "This skill was recorded on "
                + (skill.site_host or skill.start_url)
                + ". This tab is a different site. Open the right page, or send force=true."
            )
        intent = intent or skill.goal
    else:
        intent = intent or DEFAULT_INTENT
    mismatched = bool(skill and host_mismatch(skill, current_url))
    run = Run.objects.create(
        skill=skill,
        kind="skill" if skill else "attempt",
        intent=intent,
        tab_title=body.get("tab_title") or "",
        inputs=body.get("inputs") or {},
        current_url=current_url,
        status="running",
        needs_plan=not skill or mismatched or bool(body.get("force")),
    )
    stamp_activity(run)
    run.save(update_fields=["checkpoint", "updated_at"])
    try:
        payload = turn(run)
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.save(update_fields=["status", "error"])
        return error(str(exc), 500)
    return ok({"run": payload, "host_mismatch": mismatched}, 201)


@api_view(["GET"])
def run_detail(request, run_id):
    run = get_object_or_404(Run, id=run_id)
    return ok({"run": run.to_dict()})


@api_view(["POST"])
def run_turn(request, run_id):
    run = get_object_or_404(Run, id=run_id)
    body = json_body(request)
    if run.status in {"completed", "failed", "blocked"}:
        return ok({"run": run.to_dict()})
    try:
        payload = turn(
            run,
            tool_results=body.get("tool_results") or [],
            current_url=body.get("current_url"),
            tab_title=body.get("tab_title"),
        )
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.save(update_fields=["status", "error"])
        return error(str(exc), 500)
    return ok({"run": payload})


@api_view(["POST"])
def run_confirm(request, run_id):
    run = get_object_or_404(Run, id=run_id)
    body = json_body(request)
    try:
        payload = resolve_confirm(run, approved=bool(body.get("approved")))
    except Exception as exc:
        return error(str(exc), 400)
    return ok({"run": payload})


@api_view(["POST"])
def run_plan(request, run_id):
    run = get_object_or_404(Run, id=run_id)
    body = json_body(request)
    try:
        payload = resolve_plan(run, approved=bool(body.get("approved")))
    except Exception as exc:
        return error(str(exc), 400)
    return ok({"run": payload})


@api_view(["POST"])
def run_answer(request, run_id):
    run = get_object_or_404(Run, id=run_id)
    body = json_body(request)
    try:
        payload = resolve_value(run, body.get("value") or "", remember=bool(body.get("remember")))
    except Exception as exc:
        return error(str(exc), 400)
    return ok({"run": payload})


@api_view(["POST"])
def run_resume(request, run_id):
    run = get_object_or_404(Run, id=run_id)
    body = json_body(request)
    try:
        payload = resume_run(
            run,
            correction=body.get("correction") or "",
            remember=bool(body.get("remember")),
        )
    except Exception as exc:
        return error(str(exc), 400)
    return ok({"run": payload})


@api_view(["POST"])
def run_keep(request, run_id):
    run = get_object_or_404(Run, id=run_id)
    try:
        payload = keep_from_run(run)
    except Exception as exc:
        return error(str(exc), 400)
    return ok(payload)


@api_view(["POST"])
def run_pause(request, run_id):
    run = get_object_or_404(Run, id=run_id)
    run.status = "paused"
    run.last_status_text = "Paused from the overlay."
    run.save(update_fields=["status", "last_status_text", "updated_at"])
    return ok({"run": run.to_dict()})


@api_view(["POST"])
def run_file(request, run_id):
    run = get_object_or_404(Run, id=run_id)
    body = json_body(request)
    try:
        payload = resolve_file_pick(run, done=bool(body.get("done", True)))
    except Exception as exc:
        return error(str(exc), 400)
    return ok({"run": payload})


@api_view(["GET", "POST"])
def examples_collection(request):
    if request.method == "GET":
        return ok({"examples": [item.to_dict() for item in CraftExample.objects.all()]})
    body = json_body(request)
    try:
        example = keep_craft(
            body.get("kind") or "caption",
            body.get("text") or "",
            notes=body.get("notes") or "",
            title=body.get("title") or "",
        )
    except ValueError as exc:
        return error(str(exc))
    return ok({"example": example}, 201)


@api_view(["DELETE"])
def example_detail(request, example_id):
    example = get_object_or_404(CraftExample, id=example_id)
    example.delete()
    return ok({"deleted": True})


@api_view(["POST"])
def jobs_collection(request):
    body = json_body(request)
    prompt = (body.get("prompt") or body.get("intent") or "").strip()
    attachments = body.get("attachments") or []
    vault_ids = body.get("vault_ids") or []
    if not prompt and not attachments and not vault_ids:
        return error("Give it a prompt, or drop a file")
    job = compile_job(
        prompt,
        attachments=attachments,
        current_url=body.get("current_url") or "",
        tab_title=body.get("tab_title") or "",
        vault_ids=vault_ids,
    )
    run = Run.objects.create(
        kind="job",
        intent=job.get("goal") or prompt,
        tab_title=body.get("tab_title") or "",
        current_url=body.get("current_url") or "",
        inputs=job_inputs(job),
        job=job,
        status="awaiting_job",
        last_status_text="Here's how I'll deal with this.",
        checkpoint={"provider": job.get("provider")} if job.get("provider") else {},
    )
    stamp_activity(run)
    if job.get("mode") == "remember":
        example = remember_from_prompt(prompt)
        run.status = "completed"
        run.outcome = "Remembered as a " + example["kind"] + "."
        run.last_status_text = run.outcome
        run.fill_report = {"kept_example": example}
    run.save()
    return ok({"run": run.to_dict()}, 201)


@api_view(["POST"])
def job_go(request, run_id):
    run = get_object_or_404(Run, id=run_id)
    if run.kind != "job":
        return error("This run is not a job")
    if run.status not in {"awaiting_job", "paused"}:
        return error("This job is not waiting to start")
    body = json_body(request)
    job = apply_job_picks(run.job or {}, body)
    procedure = job.get("procedure") or {}
    is_listing = job.get("mode") == "listing" or procedure.get("status") == "listing"
    model_fill = (not is_listing) and (
        procedure.get("status") in {"learn", "mix"} or bool(procedure.get("off_script"))
    )
    skill = None
    if not model_fill:
        skill_id = procedure.get("skill_id")
        if skill_id:
            skill = get_object_or_404(Skill, id=skill_id)
            if not skill.ready:
                return error("Finish the compile review before running this skill")
    if body.get("keep_crafts"):
        for craft in job.get("crafts") or []:
            if craft.get("text"):
                keep_craft(
                    craft.get("kind") or "caption",
                    craft.get("text"),
                    notes=craft.get("notes") or "",
                    title=craft.get("title") or "",
                )
    job = refresh_job_memory(job)
    run.job = job
    run.skill = skill
    run.intent = job.get("goal") or run.intent
    run.inputs = job_inputs(job, body.get("answers") or {})
    if body.get("current_url"):
        run.current_url = body["current_url"]
    run.needs_plan = False if (model_fill or is_listing) else not skill
    run.plan_approved = bool(model_fill or is_listing)
    run.status = "running"
    run.last_status_text = "Starting."
    run.messages = []
    run.stage_index = 0
    bind_vault_to_run(run)
    arm_skill_queue(run)
    if model_fill:
        checkpoint = dict(run.checkpoint or {})
        checkpoint["off_script"] = True
        run.checkpoint = checkpoint
    stamp_activity(run)
    run.save()
    try:
        payload = turn(run)
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.save(update_fields=["status", "error"])
        return error(str(exc), 500)
    return ok({"run": payload})


def _stage_from_body(body, stage=None):
    playbook = body.get("playbook")
    if playbook is None:
        playbook = stage.playbook if stage else []
    demonstration = body.get("demonstration")
    if demonstration is None:
        demonstration = stage.demonstration if stage else []
    memory_keys = body.get("memory_keys")
    if memory_keys is None:
        memory_keys = stage.memory_keys if stage else []
    vault_file_ids = body.get("vault_file_ids")
    if vault_file_ids is None:
        vault_file_ids = stage.vault_file_ids if stage else []
    if not isinstance(playbook, list):
        playbook = [str(playbook)]
    if not isinstance(demonstration, list):
        demonstration = []
    if not isinstance(memory_keys, list):
        memory_keys = []
    if not isinstance(vault_file_ids, list):
        vault_file_ids = []
    vault_file_ids = [str(item) for item in vault_file_ids if item]
    yours = body.get("split_yours")
    if yours is None:
        yours = body.get("yours")
    if yours is None:
        yours = stage.split_yours if stage else []
    this_run = body.get("split_this_run")
    if this_run is None:
        this_run = body.get("this_run")
    if this_run is None:
        this_run = stage.split_this_run if stage else []
    if not isinstance(yours, list):
        yours = []
    if not isinstance(this_run, list):
        this_run = []
    return {
        "name": (body.get("name") if "name" in body else (stage.name if stage else "")) or "",
        "url_pattern": (body.get("url_pattern") if "url_pattern" in body else (stage.url_pattern if stage else ""))
        or "",
        "page_kind": (body.get("page_kind") if "page_kind" in body else (stage.page_kind if stage else "")) or "",
        "playbook": playbook,
        "demonstration": demonstration,
        "memory_keys": [str(item) for item in memory_keys if item],
        "vault_file_ids": vault_file_ids,
        "split_yours": yours,
        "split_this_run": this_run,
        "exit": (body.get("exit") if "exit" in body else (stage.exit if stage else "")) or "",
    }


@api_view(["GET", "POST"])
def documents_collection(request):
    if request.method == "GET":
        return ok({"documents": [item.to_dict() for item in Document.objects.all()]})
    uploaded = request.FILES.get("file")
    if not uploaded:
        return error("file is required")
    title = (request.POST.get("title") or uploaded.name or "Untitled")[:200]
    document = Document(
        title=title,
        mime=uploaded.content_type or "application/octet-stream",
        size=int(getattr(uploaded, "size", 0) or 0),
        status="stored",
        extracted_text="",
    )
    document.original = uploaded
    document.save()
    extract_and_propose(document)
    return ok({"document": document.to_dict(detail=True)}, 201)


@api_view(["GET", "PATCH", "DELETE"])
def document_detail(request, document_id):
    document = get_object_or_404(Document, id=document_id)
    if request.method == "DELETE":
        result = delete_document(document)
        return ok(result)
    if request.method == "PATCH":
        body = json_body(request)
        if "title" in body and str(body.get("title") or "").strip():
            document.title = str(body["title"]).strip()[:200]
        document.save()
        return ok({"document": document.to_dict(detail=True)})
    return ok({"document": document.to_dict(detail=True)})


@api_view(["GET"])
def document_file(request, document_id):
    document = get_object_or_404(Document, id=document_id)
    if not document.original:
        return error("No file stored", 404)
    return FileResponse(
        document.original.open("rb"),
        as_attachment=True,
        filename=Path(document.original.name).name,
        content_type=document.mime or "application/octet-stream",
    )


@api_view(["POST"])
def document_accept(request, document_id):
    document = get_object_or_404(Document, id=document_id)
    body = json_body(request)
    result = accept_proposals(
        document,
        fact_ids=body.get("fact_ids") or [],
        overwrite_keys=body.get("overwrite_keys") or [],
    )
    return ok({"document": document.to_dict(detail=True), **result})


@api_view(["POST"])
def document_reject(request, document_id):
    document = get_object_or_404(Document, id=document_id)
    body = json_body(request)
    result = reject_proposals(document, fact_ids=body.get("fact_ids") or [])
    return ok({"document": document.to_dict(detail=True), **result})


@api_view(["GET"])
def export_archive(request):
    buf = build_export_zip()
    response = HttpResponse(buf.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="taskwipe-profile.zip"'
    return response


@api_view(["GET", "POST"])
def skill_stages(request, skill_id):
    skill = get_object_or_404(Skill, id=skill_id)
    if request.method == "GET":
        return ok({"stages": [item.to_dict() for item in skill.stages.all()]})
    body = json_body(request)
    fields = _stage_from_body(body)
    index = body.get("index")
    if index is None:
        index = next_stage_index(skill)
    stage = Stage.objects.create(skill=skill, index=int(index), **fields)
    if skill.stages.count() == 1:
        sync_skill_from_stage(stage)
    return ok({"stage": stage.to_dict()}, 201)


@api_view(["GET", "PATCH", "DELETE"])
def skill_stage_detail(request, skill_id, stage_id):
    skill = get_object_or_404(Skill, id=skill_id)
    stage = get_object_or_404(Stage, id=stage_id, skill=skill)
    if request.method == "DELETE":
        stage.delete()
        return ok({"deleted": True})
    if request.method == "PATCH":
        body = json_body(request)
        fields = _stage_from_body(body, stage)
        for name, value in fields.items():
            setattr(stage, name, value)
        if "index" in body:
            stage.index = int(body["index"])
        stage.save()
        sync_skill_from_stage(stage)
        return ok({"stage": stage.to_dict(), "skill": skill.to_dict()})
    return ok({"stage": stage.to_dict()})


def demo_index(request):
    return render(request, "demo/index.html")


def demo_file(request, name):
    from core.demo_pack import DEMO_FILES, pack_path

    path = pack_path(name)
    if path is None:
        raise Http404("unknown demo file")
    handle = path.open("rb")
    response = FileResponse(handle, as_attachment=True, filename=name, content_type=DEMO_FILES[name])
    return response


def demo_form_a(request):
    return render(request, "demo/form_a.html")


def demo_form_a_education(request):
    return render(request, "demo/form_a_education.html")


def demo_form_a_upload(request):
    return render(request, "demo/form_a_upload.html")


def demo_form_a_review(request):
    return render(request, "demo/form_a_review.html")


def demo_form_b(request):
    return render(request, "demo/form_b.html")


def demo_parcel(request):
    return render(request, "demo/parcel.html")


def demo_studio(request):
    return render(request, "demo/studio.html")
