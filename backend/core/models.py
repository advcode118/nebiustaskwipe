import uuid
from pathlib import Path
from urllib.parse import urlparse

from django.db import models


def vault_upload_to(instance, filename):
    name = Path(str(filename or "file")).name.replace("\\", "_")
    if not name or name in {".", ".."}:
        name = "file"
    return f"vault/{instance.id}/{name}"


class Skill(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    goal = models.TextField()
    playbook = models.JSONField(default=list)
    inputs_schema = models.JSONField(default=list)
    memory_keys = models.JSONField(default=list)
    source_events = models.JSONField(default=list, blank=True)
    screenshot_paths = models.JSONField(default=list, blank=True)
    demonstration = models.JSONField(default=list, blank=True)
    lessons = models.JSONField(default=list, blank=True)
    start_url = models.TextField(blank=True, default="")
    site_host = models.CharField(max_length=300, blank=True, default="")
    page_kind = models.TextField(blank=True, default="")
    split_yours = models.JSONField(default=list, blank=True)
    split_this_run = models.JSONField(default=list, blank=True)
    ready = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "goal": self.goal,
            "playbook": self.playbook,
            "inputs_schema": self.inputs_schema,
            "memory_keys": self.memory_keys,
            "demonstration": self.demonstration,
            "lessons": self.lessons,
            "start_url": self.start_url,
            "site_host": self.site_host,
            "page_kind": self.page_kind,
            "split_yours": self.split_yours,
            "split_this_run": self.split_this_run,
            "ready": self.ready,
            "has_trace": bool(self.source_events),
            "screenshot_count": len(self.screenshot_paths or []),
            "stages": [stage.to_dict() for stage in self.stages.all()],
            "created_at": self.created_at.isoformat(),
        }


class Document(models.Model):
    STATUS_CHOICES = [
        ("stored", "stored"),
        ("extracted", "extracted"),
        ("failed", "failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    original = models.FileField(upload_to=vault_upload_to)
    mime = models.CharField(max_length=200, blank=True, default="")
    size = models.PositiveIntegerField(default=0)
    extracted_text = models.TextField(blank=True, default="")
    skipped = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="stored")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def to_dict(self, detail=False):
        name = ""
        if self.original:
            name = Path(self.original.name).name
        payload = {
            "id": str(self.id),
            "title": self.title,
            "mime": self.mime,
            "size": self.size,
            "status": self.status,
            "filename": name,
            "proposal_count": self.facts.filter(confirmed=False, rejected=False).count() if self.pk else 0,
            "created_at": self.created_at.isoformat(),
        }
        if detail:
            payload["extracted_text"] = self.extracted_text
            payload["skipped"] = self.skipped or []
            payload["proposals"] = [
                fact.to_dict()
                for fact in self.facts.filter(confirmed=False, rejected=False)
            ]
        return payload


class MemoryFact(models.Model):
    CATEGORY_CHOICES = [
        ("identity", "identity"),
        ("shipping", "shipping"),
        ("work", "work"),
        ("study", "study"),
        ("sites", "sites"),
        ("people", "people"),
        ("preferences", "preferences"),
        ("other", "other"),
    ]
    SENSITIVITY_CHOICES = [
        ("normal", "normal"),
        ("private", "private"),
        ("never", "never"),
    ]
    SOURCE_CHOICES = [
        ("typed", "typed"),
        ("recording", "recording"),
        ("file", "file"),
        ("run", "run"),
    ]
    CATEGORIES = {item[0] for item in CATEGORY_CHOICES}
    SENSITIVITIES = {item[0] for item in SENSITIVITY_CHOICES}
    SOURCES = {item[0] for item in SOURCE_CHOICES}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=120, db_index=True)
    label = models.CharField(max_length=200)
    value = models.TextField()
    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES, default="other")
    sensitivity = models.CharField(max_length=16, choices=SENSITIVITY_CHOICES, default="normal")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default="typed")
    source_quote = models.TextField(blank=True, default="")
    source_skill = models.ForeignKey(
        Skill, null=True, blank=True, on_delete=models.SET_NULL, related_name="memories"
    )
    source_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="facts"
    )
    confirmed = models.BooleanField(default=True)
    rejected = models.BooleanField(default=False)
    is_default = models.BooleanField(default=True)
    group_key = models.CharField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "key"]

    def to_dict(self):
        return {
            "id": str(self.id),
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "category": self.category,
            "sensitivity": self.sensitivity,
            "source": self.source,
            "source_quote": self.source_quote,
            "confirmed": self.confirmed,
            "rejected": self.rejected,
            "is_default": self.is_default,
            "group_key": self.group_key,
            "source_skill_id": str(self.source_skill_id) if self.source_skill_id else None,
            "source_document_id": str(self.source_document_id) if self.source_document_id else None,
            "updated_at": self.updated_at.isoformat(),
        }


class Stage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="stages")
    index = models.PositiveSmallIntegerField(default=0)
    name = models.CharField(max_length=200, blank=True, default="")
    url_pattern = models.TextField(blank=True, default="")
    page_kind = models.TextField(blank=True, default="")
    playbook = models.JSONField(default=list)
    demonstration = models.JSONField(default=list, blank=True)
    memory_keys = models.JSONField(default=list, blank=True)
    vault_file_ids = models.JSONField(default=list, blank=True)
    split_yours = models.JSONField(default=list, blank=True)
    split_this_run = models.JSONField(default=list, blank=True)
    exit = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["index"]
        unique_together = [("skill", "index")]

    def to_dict(self):
        return {
            "id": str(self.id),
            "skill_id": str(self.skill_id),
            "index": self.index,
            "name": self.name or "",
            "url_pattern": self.url_pattern,
            "page_kind": self.page_kind,
            "playbook": self.playbook or [],
            "demonstration": self.demonstration or [],
            "memory_keys": self.memory_keys or [],
            "vault_file_ids": self.vault_file_ids or [],
            "split_yours": self.split_yours or [],
            "split_this_run": self.split_this_run or [],
            "exit": self.exit,
        }


def url_pattern_from_start(start_url):
    if not start_url:
        return ""
    parsed = urlparse(start_url)
    return parsed.path or start_url


class Recording(models.Model):
    STATUS_CHOICES = [
        ("pending", "pending"),
        ("compiled", "compiled"),
        ("failed", "failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.TextField(blank=True, default="")
    events = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    skill = models.ForeignKey(
        Skill, null=True, blank=True, on_delete=models.SET_NULL, related_name="recordings"
    )
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "intent": self.intent,
            "status": self.status,
            "event_count": len(self.events or []),
            "skill": self.skill.to_dict() if self.skill_id else None,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
        }


class CraftExample(models.Model):
    KIND_CHOICES = [
        ("caption", "caption"),
        ("title", "title"),
        ("description", "description"),
        ("post", "post"),
        ("rule", "rule"),
        ("other", "other"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, default="caption")
    text = models.TextField()
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def to_dict(self):
        return {
            "id": str(self.id),
            "kind": self.kind,
            "text": self.text,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
        }


class Run(models.Model):
    STATUS_CHOICES = [
        ("pending", "pending"),
        ("running", "running"),
        ("awaiting_job", "awaiting_job"),
        ("awaiting_plan", "awaiting_plan"),
        ("awaiting_value", "awaiting_value"),
        ("awaiting_file", "awaiting_file"),
        ("awaiting_confirm", "awaiting_confirm"),
        ("completed", "completed"),
        ("failed", "failed"),
        ("paused", "paused"),
        ("blocked", "blocked"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(
        Skill, null=True, blank=True, on_delete=models.SET_NULL, related_name="runs"
    )
    kind = models.CharField(max_length=20, default="skill")
    intent = models.TextField(blank=True, default="")
    tab_title = models.TextField(blank=True, default="")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="pending")
    inputs = models.JSONField(default=dict)
    current_url = models.TextField(blank=True, default="")
    messages = models.JSONField(default=list)
    pending_actions = models.JSONField(default=list)
    confirm_summary = models.TextField(blank=True, default="")
    prompt = models.JSONField(default=dict, blank=True)
    fill_report = models.JSONField(default=dict, blank=True)
    last_status_text = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    outcome = models.TextField(blank=True, default="")
    job = models.JSONField(default=dict, blank=True)
    needs_plan = models.BooleanField(default=False)
    plan_approved = models.BooleanField(default=False)
    plan_nudged = models.BooleanField(default=False)
    nudged = models.BooleanField(default=False)
    step = models.IntegerField(default=0)
    stage_index = models.PositiveSmallIntegerField(default=0)
    checkpoint = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def to_dict(self):
        stage = None
        if self.skill_id:
            stage = self.skill.stages.filter(index=self.stage_index or 0).first()
        return {
            "id": str(self.id),
            "skill_id": str(self.skill_id) if self.skill_id else None,
            "skill_name": self.skill.name if self.skill_id else "",
            "kind": self.kind,
            "intent": self.intent,
            "status": self.status,
            "inputs": self.inputs,
            "current_url": self.current_url,
            "pending_actions": self.pending_actions,
            "confirm_summary": self.confirm_summary,
            "prompt": self.prompt or {},
            "fill_report": self.fill_report or {},
            "last_status_text": self.last_status_text,
            "error": self.error,
            "outcome": self.outcome,
            "job": self.job or {},
            "needs_plan": self.needs_plan,
            "plan_approved": self.plan_approved,
            "step": self.step,
            "stage_index": self.stage_index or 0,
            "stage": stage.to_dict() if stage else None,
            "provider": (self.checkpoint or {}).get("provider") or "",
        }
