import json
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.models import CraftExample, Document, MemoryFact, Run, Skill, Stage
from core.services.agent import (
    apply_control_tools,
    close_open_tools,
    repair_tool_pairing,
    resolve_confirm,
    start_messages,
    turn,
    _block_to_dict,
    _create_kwargs,
    _messages_create,
    _sanitize_messages,
)
from core.services.attempt import keep_from_run, match_skills
from core.services.commit import commit_skill
from django.conf import settings

from core.services.listing import _choose_package, attach_listing_copy
from core.services.job import (
    compile_job,
    compile_job_heuristic,
    infer_start,
    keep_craft,
    match_skills_for_goal,
    task_needs_public_page,
)
from core.services.memory import MEMORY_CAP, commit_yours, lookup_stored_fact, memory_for_run
from core.services.pagekind import classify_page, classify_page_heuristic
from core.services.split import fields_from_events, start_from_events
from core.services.runner import (
    apply_stage_turn,
    compact_messages,
    is_dangerous_name,
    leftover_work,
    local_actions,
    local_fill_from_memory,
    model_call_count,
    parse_page,
    payload_has_document_body,
    payload_has_earlier_screenshot,
    unexpected_page,
    useful_page_dump,
)
from core.services.vault import candidates_from_text
from core.services.stages import ensure_one_stage, split_event_slices, write_stages_from_events


class SplitTests(TestCase):
    def test_email_is_yours_and_motivation_is_this_run(self):
        yours, this_run = fields_from_events(
            [
                {"type": "navigate", "url": "http://127.0.0.1:8001/demo/form-a/"},
                {"type": "input", "name": "University email", "value": "alex@imperial.ac.uk"},
                {"type": "input", "name": "Full legal name", "value": "Alex Example"},
                {"type": "input", "name": "Why this internship", "value": "I like compilers a lot"},
            ]
        )
        your_keys = {item["key"] for item in yours}
        run_keys = {item["key"] for item in this_run}
        self.assertIn("email", your_keys)
        self.assertIn("full_name", your_keys)
        self.assertTrue(any("why" in key for key in run_keys))
        self.assertNotIn("email", run_keys)

    def test_start_url(self):
        url, host = start_from_events([{"type": "navigate", "url": "http://127.0.0.1:8001/demo/form-a/"}])
        self.assertEqual(host, "127.0.0.1:8001")
        self.assertIn("form-a", url)

    def test_commit_does_not_overwrite_email(self):
        MemoryFact.objects.create(key="email", label="Email", value="old@example.com", category="identity")
        skill = Skill.objects.create(name="x", goal="g", ready=False)
        result = commit_yours(
            [{"key": "email", "label": "University email", "value": "new@example.com", "category": "identity"}],
            skill,
            overwrite_keys=[],
        )
        self.assertEqual(MemoryFact.objects.get(key="email").value, "old@example.com")
        self.assertEqual(result["conflicts"][0]["proposed"], "new@example.com")

    def test_commit_overwrite_when_asked(self):
        MemoryFact.objects.create(key="email", label="Email", value="old@example.com", category="identity")
        skill = Skill.objects.create(name="x", goal="g", ready=False)
        commit_skill(
            skill,
            {
                "yours": [{"key": "email", "label": "Email", "value": "new@example.com", "category": "identity"}],
                "this_run": [],
                "playbook": ["fill the form"],
                "overwrite_keys": ["email"],
            },
        )
        self.assertEqual(MemoryFact.objects.get(key="email").value, "new@example.com")
        self.assertTrue(skill.ready)


class AttemptTests(TestCase):
    def test_memory_for_attempt_returns_all_facts(self):
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        MemoryFact.objects.create(key="course", label="Course", value="CS", category="work")
        keys = {item["key"] for item in memory_for_run(None)}
        self.assertEqual(keys, {"email", "course"})

    def test_match_same_host(self):
        Skill.objects.create(
            name="Internship",
            goal="Fill internship forms",
            page_kind="university internship application",
            site_host="127.0.0.1:8001",
            ready=True,
        )
        matches = match_skills("http://127.0.0.1:8001/demo/form-b/", "Student ambassador")
        self.assertEqual(matches[0]["name"], "Internship")
        self.assertIn("same site", matches[0]["match_reason"])

    def test_match_page_kind_on_a_new_host(self):
        Skill.objects.create(
            name="Internship",
            goal="Fill internship forms",
            page_kind="university internship application",
            site_host="forms.example.edu",
            ready=True,
        )
        matches = match_skills("https://bristol.ac.uk/internship/apply", "Internship application")
        self.assertEqual(matches[0]["name"], "Internship")

    def test_propose_plan_continues_with_a_model_call(self):
        run = Run.objects.create(kind="attempt", status="running", needs_plan=True, intent="Fill this")
        plan = _tool_response(
            {
                "id": "toolu_plan",
                "name": "propose_plan",
                "input": {"summary": "Name and email from memory, leave the essay."},
            }
        )
        fill = _tool_response(
            {"id": "toolu_fill", "name": "form_input", "input": {"target": "ref_1", "value": "Alex"}}
        )
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", side_effect=[plan, fill]
        ) as mocked:
            payload = turn(run)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["pending_actions"][0]["name"], "form_input")

    def test_ask_for_value_pauses_the_run(self):
        run = Run.objects.create(kind="attempt", status="running", intent="Fill this")
        payload = apply_control_tools(
            run,
            [
                {
                    "name": "ask_for_value",
                    "tool_use_id": "toolu_ask",
                    "input": {"question": "What is your phone number?", "key": "phone", "remember": True},
                }
            ],
        )
        self.assertEqual(payload["status"], "awaiting_value")
        self.assertEqual(payload["prompt"]["key"], "phone")

    def test_complete_stores_fill_report(self):
        run = Run.objects.create(kind="attempt", status="running", intent="Fill this")
        payload = apply_control_tools(
            run,
            [
                {
                    "name": "mark_goal_complete",
                    "tool_use_id": "toolu_done",
                    "input": {
                        "summary": "Filled identity fields.",
                        "fill_report": {
                            "filled": [{"label": "Email", "value": "a@x.com", "key": "email", "source": "memory"}],
                            "skipped": ["Motivation"],
                            "keep_as_skill": {"name": "Internship", "page_kind": "internship form"},
                        },
                    },
                }
            ],
        )
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["fill_report"]["skipped"], ["Motivation"])

    def test_keep_from_attempt_makes_a_draft(self):
        run = Run.objects.create(
            kind="attempt",
            status="completed",
            intent="Fill the internship form",
            current_url="http://127.0.0.1:8001/demo/form-b/",
            fill_report={
                "filled": [{"label": "Email", "value": "a@x.com", "key": "email", "source": "memory"}],
                "skipped": ["Motivation"],
                "keep_as_skill": {"name": "Ambassador form", "page_kind": "internship form"},
            },
        )
        result = keep_from_run(run)
        skill = Skill.objects.get(id=result["skill"]["id"])
        self.assertFalse(skill.ready)
        self.assertEqual(skill.name, "Ambassador form")
        self.assertEqual(skill.site_host, "127.0.0.1:8001")
        self.assertEqual(skill.split_yours[0]["key"], "email")
        self.assertEqual(len(result["skill"]["stages"]), 1)
        self.assertEqual(result["skill"]["stages"][0]["index"], 0)

    def test_attempt_start_messages_have_no_demonstration(self):
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        run = Run.objects.create(kind="attempt", intent="Fill this", needs_plan=True, current_url="http://x/form")
        messages = start_messages(run)
        text = messages[0]["content"][0]["text"]
        self.assertIn("no demonstration", text.lower())
        self.assertIn("do not wait", text.lower())
        self.assertNotIn("until the plan is approved", text.lower())
        self.assertIn("a@x.com", text)

    def test_match_endpoint(self):
        Skill.objects.create(name="Parcel", goal="Send a parcel", page_kind="checkout", site_host="shop.test", ready=True)
        response = self.client.get("/api/skills/match/", {"url": "https://shop.test/buy", "title": "Checkout"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["skills"][0]["name"], "Parcel")


class ThinkingBlockTests(TestCase):
    def test_empty_thinking_is_dropped(self):
        self.assertIsNone(_block_to_dict({"type": "thinking"}))

    def test_thinking_keeps_text_and_signature(self):
        block = _block_to_dict(
            {"type": "thinking", "thinking": "map email to University email", "signature": "sig"}
        )
        self.assertEqual(block["thinking"], "map email to University email")
        self.assertEqual(block["signature"], "sig")

    def test_sanitize_strips_broken_thinking_from_old_runs(self):
        messages = _sanitize_messages(
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking"},
                        {"type": "tool_use", "id": "toolu_1", "name": "read_page", "input": {}},
                    ],
                }
            ]
        )
        self.assertEqual(len(messages[0]["content"]), 1)
        self.assertEqual(messages[0]["content"][0]["name"], "read_page")

    def test_mismatched_thinking_signature_is_sent_again_without_the_block(self):
        messages = [
            {"role": "user", "content": "Fill the form"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "map the email", "signature": "sig"},
                    {"type": "tool_use", "id": "toolu_1", "name": "read_page", "input": {}},
                ],
            },
        ]
        kwargs = _create_kwargs(messages)
        self.assertNotIn("thinking", kwargs)
        self.assertNotIn("extra_headers", kwargs)
        self.assertEqual([block["type"] for block in kwargs["messages"][1]["content"]], ["tool_use"])
        tool_names = [tool["name"] for tool in kwargs["tools"]]
        self.assertIn("form_input", tool_names)
        self.assertNotIn("browser_toolset_20260801", [tool.get("type") for tool in kwargs["tools"]])
        calls = []

        class Client:
            class messages:
                @staticmethod
                def create(**sent):
                    calls.append(sent)
                    if len(calls) == 1:
                        raise RuntimeError(
                            "Error code: 400 - Invalid `signature` in `thinking` block. "
                            "The block is bound to a different conversation."
                        )
                    return SimpleNamespace(content=[], stop_reason="end_turn")

        result = _messages_create(Client(), kwargs)
        self.assertEqual(result.stop_reason, "end_turn")
        self.assertEqual(len(calls), 2)
        replayed = calls[1]["messages"][1]["content"]
        self.assertEqual([block["type"] for block in replayed], ["tool_use"])

    def test_stray_user_text_after_tool_use_is_repaired(self):
        messages = repair_tool_pairing(
            [
                {"role": "user", "content": "Fill the form"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_013vf9S3wZK5ovevbbLPGiVR", "name": "screenshot", "input": {}},
                    ],
                },
                {"role": "user", "content": "Stop reading and scrolling."},
            ]
        )
        self.assertEqual(messages[2]["role"], "user")
        kinds = [block["type"] for block in messages[2]["content"]]
        self.assertEqual(kinds[0], "tool_result")
        self.assertEqual(messages[2]["content"][0]["tool_use_id"], "toolu_013vf9S3wZK5ovevbbLPGiVR")
        self.assertIn("text", kinds)

    def test_member_tool_result_without_toolset_name_is_echoed(self):
        messages = _sanitize_messages(
            [
                {"role": "user", "content": "Fill the form"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read_page",
                            "input": {},
                            "toolset_name": "browser",
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}],
                },
            ]
        )
        self.assertEqual(messages[2]["content"][0]["toolset_name"], "browser")

    def test_close_open_tools_echoes_browser_toolset_name(self):
        closed = close_open_tools(
            [
                {"role": "user", "content": "Fill the form"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read_page",
                            "input": {},
                            "toolset_name": "browser",
                        }
                    ],
                },
            ],
            extra_text="Current page: name Maya Chen",
        )
        block = closed[2]["content"][0]
        self.assertEqual(block["type"], "tool_result")
        self.assertEqual(block["tool_use_id"], "toolu_1")
        self.assertEqual(block["toolset_name"], "browser")

    def test_missing_toolset_name_is_retried_with_echo(self):
        messages = [
            {"role": "user", "content": "Fill the form"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_page",
                        "input": {},
                        "toolset_name": "browser",
                    }
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}],
            },
        ]
        calls = []

        class Client:
            class messages:
                @staticmethod
                def create(**sent):
                    calls.append(sent)
                    if len(calls) == 1:
                        raise RuntimeError(
                            "Error code: 400 - messages.2.content.0: a tool_result answering a "
                            "member tool_use must carry the paired tool_use's toolset_name "
                            "('browser'); this one carries no toolset_name"
                        )
                    return SimpleNamespace(content=[], stop_reason="end_turn")

        result = _messages_create(Client(), {"messages": messages, "thinking": {}})
        self.assertEqual(result.stop_reason, "end_turn")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["messages"][2]["content"][0]["toolset_name"], "browser")


class MemoryApiTests(TestCase):
    def test_same_email_is_kept_not_rejected(self):
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        response = self.client.post(
            "/api/memory/",
            data={"key": "email", "label": "Email", "value": "a@x.com", "category": "identity"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_different_email_needs_overwrite(self):
        MemoryFact.objects.create(key="email", label="Email", value="old@x.com", category="identity")
        blocked = self.client.post(
            "/api/memory/",
            data={"key": "email", "label": "Email", "value": "new@x.com", "category": "identity"},
            content_type="application/json",
        )
        self.assertEqual(blocked.status_code, 400)
        replaced = self.client.post(
            "/api/memory/",
            data={"key": "email", "label": "Email", "value": "new@x.com", "overwrite": True},
            content_type="application/json",
        )
        self.assertEqual(replaced.status_code, 200)
        self.assertEqual(MemoryFact.objects.get(key="email").value, "new@x.com")

    def test_card_number_is_refused(self):
        response = self.client.post(
            "/api/memory/",
            data={"key": "note", "label": "Note", "value": "4111 1111 1111 1111"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MemoryFact.objects.count(), 0)

    def test_second_address_needs_add_another(self):
        MemoryFact.objects.create(
            key="shipping.line1",
            label="Line 1",
            value="Home Street",
            category="shipping",
            is_default=True,
        )
        blocked = self.client.post(
            "/api/memory/",
            data={"key": "shipping.line1", "label": "Line 1", "value": "Term Street", "category": "shipping"},
            content_type="application/json",
        )
        self.assertEqual(blocked.status_code, 400)
        added = self.client.post(
            "/api/memory/",
            data={
                "key": "shipping.line1",
                "label": "Line 1",
                "value": "Term Street",
                "category": "shipping",
                "add_another": True,
                "group_key": "term",
            },
            content_type="application/json",
        )
        self.assertEqual(added.status_code, 200)
        self.assertEqual(MemoryFact.objects.filter(key="shipping.line1").count(), 2)
        chosen = [item for item in memory_for_run(None) if item["key"] == "shipping.line1"]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["value"], "Home Street")

    def test_never_send_is_omitted_from_run_memory(self):
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        MemoryFact.objects.create(
            key="private_note",
            label="Private",
            value="do not tell the model",
            category="other",
            sensitivity="never",
        )
        keys = {item["key"] for item in memory_for_run(None)}
        self.assertEqual(keys, {"email"})

    def test_run_slice_skips_unrelated_facts(self):
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        MemoryFact.objects.create(key="hobby", label="Hobby", value="chess", category="other")
        keys = {item["key"] for item in memory_for_run(None, goal="upload this onto youtube")}
        self.assertIn("email", keys)
        self.assertNotIn("hobby", keys)

    def test_run_slice_caps_at_thirty(self):
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        for index in range(40):
            MemoryFact.objects.create(
                key="note_" + str(index),
                label="Note " + str(index),
                value=str(index),
                category="other",
            )
        sliced = memory_for_run(None)
        self.assertEqual(len(sliced), MEMORY_CAP)
        self.assertIn("email", {item["key"] for item in sliced})

    def test_patch_sets_the_default_address(self):
        home = MemoryFact.objects.create(
            key="shipping.line1",
            label="Line 1",
            value="Home Street",
            category="shipping",
            group_key="home",
            is_default=True,
        )
        term = MemoryFact.objects.create(
            key="shipping.line1",
            label="Line 1",
            value="Term Street",
            category="shipping",
            group_key="term",
            is_default=False,
        )
        response = self.client.patch(
            "/api/memory/" + str(term.id) + "/",
            data={"is_default": True},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        home.refresh_from_db()
        term.refresh_from_db()
        self.assertFalse(home.is_default)
        self.assertTrue(term.is_default)
        chosen = memory_for_run(None, current_url="http://127.0.0.1:8001/demo/parcel/", goal="send a parcel")
        lines = [item for item in chosen if item["key"] == "shipping.line1"]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["value"], "Term Street")


class JobTests(TestCase):
    def test_youtube_prompt_splits_caption_and_upload(self):
        job = compile_job_heuristic(
            "upload this video onto youtube and a suitable caption",
            attachments=[{"name": "clip.mp4", "media_type": "video/mp4", "size": 1200}],
            current_url="http://127.0.0.1:8001/demo/form-a/",
        )
        self.assertEqual(job["mode"], "run")
        self.assertIn("caption", job["crafts_needed"])
        self.assertEqual(job["procedure"]["status"], "learn")
        self.assertIn("/demo/studio/", job["start_url"])
        self.assertEqual(job["stop_before"], "Publish")
        self.assertEqual(job["attachments"][0]["name"], "clip.mp4")
        self.assertTrue(any("suitable" in cue.lower() for cue in job["memory_cues"]))

    def test_localhost_form_skill_is_not_a_youtube_job(self):
        Skill.objects.create(
            name="Internship",
            goal="Fill this internship form with why this course",
            page_kind="university internship application",
            site_host="127.0.0.1:8001",
            ready=True,
        )
        job = compile_job_heuristic(
            "upload this video onto youtube with a suitable caption",
            current_url="http://127.0.0.1:8001/demo/",
        )
        self.assertEqual(job["procedure"]["status"], "learn")
        self.assertFalse(job["procedure"]["skill_id"])

    def test_picks_a_known_youtube_skill(self):
        Skill.objects.create(
            name="Upload to YouTube",
            goal="Upload a video to YouTube Studio",
            page_kind="youtube studio upload",
            site_host="studio.youtube.com",
            start_url="https://studio.youtube.com",
            ready=True,
        )
        matches = match_skills_for_goal("upload this onto youtube with a caption")
        self.assertEqual(matches[0]["name"], "Upload to YouTube")
        job = compile_job_heuristic("upload this onto youtube with a caption")
        self.assertEqual(job["procedure"]["status"], "known")
        self.assertEqual(job["procedure"]["skill_name"], "Upload to YouTube")

    def test_two_close_skills_are_mixed(self):
        Skill.objects.create(
            name="Upload a video",
            goal="Upload a video to YouTube",
            page_kind="youtube video upload",
            site_host="studio.youtube.com",
            ready=True,
        )
        Skill.objects.create(
            name="Upload a Short",
            goal="Upload a short to YouTube",
            page_kind="youtube short upload",
            site_host="studio.youtube.com",
            ready=True,
        )
        job = compile_job_heuristic("upload this onto youtube")
        self.assertEqual(job["procedure"]["status"], "mix")
        self.assertFalse(job["procedure"]["skill_id"])
        self.assertEqual(
            {item["skill_name"] for item in job["procedure"]["skills"]},
            {"Upload a video", "Upload a Short"},
        )

    def test_one_prompt_can_use_two_skills(self):
        Skill.objects.create(
            name="Internship",
            goal="Apply for the internship",
            page_kind="internship application",
            site_host="127.0.0.1:8001",
            start_url="http://127.0.0.1:8001/demo/form-a/",
            ready=True,
        )
        Skill.objects.create(
            name="Parcel",
            goal="Send a parcel",
            page_kind="parcel checkout",
            site_host="127.0.0.1:8001",
            start_url="http://127.0.0.1:8001/demo/parcel/",
            ready=True,
        )
        job = compile_job_heuristic("apply for the internship and send the parcel")
        self.assertEqual(job["procedure"]["status"], "sequence")
        self.assertEqual(
            [item["skill_name"] for item in job["procedure"]["skills"]],
            ["Internship", "Parcel"],
        )
        created = self.client.post(
            "/api/jobs/",
            data={"prompt": "apply for the internship and send the parcel"},
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        run_id = created.json()["run"]["id"]
        first_id = created.json()["run"]["job"]["procedure"]["skill_id"]
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create",
            return_value=_tool_response(
                {"id": "toolu_read", "name": "read_page", "input": {"filter": "interactive"}},
            ),
        ):
            started = self.client.post(
                "/api/jobs/" + run_id + "/go/",
                data={"skill_id": first_id},
                content_type="application/json",
            )
        self.assertEqual(started.status_code, 200)
        stored = Run.objects.get(id=run_id)
        self.assertEqual(stored.checkpoint.get("skill_queue"), [created.json()["run"]["job"]["procedure"]["skills"][1]["skill_id"]])
        self.assertEqual(str(stored.skill_id), first_id)

    def test_caption_rule_is_remembered_not_run(self):
        response = self.client.post(
            "/api/jobs/",
            data={"prompt": "My captions should never say smash that like button"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()["run"]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["job"]["mode"], "remember")
        self.assertEqual(CraftExample.objects.get().kind, "rule")

    def test_job_card_endpoint(self):
        CraftExample.objects.create(kind="caption", text="Guest's name in the first line. No hashtags.")
        response = self.client.post(
            "/api/jobs/",
            data={
                "prompt": "upload this video onto youtube with a suitable caption",
                "current_url": "http://127.0.0.1:8001/demo/",
                "attachments": [{"name": "talk.mp4", "media_type": "video/mp4", "size": 4000, "injectable": True}],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        run = response.json()["run"]
        self.assertEqual(run["status"], "awaiting_job")
        self.assertEqual(run["kind"], "job")
        self.assertEqual(run["job"]["crafts"][0]["status"], "from_examples")
        self.assertIn("talk.mp4", run["job"]["attachments"][0]["name"])

    def test_job_card_shows_the_memory_slice(self):
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        MemoryFact.objects.create(key="hobby", label="Hobby", value="chess", category="other")
        response = self.client.post(
            "/api/jobs/",
            data={
                "prompt": "fill the internship form",
                "current_url": "http://127.0.0.1:8001/demo/form-a/",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        keys = {item["key"] for item in response.json()["run"]["job"]["memory"]}
        self.assertIn("email", keys)
        self.assertNotIn("hobby", keys)

    def test_keep_craft_does_not_make_a_skill(self):
        example = keep_craft("caption", "Quiet week. New clip up.", title="Quiet week")
        self.assertEqual(example["kind"], "caption")
        self.assertIn("Quiet week", example["text"])
        self.assertEqual(Skill.objects.count(), 0)

    def test_job_start_messages_include_attachment_and_caption(self):
        run = Run.objects.create(
            kind="job",
            intent="Upload a video",
            needs_plan=True,
            job={
                "goal": "Upload a video to YouTube",
                "prompt": "upload this with a suitable caption",
                "attachments": [{"name": "clip.mp4", "role": "this_run", "injectable": True}],
                "crafts": [{"kind": "caption", "text": "Quiet week.", "status": "from_examples"}],
                "start_url": "http://127.0.0.1:8001/demo/studio/",
                "stop_before": "Publish",
            },
        )
        messages = start_messages(run)
        text = messages[0]["content"][0]["text"]
        self.assertIn("JOB", text)
        self.assertIn("clip.mp4", text)
        self.assertIn("Quiet week.", text)
        self.assertIn("Publish", text)

    def test_request_file_pick_pauses(self):
        run = Run.objects.create(kind="job", status="running", intent="Upload")
        payload = apply_control_tools(
            run,
            [
                {
                    "name": "request_file_pick",
                    "tool_use_id": "toolu_file",
                    "input": {"filename": "clip.mp4", "summary": "Choose clip.mp4"},
                }
            ],
        )
        self.assertEqual(payload["status"], "awaiting_file")
        self.assertEqual(payload["prompt"]["filename"], "clip.mp4")

    def test_local_studio_url(self):
        url, host, label = infer_start(
            "upload this onto youtube",
            "http://127.0.0.1:8001/demo/form-a/",
        )
        self.assertIn("/demo/studio/", url)
        self.assertEqual(host, "127.0.0.1:8001")
        self.assertIn("studio", label.lower())

    def test_go_without_a_pick_on_close_skills_starts(self):
        Skill.objects.create(name="Upload a video", goal="Upload a video to YouTube", page_kind="youtube video", site_host="studio.youtube.com", ready=True)
        Skill.objects.create(name="Upload a Short", goal="Upload a short to YouTube", page_kind="youtube short", site_host="studio.youtube.com", ready=True)
        created = self.client.post(
            "/api/jobs/",
            data={"prompt": "upload this onto youtube"},
            content_type="application/json",
        )
        run_id = created.json()["run"]["id"]
        self.assertEqual(created.json()["run"]["job"]["procedure"]["status"], "mix")
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create",
            return_value=_tool_response(
                {"id": "toolu_read", "name": "read_page", "input": {"filter": "interactive"}},
            ),
        ):
            started = self.client.post(
                "/api/jobs/" + run_id + "/go/",
                data={},
                content_type="application/json",
            )
        self.assertEqual(started.status_code, 200)
        stored = Run.objects.get(id=run_id)
        self.assertIsNone(stored.skill_id)
        self.assertTrue(stored.checkpoint.get("off_script"))

    def test_official_application_stays_on_the_page(self):
        Skill.objects.create(
            name="Internship",
            goal="Apply for the internship",
            page_kind="internship application",
            site_host="127.0.0.1:8001",
            start_url="http://127.0.0.1:8001/demo/form-a/",
            playbook=["fill name", "fill email"],
            memory_keys=["email"],
            ready=True,
        )
        job = compile_job_heuristic(
            "apply for this job",
            current_url="https://jobs.example.edu/vacancies/42/apply",
            tab_title="Software engineer",
        )
        self.assertTrue(job["procedure"]["off_script"])
        self.assertEqual(job["start_url"], "https://jobs.example.edu/vacancies/42/apply")
        self.assertEqual(job["skill_hints"][0]["name"], "Internship")
        self.assertNotEqual(job["procedure"]["status"], "ambiguous")

    def test_pasted_application_url_uses_stored_data_and_does_not_ask(self):
        MemoryFact.objects.create(key="email", label="Email", value="ada@example.com", category="identity")
        MemoryFact.objects.create(key="full_name", label="Name", value="Ada Example", category="identity")
        MemoryFact.objects.create(key="hobby", label="Hobby", value="chess", category="other")
        Skill.objects.create(
            name="Internship",
            goal="Apply for the internship",
            page_kind="internship application",
            site_host="127.0.0.1:8001",
            start_url="http://127.0.0.1:8001/demo/form-a/",
            ready=True,
        )
        Skill.objects.create(
            name="Upload a video",
            goal="Upload a video to YouTube",
            page_kind="youtube video upload",
            site_host="studio.youtube.com",
            ready=True,
        )
        Skill.objects.create(
            name="Upload a Short",
            goal="Upload a short to YouTube",
            page_kind="youtube short upload",
            site_host="studio.youtube.com",
            ready=True,
        )
        url = "https://www.janestreet.com/apply-fttp-ldn/"
        prompt = "apply to this - " + url
        job = compile_job_heuristic(prompt, current_url="http://127.0.0.1:8001/demo/form-a/")
        self.assertEqual(job["start_url"], url)
        self.assertTrue(job["procedure"]["off_script"])
        self.assertNotEqual(job["procedure"]["status"], "ambiguous")
        self.assertIn("Submit", job["stop_before"])
        keys = {item["key"] for item in job["memory"]}
        self.assertIn("email", keys)
        self.assertIn("full_name", keys)
        self.assertIn("hobby", keys)
        created = self.client.post(
            "/api/jobs/",
            data={"prompt": prompt, "current_url": "http://127.0.0.1:8001/demo/form-a/"},
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        run_id = created.json()["run"]["id"]
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create",
            return_value=_tool_response(
                {"id": "toolu_read", "name": "read_page", "input": {"filter": "interactive"}},
            ),
        ):
            started = self.client.post(
                "/api/jobs/" + run_id + "/go/",
                data={},
                content_type="application/json",
            )
        self.assertEqual(started.status_code, 200)
        body = started.json()
        self.assertNotIn("Pick one", json.dumps(body))
        self.assertNotEqual(body["run"]["status"], "awaiting_plan")
        names = [item["name"] for item in body["run"].get("pending_actions") or []]
        self.assertNotIn("request_user_confirm", names)

    def test_jane_street_fill_uses_fake_facts_and_holds_submit(self):
        MemoryFact.objects.create(key="first_name", label="Legal first name", value="Ada", category="identity")
        MemoryFact.objects.create(key="last_name", label="Legal last name", value="Example", category="identity")
        MemoryFact.objects.create(key="email", label="Email", value="ada@example.com", category="identity")
        MemoryFact.objects.create(key="phone", label="Phone", value="5550100", category="identity")
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="apply to https://www.janestreet.com/apply-fttp-ldn/",
            current_url="https://www.janestreet.com/apply-fttp-ldn/",
            checkpoint={"off_script": True},
            job={
                "goal": "apply to https://www.janestreet.com/apply-fttp-ldn/",
                "prompt": "apply to https://www.janestreet.com/apply-fttp-ldn/",
                "start_url": "https://www.janestreet.com/apply-fttp-ldn/",
                "stop_before": "Submit",
                "procedure": {"status": "learn", "off_script": True, "skill_id": None},
            },
        )
        reply = _tool_response(
            {"id": "toolu_1", "name": "form_input", "input": {"target": "e20", "value": "Ada"}},
            {"id": "toolu_2", "name": "form_input", "input": {"target": "e22", "value": "Example"}},
            {"id": "toolu_3", "name": "form_input", "input": {"target": "e23", "value": "ada@example.com"}},
            {"id": "toolu_4", "name": "form_input", "input": {"target": "e26", "value": "5550100"}},
            {"id": "toolu_5", "name": "left_click", "input": {"target": "e42"}},
            {"id": "toolu_6", "name": "request_user_confirm", "input": {"summary": "Submit the application"}},
        )
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", return_value=reply
        ) as mocked:
            first = turn(run)
            self.assertEqual(first["status"], "running")
            values = [item["input"]["value"] for item in first["pending_actions"]]
            self.assertEqual(values, ["Ada", "Example", "ada@example.com", "5550100"])
            self.assertTrue(all(item["name"] == "form_input" for item in first["pending_actions"]))
            second = turn(
                run,
                tool_results=[
                    {"tool_use_id": item["tool_use_id"], "content": "Filled"}
                    for item in first["pending_actions"]
                ],
            )
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(second["status"], "awaiting_confirm")
        self.assertIn("Submit", second["confirm_summary"])
        self.assertNotIn("left_click", [item["name"] for item in second["pending_actions"][:1]])

    def test_learn_apply_job_shows_form_a_stages_and_address(self):
        MemoryFact.objects.create(
            key="full_name", label="Full name", value="Maya Chen", category="identity"
        )
        MemoryFact.objects.create(
            key="shipping.line1",
            label="Address line 1",
            value="14 Exhibition Road",
            category="shipping",
        )
        job = compile_job_heuristic(
            "apply with my CV and transcript, stop before submit",
            current_url="http://127.0.0.1:8001/demo/form-a/",
        )
        self.assertEqual(job["procedure"]["status"], "learn")
        self.assertEqual(
            job["procedure"]["stages"],
            ["Personal details", "Education history", "Transcript upload", "Review"],
        )
        keys = {item["key"] for item in job["memory"]}
        self.assertIn("full_name", keys)
        self.assertIn("shipping.line1", keys)
        self.assertEqual(job["stop_before"], "Submit")

    def test_learn_form_a_reads_and_fills_without_a_model_call(self):
        MemoryFact.objects.create(key="full_name", label="Full name", value="Maya Chen", category="identity", confirmed=True)
        MemoryFact.objects.create(key="school_email", label="University email", value="maya.chen24@imperial.ac.uk", category="identity", confirmed=True)
        MemoryFact.objects.create(key="shipping.line1", label="Address line 1", value="14 Exhibition Road", category="shipping", confirmed=True)
        MemoryFact.objects.create(key="shipping.city", label="City", value="London", category="shipping", confirmed=True)
        MemoryFact.objects.create(key="shipping.postcode", label="Postcode", value="SW7 2AZ", category="shipping", confirmed=True)
        created = self.client.post(
            "/api/jobs/",
            data={
                "prompt": "apply with my CV and transcript, stop before submit",
                "current_url": "http://127.0.0.1:8001/demo/form-a/",
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        run_id = created.json()["run"]["id"]
        with patch("core.services.agent._messages_create", side_effect=AssertionError("page 1 must not call the model")):
            started = self.client.post(
                f"/api/jobs/{run_id}/go/",
                data={"current_url": "http://127.0.0.1:8001/demo/form-a/"},
                content_type="application/json",
            )
            self.assertEqual(started.status_code, 200)
            run = started.json()["run"]
            self.assertEqual(run["pending_actions"][0]["name"], "read_page")
            self.assertEqual(model_call_count(Run.objects.get(id=run_id)), 0)
            personal = self.client.post(
                f"/api/runs/{run_id}/turn/",
                data={
                    "current_url": "http://127.0.0.1:8001/demo/form-a/",
                    "tool_results": [
                        {"tool_use_id": run["pending_actions"][0]["tool_use_id"], "content": FORM_A_APPLY_PAGE1}
                    ],
                },
                content_type="application/json",
            )
        self.assertEqual(personal.status_code, 200)
        run = personal.json()["run"]
        fills = {item["input"]["value"] for item in run["pending_actions"] if item["name"] == "form_input"}
        self.assertEqual(
            fills,
            {"Maya Chen", "maya.chen24@imperial.ac.uk", "14 Exhibition Road", "London", "SW7 2AZ"},
        )
        self.assertNotIn("submit", [item["name"] for item in run["pending_actions"]])
        self.assertTrue(any(item["name"] == "left_click" for item in run["pending_actions"]))
        self.assertEqual(model_call_count(Run.objects.get(id=run_id)), 0)
        self.assertNotIn("tavily", json.dumps(run).lower())

    def test_apply_prompt_picks_vault_cv_and_transcript_by_name(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.settings(MEDIA_ROOT=folder):
                cv = self.client.post(
                    "/api/documents/",
                    data={
                        "title": "maya-chen-cv.docx",
                        "file": _docx_upload(["Maya Chen"], name="maya-chen-cv.docx"),
                    },
                )
                transcript = self.client.post(
                    "/api/documents/",
                    data={
                        "title": "maya-chen-transcript.docx",
                        "file": _docx_upload(["Year 3 Computing transcript"], name="maya-chen-transcript.docx"),
                    },
                )
                self.assertEqual(cv.status_code, 201)
                self.assertEqual(transcript.status_code, 201)
                job = compile_job_heuristic(
                    "apply with my CV and transcript, stop before submit",
                    current_url="http://127.0.0.1:8001/demo/form-a/",
                )
        titles = {item.get("title") for item in job["attachments"]}
        self.assertEqual(titles, {"maya-chen-cv.docx", "maya-chen-transcript.docx"})
        self.assertTrue(all(item.get("document_id") for item in job["attachments"]))

    def test_localhost_apply_still_opens_form_a(self):
        Skill.objects.create(
            name="Internship",
            goal="Apply for the internship",
            page_kind="internship application",
            site_host="127.0.0.1:8001",
            start_url="http://127.0.0.1:8001/demo/form-a/",
            ready=True,
        )
        job = compile_job_heuristic(
            "apply with my CV and transcript",
            current_url="http://127.0.0.1:8001/demo/",
        )
        self.assertFalse(job["procedure"].get("off_script"))
        self.assertIn("/demo/form-a/", job["start_url"])

    def test_stored_fact_is_looked_up_instead_of_pausing(self):
        MemoryFact.objects.create(key="phone", label="Phone", value="07123", category="identity")
        run = Run.objects.create(kind="attempt", status="running", intent="Fill this application")
        ask = _tool_response(
            {
                "id": "toolu_ask",
                "name": "ask_for_value",
                "input": {"question": "What is your phone?", "key": "phone", "label": "Phone"},
            }
        )
        fill = _tool_response(
            {"id": "toolu_fill", "name": "form_input", "input": {"target": "ref_1", "value": "07123"}}
        )
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", side_effect=[ask, fill]
        ) as mocked:
            payload = turn(run)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(payload["status"], "running")
        blob = json.dumps(mocked.call_args_list[1][0][1]["messages"])
        self.assertIn("07123", blob)

    def test_missing_fact_still_asks(self):
        run = Run.objects.create(kind="attempt", status="running", intent="Fill this")
        ask = _tool_response(
            {
                "id": "toolu_ask",
                "name": "ask_for_value",
                "input": {"question": "What is your middle name?", "key": "middle_name", "label": "Middle name"},
            }
        )
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", return_value=ask
        ) as mocked:
            payload = turn(run)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(payload["status"], "awaiting_value")


class StageSchemaTests(TestCase):
    def test_ensure_one_stage_copies_skill_fields(self):
        skill = Skill.objects.create(
            name="Internship",
            goal="Fill the form",
            playbook=["fill the form"],
            demonstration=[{"action": "fill", "field": "email"}],
            memory_keys=["email"],
            page_kind="university internship application",
            start_url="http://127.0.0.1:8001/demo/form-a/",
        )
        stage = ensure_one_stage(skill)
        self.assertEqual(skill.stages.count(), 1)
        self.assertEqual(stage.index, 0)
        self.assertEqual(stage.page_kind, "university internship application")
        self.assertEqual(stage.url_pattern, "/demo/form-a/")
        self.assertEqual(stage.playbook, ["fill the form"])
        data = skill.to_dict()
        self.assertEqual(len(data["stages"]), 1)
        self.assertEqual(data["stages"][0]["id"], str(stage.id))

    def test_commit_keeps_a_single_stage(self):
        skill = Skill.objects.create(name="x", goal="g", ready=False)
        commit_skill(
            skill,
            {
                "yours": [{"key": "email", "label": "Email", "value": "a@x.com", "category": "identity"}],
                "this_run": [],
                "playbook": ["fill the form"],
                "page_kind": "form",
                "start_url": "http://127.0.0.1:8001/demo/form-b/",
            },
        )
        self.assertEqual(skill.stages.count(), 1)
        self.assertEqual(skill.stages.first().page_kind, "form")
        self.assertEqual(skill.stages.first().url_pattern, "/demo/form-b/")

    def test_two_url_changes_become_three_stages(self):
        events = [
            {"type": "navigate", "url": "http://127.0.0.1:8001/demo/form-a/"},
            {"type": "input", "name": "University email", "value": "alex@imperial.ac.uk"},
            {"type": "click", "name": "Continue"},
            {"type": "navigate", "url": "http://127.0.0.1:8001/demo/form-a/education/"},
            {"type": "input", "name": "Course", "value": "Computing"},
            {"type": "click", "name": "Continue"},
            {"type": "navigate", "url": "http://127.0.0.1:8001/demo/form-a/review/"},
            {"type": "input", "name": "Why this internship", "value": "I like compilers a lot"},
            {"type": "click", "name": "Submit application"},
        ]
        self.assertEqual(len(split_event_slices(events)), 3)
        skill = Skill.objects.create(
            name="Internship",
            goal="Apply",
            start_url="http://127.0.0.1:8001/demo/form-a/",
            source_events=events,
        )
        yours, this_run = fields_from_events(events)
        stages = write_stages_from_events(skill, events, yours, this_run)
        self.assertEqual(len(stages), 3)
        self.assertEqual(skill.stages.count(), 3)
        self.assertEqual(stages[0].url_pattern, "/demo/form-a/")
        self.assertEqual(stages[1].url_pattern, "/demo/form-a/education/")
        self.assertEqual(stages[2].url_pattern, "/demo/form-a/review/")
        self.assertEqual(stages[0].exit, "/demo/form-a/education/")
        self.assertTrue(any(item.get("key") == "email" for item in stages[0].split_yours))
        self.assertTrue(any(item.get("key") == "course" for item in stages[1].split_yours))
        self.assertTrue(any(item.get("name") == "why" or "why" in str(item.get("name")) for item in stages[2].split_this_run))

    def test_one_page_trace_stays_one_stage(self):
        events = [
            {"type": "navigate", "url": "http://127.0.0.1:8001/demo/form-b/"},
            {"type": "navigate", "url": "http://127.0.0.1:8001/demo/form-b/"},
            {"type": "input", "name": "University email", "value": "alex@imperial.ac.uk"},
            {"type": "click", "name": "Submit application"},
        ]
        self.assertEqual(len(split_event_slices(events)), 1)
        skill = Skill.objects.create(
            name="Ambassador",
            goal="Apply",
            playbook=["Fill the form"],
            page_kind="student ambassador form",
            start_url="http://127.0.0.1:8001/demo/form-b/",
            source_events=events,
        )
        yours, this_run = fields_from_events(events)
        stages = write_stages_from_events(skill, events, yours, this_run, playbook=skill.playbook)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0].playbook, ["Fill the form"])
        self.assertEqual(stages[0].url_pattern, "/demo/form-b/")

    def test_commit_with_stages_does_not_overwrite_email(self):
        MemoryFact.objects.create(key="email", label="Email", value="old@example.com", category="identity")
        skill = Skill.objects.create(name="x", goal="g", ready=False)
        result = commit_skill(
            skill,
            {
                "overwrite_keys": [],
                "stages": [
                    {
                        "name": "Personal details",
                        "url_pattern": "/demo/form-a/",
                        "page_kind": "personal details",
                        "playbook": ["Fill email"],
                        "yours": [
                            {
                                "key": "email",
                                "label": "University email",
                                "value": "new@example.com",
                                "category": "identity",
                            }
                        ],
                        "this_run": [],
                    }
                ],
            },
        )
        self.assertEqual(MemoryFact.objects.get(key="email").value, "old@example.com")
        self.assertEqual(result["memory"]["conflicts"][0]["proposed"], "new@example.com")
        self.assertEqual(skill.stages.count(), 1)
        self.assertTrue(skill.ready)

    def test_form_a_has_four_pages(self):
        first = self.client.get("/demo/form-a/")
        self.assertContains(first, "/demo/form-a/education/")
        second = self.client.get("/demo/form-a/education/")
        self.assertContains(second, "/demo/form-a/upload/")
        third = self.client.get("/demo/form-a/upload/")
        self.assertContains(third, "/demo/form-a/review/")
        fourth = self.client.get("/demo/form-a/review/")
        self.assertContains(fourth, "Submit application")

    def test_demo_hub_has_two_tracks(self):
        response = self.client.get("/demo/")
        self.assertContains(response, "Demo 1 — Apply")
        self.assertContains(response, "Demo 2 — Send, then post")
        self.assertContains(response, "apply with my CV and transcript, stop before submit")
        self.assertContains(response, "upload this with a suitable caption")
        self.assertContains(response, "/demo/files/maya-chen-cv.docx/")
        self.assertContains(response, "/demo/files/clip-one.mp4/")

    def test_demo_pack_files_download(self):
        from core.demo_pack import DEMO_FILES

        for name in DEMO_FILES:
            response = self.client.get(f"/demo/files/{name}/")
            self.assertEqual(response.status_code, 200, name)
            body = b"".join(response.streaming_content)
            self.assertTrue(len(body) > 0, name)
        missing = self.client.get("/demo/files/not-a-pack-file.txt/")
        self.assertEqual(missing.status_code, 404)

    def test_demo_cv_parses_maya_chen(self):
        from core.demo_pack import PACK_DIR

        text = (PACK_DIR / "maya-chen-cv.txt").read_text(encoding="utf-8")
        found, _skipped = candidates_from_text(text)
        by_key = {item["key"]: item["value"] for item in found}
        self.assertEqual(by_key["first_name"], "Maya")
        self.assertEqual(by_key["last_name"], "Chen")
        self.assertEqual(by_key["school_email"], "maya.chen24@imperial.ac.uk")
        self.assertEqual(by_key["university"], "Imperial College London")
        self.assertEqual(by_key["degree"], "MEng")
        self.assertEqual(by_key["major"], "Computer Science")
        self.assertEqual(by_key["shipping.postcode"], "SW7 2AZ")

    def test_demo_transcript_does_not_treat_college_as_a_name(self):
        from core.demo_pack import PACK_DIR

        text = (PACK_DIR / "maya-chen-transcript.txt").read_text(encoding="utf-8")
        found, _skipped = candidates_from_text(text)
        by_key = {item["key"]: item["value"] for item in found}
        self.assertEqual(by_key["full_name"], "Maya Chen")
        self.assertEqual(by_key["first_name"], "Maya")
        self.assertEqual(by_key["last_name"], "Chen")
        self.assertEqual(by_key["university"], "Imperial College London")
        self.assertNotEqual(by_key.get("full_name"), "Imperial College London")
        self.assertNotEqual(by_key.get("first_name"), "Imperial")

    def test_form_b_has_accept_cookies(self):
        response = self.client.get("/demo/form-b/")
        self.assertContains(response, "Accept cookies")
        self.assertContains(response, "Name")
        self.assertContains(response, "Email address")

    def test_stage_patch_copies_back_onto_a_one_page_skill(self):
        skill = Skill.objects.create(name="x", goal="g", playbook=["old"])
        stage = ensure_one_stage(skill)
        response = self.client.patch(
            f"/api/skills/{skill.id}/stages/{stage.id}/",
            data={"playbook": ["new step"], "page_kind": "checkout"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        skill.refresh_from_db()
        self.assertEqual(skill.playbook, ["new step"])
        self.assertEqual(skill.page_kind, "checkout")


def _docx_upload(lines, name="cv.docx"):
    from docx import Document as Docx

    doc = Docx()
    for line in lines:
        doc.add_paragraph(line)
    buf = BytesIO()
    doc.save(buf)
    return SimpleUploadedFile(
        name,
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


class DocumentApiTests(TestCase):
    def test_upload_stores_under_vault(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.settings(MEDIA_ROOT=folder):
                response = self.client.post(
                    "/api/documents/",
                    data={
                        "title": "Note",
                        "file": SimpleUploadedFile("note.txt", b"hello vault\n", content_type="text/plain"),
                    },
                )
                self.assertEqual(response.status_code, 201)
                payload = response.json()["document"]
                self.assertEqual(payload["title"], "Note")
                self.assertEqual(payload["status"], "extracted")
                self.assertIn("hello vault", payload["extracted_text"])
                listed = self.client.get("/api/documents/")
                self.assertNotIn("extracted_text", listed.json()["documents"][0])
                stored = Path(folder) / "vault" / payload["id"] / "note.txt"
                self.assertTrue(stored.exists())
                downloaded = self.client.get(f"/api/documents/{payload['id']}/file/")
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(b"".join(downloaded.streaming_content), b"hello vault\n")

    def test_upload_proposes_facts_and_skips_a_card(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.settings(MEDIA_ROOT=folder):
                response = self.client.post(
                    "/api/documents/",
                    data={
                        "title": "CV",
                        "file": _docx_upload(
                            [
                                "Alex Example",
                                "MEng Computer Science, Imperial College London, 2024",
                                "alex@imperial.ac.uk",
                                "Card 4111 1111 1111 1111",
                            ]
                        ),
                    },
                )
                self.assertEqual(response.status_code, 201)
                payload = response.json()["document"]
                self.assertEqual(payload["status"], "extracted")
                keys = {item["key"] for item in payload["proposals"]}
                self.assertIn("email", keys)
                self.assertIn("school_email", keys)
                self.assertIn("full_name", keys)
                self.assertIn("first_name", keys)
                self.assertIn("last_name", keys)
                self.assertIn("university", keys)
                self.assertIn("degree", keys)
                self.assertTrue(any(item["confirmed"] is False for item in payload["proposals"]))
                self.assertFalse(MemoryFact.objects.filter(confirmed=True).exists())
                self.assertFalse(any("4111" in (item.get("value") or "") for item in payload["proposals"]))
                self.assertTrue(payload["skipped"])

    def test_accept_writes_facts_reject_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.settings(MEDIA_ROOT=folder):
                uploaded = self.client.post(
                    "/api/documents/",
                    data={"title": "CV", "file": _docx_upload(["Alex Example", "alex@imperial.ac.uk"])},
                )
                doc_id = uploaded.json()["document"]["id"]
                proposals = uploaded.json()["document"]["proposals"]
                email = next(item for item in proposals if item["key"] == "email")
                name = next(item for item in proposals if item["key"] == "full_name")
                accepted = self.client.post(
                    "/api/documents/" + doc_id + "/accept/",
                    data={"fact_ids": [email["id"]]},
                    content_type="application/json",
                )
                self.assertEqual(accepted.status_code, 200)
                self.assertTrue(MemoryFact.objects.get(id=email["id"]).confirmed)
                rejected = self.client.post(
                    "/api/documents/" + doc_id + "/reject/",
                    data={"fact_ids": [name["id"]]},
                    content_type="application/json",
                )
                self.assertEqual(rejected.status_code, 200)
                self.assertFalse(MemoryFact.objects.get(id=name["id"]).confirmed)
                self.assertTrue(MemoryFact.objects.get(id=name["id"]).rejected)
                again = self.client.post(
                    "/api/documents/",
                    data={"title": "CV 2", "file": _docx_upload(["Alex Example", "alex@imperial.ac.uk"])},
                )
                keys = {item["key"] for item in again.json()["document"]["proposals"]}
                self.assertNotIn("email", keys)
                self.assertNotIn("full_name", keys)

    def test_job_attaches_vault_id_without_document_body(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.settings(MEDIA_ROOT=folder):
                uploaded = self.client.post(
                    "/api/documents/",
                    data={
                        "title": "Transcript",
                        "file": _docx_upload(["Alex Example", "MEng Computer Science, Imperial College London"]),
                    },
                )
                doc_id = uploaded.json()["document"]["id"]
                extracted = uploaded.json()["document"]["extracted_text"]
                self.assertIn("MEng Computer Science", extracted)
                created = self.client.post(
                    "/api/jobs/",
                    data={
                        "prompt": "apply with my transcript, stop before submit",
                        "vault_ids": [doc_id],
                        "current_url": "http://127.0.0.1:8001/demo/form-a/",
                    },
                    content_type="application/json",
                )
                self.assertEqual(created.status_code, 201)
                attachment = created.json()["run"]["job"]["attachments"][0]
                self.assertEqual(attachment["document_id"], doc_id)
                self.assertEqual(attachment["title"], "Transcript")
                self.assertNotIn("extracted_text", attachment)
                self.assertNotIn("text", attachment)
                run = Run.objects.get(id=created.json()["run"]["id"])
                blob = json.dumps(start_messages(run))
                self.assertIn(doc_id, blob)
                self.assertNotIn("extracted_text", blob)
                self.assertNotIn('"extracted_text"', json.dumps(run.job.get("attachments") or []))


class PageKindAndCvTests(TestCase):
    def test_cv_text_parses_application_fields(self):
        found, _skipped = candidates_from_text(
            "\n".join(
                [
                    "Alex de Vrieze",
                    "alex.de-vrieze26@imperial.ac.uk",
                    "+44 7700 900123",
                    "MEng Computer Science, Imperial College London, 2024-2028",
                ]
            )
        )
        by_key = {item["key"]: item["value"] for item in found}
        self.assertEqual(by_key["first_name"], "Alex")
        self.assertEqual(by_key["last_name"], "de Vrieze")
        self.assertEqual(by_key["school_email"], "alex.de-vrieze26@imperial.ac.uk")
        self.assertEqual(by_key["university"], "Imperial College London")
        self.assertEqual(by_key["degree"], "MEng")
        self.assertEqual(by_key["major"], "Computer Science")
        self.assertEqual(by_key["study.start_year"], "2024")

    def test_school_email_question_uses_stored_email(self):
        MemoryFact.objects.create(
            key="school_email",
            label="School email",
            value="ada@imperial.ac.uk",
            category="identity",
        )
        fact = lookup_stored_fact(
            "school_email",
            "Your primary college/university/school email",
            "What is your school email?",
        )
        self.assertEqual(fact["value"], "ada@imperial.ac.uk")

    def test_job_uses_unconfirmed_cv_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.settings(MEDIA_ROOT=folder):
                uploaded = self.client.post(
                    "/api/documents/",
                    data={
                        "title": "CV",
                        "file": _docx_upload(
                            [
                                "Ada Example",
                                "ada@imperial.ac.uk",
                                "MEng Mathematics, Imperial College London, 2023-2027",
                            ]
                        ),
                    },
                )
                doc_id = uploaded.json()["document"]["id"]
                job = compile_job_heuristic(
                    "apply to this - https://www.janestreet.com/apply-fttp-ldn/",
                    current_url="https://www.janestreet.com/apply-fttp-ldn/",
                    vault_ids=[doc_id],
                )
                keys = {item["key"] for item in job["memory"]}
                self.assertIn("first_name", keys)
                self.assertIn("school_email", keys)
                self.assertIn("university", keys)
                self.assertFalse(any(item.get("extracted_text") for item in job["attachments"]))

    def test_cookie_banner_is_dismissed_without_a_model_call(self):
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="apply",
            current_url="https://www.janestreet.com/apply-fttp-ldn/",
            checkpoint={"off_script": True},
            job={"start_url": "https://www.janestreet.com/apply-fttp-ldn/", "procedure": {"off_script": True}},
        )
        dump = (
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            "title: Apply\n"
            'button "Reject all" [c1]\n'
            'button "Accept all" [c2]\n'
            'textbox "Email *" [e23]\n'
            "cookie consent overlay\n"
        )
        with patch("core.services.pagekind._classify_haiku", side_effect=AssertionError("heuristic must not call haiku")):
            with patch("core.services.agent._messages_create") as mocked:
                mocked.side_effect = AssertionError("blocker must not call the model")
                payload = apply_stage_turn(
                    run,
                    tool_results=[{"tool_use_id": "local_read", "content": dump}],
                )
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["pending_actions"][0]["name"], "left_click")
        self.assertEqual(payload["pending_actions"][0]["input"]["target"], "c1")

    def test_matching_form_is_expected_without_haiku(self):
        skill = Skill.objects.create(
            name="Internship",
            goal="Fill",
            ready=True,
            start_url="http://127.0.0.1:8001/demo/form-a/",
            site_host="127.0.0.1:8001",
        )
        Stage.objects.create(skill=skill, index=0, name="Personal", url_pattern="/demo/form-a/")
        run = Run.objects.create(skill=skill, kind="skill", current_url="http://127.0.0.1:8001/demo/form-a/")
        page = parse_page(FORM_A_PAGE1)
        with patch("core.services.pagekind._classify_haiku", side_effect=AssertionError("must not call haiku")):
            kind, used = classify_page(run, page)
        self.assertEqual(kind, "expected")
        self.assertFalse(used)

    def test_unsure_page_asks_haiku_once(self):
        run = Run.objects.create(
            kind="job",
            intent="apply",
            current_url="https://jobs.example.edu/about",
            checkpoint={"off_script": True},
            job={"start_url": "https://jobs.example.edu/apply", "procedure": {"off_script": True}},
        )
        page = parse_page('url: https://jobs.example.edu/about\ntitle: About\nlink "Home" [r1]\n')
        self.assertIsNone(classify_page_heuristic(run, page))
        with patch("core.services.pagekind._classify_haiku", return_value="wrong") as mocked:
            kind, used = classify_page(run, page)
        self.assertEqual(kind, "wrong")
        self.assertTrue(used)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual((run.checkpoint or {}).get("classify_calls"), 1)

    def test_application_page_fills_from_cv_facts_and_does_not_submit(self):
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="apply to https://www.janestreet.com/apply-fttp-ldn/",
            current_url="https://www.janestreet.com/apply-fttp-ldn/",
            checkpoint={"off_script": True, "dismissed_blocker": True},
            job={
                "start_url": "https://www.janestreet.com/apply-fttp-ldn/",
                "stop_before": "Submit",
                "procedure": {"status": "learn", "off_script": True},
                "memory": [
                    {"key": "first_name", "label": "First name", "value": "Ada"},
                    {"key": "last_name", "label": "Last name", "value": "Example"},
                    {"key": "email", "label": "Email", "value": "ada@example.com"},
                    {"key": "phone", "label": "Phone", "value": "5550100"},
                ],
            },
        )
        dump = (
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            "title: Apply FTTP London\n"
            'textbox "Legal first name *" [e20]\n'
            'textbox "Legal last name *" [e22]\n'
            'textbox "Email *" [e23]\n'
            'textbox "Email confirmation *" [e24]\n'
            'textbox "Phone *" [e26]\n'
            'button "Submit" [e42]\n'
        )
        with patch("core.services.pagekind._classify_haiku", side_effect=AssertionError("must not call haiku")):
            with patch("core.services.agent._messages_create") as mocked:
                mocked.side_effect = AssertionError("local fill must not call the model")
                payload = apply_stage_turn(
                    run,
                    tool_results=[{"tool_use_id": "local_read", "content": dump}],
                )
        names = [item["name"] for item in payload["pending_actions"]]
        values = [item["input"]["value"] for item in payload["pending_actions"] if item["name"] == "form_input"]
        self.assertEqual(payload["status"], "running")
        self.assertEqual(names, ["form_input", "form_input", "form_input", "form_input", "form_input"])
        self.assertEqual(values, ["Ada", "Example", "ada@example.com", "ada@example.com", "5550100"])
        self.assertNotIn("left_click", names)

    def test_recaptcha_iframe_does_not_steal_the_form(self):
        dump = (
            "frame f0 https://www.janestreet.com/apply-fttp-ldn/\n"
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            "title: Apply FTTP London\n"
            'textbox "Legal first name *" [f0::ref_1]\n'
            'textbox "Legal last name *" [f0::ref_2]\n'
            'textbox "Email *" [f0::ref_3]\n'
            'textbox "Email confirmation *" [f0::ref_4]\n'
            'textbox "Phone *" [f0::ref_5]\n'
            'button "Submit" [f0::ref_6]\n'
            "\n"
            "frame f3 https://www.recaptcha.net/recaptcha/api2/anchor?k=abc\n"
            "url: https://www.recaptcha.net/recaptcha/api2/anchor?k=abc\n"
            'checkbox "I\'m not a robot" [f3::ref_1]\n'
        )
        page = parse_page(dump)
        self.assertEqual(page["url"], "https://www.janestreet.com/apply-fttp-ldn/")
        names = [item["name"] for item in page["controls"]]
        self.assertIn("Legal first name *", names)
        self.assertNotIn("I'm not a robot", names)
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="apply to https://www.janestreet.com/apply-fttp-ldn/",
            current_url="https://www.janestreet.com/apply-fttp-ldn/",
            checkpoint={"off_script": True, "dismissed_blocker": True},
            job={
                "start_url": "https://www.janestreet.com/apply-fttp-ldn/",
                "stop_before": "Submit",
                "procedure": {"status": "learn", "off_script": True},
                "memory": [
                    {"key": "full_name", "label": "Full legal name", "value": "Ada Example"},
                    {"key": "email", "label": "University email", "value": "ada@example.com"},
                    {"key": "phone", "label": "Phone", "value": "5550100"},
                ],
            },
        )
        with patch("core.services.pagekind._classify_haiku", side_effect=AssertionError("must not call haiku")):
            with patch("core.services.agent._messages_create") as mocked:
                mocked.side_effect = AssertionError("local fill must not call the model")
                first = apply_stage_turn(
                    run,
                    tool_results=[{"tool_use_id": "toolu_read", "content": dump}],
                )
                self.assertEqual(run.current_url, "https://www.janestreet.com/apply-fttp-ldn/")
                values = [item["input"]["value"] for item in first["pending_actions"]]
                self.assertEqual(values, ["Ada", "Example", "ada@example.com", "ada@example.com", "5550100"])
                self.assertNotIn("left_click", [item["name"] for item in first["pending_actions"]])
                second = apply_stage_turn(
                    run,
                    tool_results=[{"tool_use_id": "toolu_read2", "content": dump}],
                )
        self.assertIsNone(second)

    def test_turn_fills_the_form_from_a_mixed_dump(self):
        dump = (
            "frame f0 https://www.janestreet.com/apply-fttp-ldn/\n"
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            'textbox "Legal first name *" [f0::ref_1]\n'
            'textbox "Email *" [f0::ref_2]\n'
            'button "Submit" [f0::ref_3]\n'
            "frame f2 https://www.recaptcha.net/recaptcha/api2/anchor\n"
            "url: https://www.recaptcha.net/recaptcha/api2/anchor\n"
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="apply to https://www.janestreet.com/apply-fttp-ldn/",
            current_url="https://www.janestreet.com/apply-fttp-ldn/",
            checkpoint={"off_script": True, "dismissed_blocker": True},
            job={
                "start_url": "https://www.janestreet.com/apply-fttp-ldn/",
                "stop_before": "Submit",
                "procedure": {"status": "learn", "off_script": True},
                "memory": [
                    {"key": "full_name", "label": "Name", "value": "Ada Example"},
                    {"key": "email", "label": "Email", "value": "ada@example.com"},
                ],
            },
        )
        read = _tool_response({"id": "toolu_read", "name": "read_page", "input": {"filter": "interactive"}})
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", return_value=read
        ) as mocked:
            first = turn(run)
            filled = turn(
                run,
                tool_results=[{"tool_use_id": first["pending_actions"][0]["tool_use_id"], "content": dump}],
            )
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(filled["status"], "running")
        self.assertEqual(
            [item["input"]["value"] for item in filled["pending_actions"]],
            ["Ada", "ada@example.com"],
        )
        self.assertEqual(run.current_url, "https://www.janestreet.com/apply-fttp-ldn/")

    def test_local_fill_maps_school_email_label(self):
        page = parse_page(
            'url: https://www.janestreet.com/apply-fttp-ldn/\n'
            'textbox "Your primary college/university/school email" [e30]\n'
            'button "Submit" [e42]\n'
        )
        run = Run.objects.create(kind="job", job={"memory": []})
        actions = local_fill_from_memory(
            run,
            page,
            [{"key": "school_email", "value": "ada@imperial.ac.uk"}],
        )
        self.assertEqual(actions[0]["input"]["value"], "ada@imperial.ac.uk")

    def test_learn_form_a_fills_address_and_clicks_continue(self):
        page = parse_page(FORM_A_APPLY_PAGE1)
        run = Run.objects.create(kind="job", job={"memory": []})
        actions = local_fill_from_memory(
            run,
            page,
            [
                {"key": "full_name", "value": "Maya Chen"},
                {"key": "school_email", "value": "maya.chen24@imperial.ac.uk"},
                {"key": "shipping.line1", "value": "14 Exhibition Road"},
                {"key": "shipping.city", "value": "London"},
                {"key": "shipping.postcode", "value": "SW7 2AZ"},
            ],
        )
        fills = [item for item in actions if item["name"] == "form_input"]
        clicks = [item for item in actions if item["name"] == "left_click"]
        self.assertEqual(
            [item["input"]["value"] for item in fills],
            ["Maya Chen", "maya.chen24@imperial.ac.uk", "14 Exhibition Road", "London", "SW7 2AZ"],
        )
        self.assertEqual(clicks[0]["input"]["target"], "ref_6")

    def test_learn_form_a_education_does_not_skip_the_year_dropdown(self):
        page = parse_page(
            'url: http://127.0.0.1:8001/demo/form-a/education/\n'
            'textbox "Course" [ref_1]\n'
            'combobox "Year of study" [ref_2]\n'
            'link "Continue" [ref_3]\n'
        )
        run = Run.objects.create(kind="job", job={"memory": []})
        actions = local_fill_from_memory(
            run,
            page,
            [{"key": "course", "value": "MEng Computer Science"}],
        )
        self.assertEqual(actions[0]["input"]["value"], "MEng Computer Science")
        self.assertFalse(any(item["name"] == "left_click" for item in actions))

    def test_leftover_dropdowns_are_detected(self):
        dump = (
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            'textbox "Legal first name *" [e20]\n'
            'combobox "Select an option" [e40]\n'
            'button "Submit" [e42]\n'
        )
        self.assertTrue(leftover_work(dump))
        self.assertFalse(
            leftover_work(
                "url: https://www.janestreet.com/apply-fttp-ldn/\n"
                'textbox "Legal first name *" [e20]\n'
                'button "Submit" [e42]\n'
            )
        )
        self.assertTrue(
            leftover_work(
                'url: http://127.0.0.1:8001/demo/form-a/education/\n'
                'textbox "Course" [ref_1] value="MEng Computer Science"\n'
                'combobox "Year of study" [ref_2]\n'
                'link "Continue" [ref_3]\n'
            )
        )
        self.assertFalse(
            leftover_work(
                'url: http://127.0.0.1:8001/demo/form-a/education/\n'
                'textbox "Course" [ref_1] value="MEng Computer Science"\n'
                'combobox "Year of study" [ref_2] value="Year 3"\n'
                'link "Continue" [ref_3]\n'
            )
        )
        self.assertTrue(leftover_work(FORM_A_UPLOAD_PAGE))
        self.assertFalse(
            leftover_work(
                'url: http://127.0.0.1:8001/demo/form-a/upload/\n'
                'file "Transcript" [ref_1] value="maya-chen-transcript.docx"\n'
                'link "Continue" [ref_2]\n'
            )
        )

    def test_learn_job_clicks_continue_once_the_page_is_filled(self):
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="apply with my CV and transcript",
            current_url="http://127.0.0.1:8001/demo/form-a/upload/",
            checkpoint={
                "off_script": True,
                "filled_url": "http://127.0.0.1:8001/demo/form-a/upload/",
            },
            job={"procedure": {"status": "learn", "off_script": True}, "memory": []},
        )
        dump = (
            'url: http://127.0.0.1:8001/demo/form-a/upload/\n'
            'file "Transcript" [ref_1] value="maya-chen-transcript.docx"\n'
            'link "Continue" [ref_2]\n'
        )
        with patch("core.services.agent._messages_create") as mocked:
            mocked.side_effect = AssertionError("a filled file page must click Continue locally")
            payload = apply_stage_turn(
                run,
                tool_results=[{"tool_use_id": "local_vault", "content": dump}],
            )
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["pending_actions"][0]["name"], "left_click")
        self.assertEqual(payload["pending_actions"][0]["input"]["target"], "ref_2")

    def test_stuck_dropdowns_call_the_model_instead_of_completing(self):
        dump = (
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            "title: Apply FTTP London\n"
            'textbox "Legal first name *" [e20]\n'
            'textbox "Email *" [e23]\n'
            'combobox "Select an option" [e40]\n'
            'combobox "Select an option" [e41]\n'
            'button "Submit" [e42]\n'
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="apply to https://www.janestreet.com/apply-fttp-ldn/",
            current_url="https://www.janestreet.com/apply-fttp-ldn/",
            checkpoint={"off_script": True, "dismissed_blocker": True},
            job={
                "start_url": "https://www.janestreet.com/apply-fttp-ldn/",
                "stop_before": "Submit",
                "procedure": {"status": "learn", "off_script": True},
                "memory": [
                    {"key": "first_name", "label": "First name", "value": "Ada"},
                    {"key": "email", "label": "Email", "value": "ada@example.com"},
                    {"key": "university", "label": "University", "value": "Example University"},
                ],
            },
        )
        first = apply_stage_turn(
            run,
            tool_results=[{"tool_use_id": "local_read", "content": dump}],
        )
        names = [item["name"] for item in first["pending_actions"]]
        self.assertIn("form_input", names)
        self.assertNotIn("read_page", names)
        self.assertNotIn("left_click", names)
        complete = _tool_response(
            {"id": "toolu_done", "name": "mark_goal_complete", "input": {"summary": "Filled identity"}}
        )
        click = _tool_response({"id": "toolu_dd", "name": "left_click", "input": {"target": "e40"}})
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", side_effect=[complete, click]
        ) as mocked:
            results = []
            for item in first["pending_actions"]:
                if item["name"] == "form_input":
                    results.append({"tool_use_id": item["tool_use_id"], "content": "Filled"})
                else:
                    results.append({"tool_use_id": item["tool_use_id"], "content": dump})
            payload = turn(run, tool_results=results)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["pending_actions"][0]["name"], "left_click")
        self.assertEqual(payload["pending_actions"][0]["input"]["target"], "e40")
        briefing = json.dumps(mocked.call_args_list[0][0][1]["messages"])
        self.assertIn("Select an option", briefing)
        self.assertNotIn("left_click", names)

    def test_empty_pending_turn_uses_last_dump_for_the_model(self):
        dump = (
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            'combobox "Select an option" [e40]\n'
            'button "Submit" [e42]\n'
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="apply to https://www.janestreet.com/apply-fttp-ldn/",
            current_url="https://www.janestreet.com/apply-fttp-ldn/",
            checkpoint={
                "off_script": True,
                "dismissed_blocker": True,
                "last_dump": dump,
                "filled_url": "https://www.janestreet.com/apply-fttp-ldn/",
            },
            job={
                "start_url": "https://www.janestreet.com/apply-fttp-ldn/",
                "stop_before": "Submit",
                "procedure": {"status": "learn", "off_script": True},
                "memory": [{"key": "university", "label": "University", "value": "Example University"}],
            },
        )
        click = _tool_response({"id": "toolu_dd", "name": "left_click", "input": {"target": "e40"}})
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", return_value=click
        ) as mocked:
            payload = turn(run, tool_results=[])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["pending_actions"][0]["name"], "left_click")
        briefing = json.dumps(mocked.call_args[0][1]["messages"])
        self.assertIn("Select an option", briefing)

    def test_off_script_wrong_page_still_calls_the_model(self):
        dump = (
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            "title: Apply\n"
            'combobox "Select an option" [e40]\n'
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="apply to https://www.janestreet.com/apply-fttp-ldn/",
            current_url="https://www.janestreet.com/apply-fttp-ldn/",
            checkpoint={"off_script": True},
            job={
                "start_url": "https://www.janestreet.com/join-us/",
                "procedure": {"status": "learn", "off_script": True},
            },
        )
        click = _tool_response({"id": "toolu_dd", "name": "left_click", "input": {"target": "e40"}})
        with patch("core.services.pagekind.classify_page", return_value=("wrong", False)):
            with patch("core.services.agent._client", return_value=object()), patch(
                "core.services.agent._messages_create", return_value=click
            ) as mocked:
                payload = turn(
                    run,
                    tool_results=[{"tool_use_id": "local_read", "content": dump}],
                )
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(payload["status"], "running")
        self.assertNotEqual(payload["status"], "paused")
        self.assertEqual(payload["pending_actions"][0]["name"], "left_click")

    def test_nav_chrome_does_not_hide_form_controls(self):
        dump = (
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            "title: Apply FTTP London\n"
            + "\n".join(['link "WHO WE ARE" [n%s] [offscreen]' % i for i in range(80)])
            + "\n"
            'textbox "Legal first name *" [e20]\n'
            'button "Select an option" [e40]\n'
            'button "Submit" [e42]\n'
        )
        compact = useful_page_dump(dump)
        self.assertIn("Legal first name", compact)
        self.assertIn("Select an option", compact)
        self.assertNotIn("WHO WE ARE", compact)

    def test_known_skill_fills_even_when_memory_keys_mismatch(self):
        skill = Skill.objects.create(
            name="Jane Street",
            goal="Apply",
            start_url="https://www.janestreet.com/apply-fttp-ldn/",
            site_host="www.janestreet.com",
            ready=True,
        )
        Stage.objects.create(
            skill=skill,
            index=0,
            name="Apply",
            url_pattern="/apply-fttp-ldn/",
            demonstration=[
                {
                    "action": "fill",
                    "field": "Legal first name *",
                    "source": "memory",
                    "key": "legal_first_name_first_name",
                    "demo_value": "Ada",
                },
                {"action": "click", "field": "Select an option"},
                {"action": "click", "field": "Submit"},
            ],
            memory_keys=["legal_first_name_first_name"],
        )
        run = Run.objects.create(
            skill=skill,
            kind="job",
            status="running",
            current_url="https://www.janestreet.com/apply-fttp-ldn/",
            job={"procedure": {"status": "known", "off_script": False}},
        )
        dump = (
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            'textbox "Legal first name *" [e20]\n'
            'button "Select an option" [e40]\n'
            'button "Yes, I am available." [e41]\n'
            'button "Submit" [e42]\n'
        )
        MemoryFact.objects.create(key="first_name", label="First name", value="Ada", category="identity")
        with patch("core.services.pagekind._classify_haiku", side_effect=AssertionError("must not call haiku")):
            with patch("core.services.agent._messages_create") as mocked:
                mocked.side_effect = AssertionError("local fill must not call the model")
                payload = apply_stage_turn(
                    run,
                    tool_results=[{"tool_use_id": "local_read", "content": dump}],
                )
        names = [item["name"] for item in payload["pending_actions"]]
        values = [item["input"]["value"] for item in payload["pending_actions"] if item["name"] == "form_input"]
        self.assertEqual(payload["status"], "running")
        self.assertIn("form_input", names)
        self.assertEqual(values, ["Ada"])
        self.assertNotIn("read_page", names)
        self.assertNotIn("left_click", names)

    def test_look_only_model_turn_is_nudged_to_click(self):
        dump = (
            "url: https://www.janestreet.com/apply-fttp-ldn/\n"
            'button "Select an option" [e40]\n'
            'button "Submit" [e42]\n'
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="apply to https://www.janestreet.com/apply-fttp-ldn/",
            current_url="https://www.janestreet.com/apply-fttp-ldn/",
            checkpoint={"off_script": True, "last_dump": dump},
            job={
                "start_url": "https://www.janestreet.com/apply-fttp-ldn/",
                "stop_before": "Submit",
                "procedure": {"status": "learn", "off_script": True},
            },
        )
        look = _tool_response(
            {"id": "toolu_s", "name": "screenshot", "input": {}},
            {"id": "toolu_r", "name": "scroll_to", "input": {"target": "e40"}},
        )
        click = _tool_response({"id": "toolu_dd", "name": "left_click", "input": {"target": "e40"}})
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", side_effect=[look, click]
        ) as mocked:
            payload = turn(run, tool_results=[])
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["pending_actions"][0]["name"], "left_click")
        self.assertEqual(payload["pending_actions"][0]["input"]["target"], "e40")
        second_messages = mocked.call_args_list[1][0][1]["messages"]
        blob = json.dumps(second_messages)
        self.assertIn('"type": "tool_result"', blob)
        self.assertIn("toolu_s", blob)
        self.assertIn("toolu_r", blob)


FORM_A_PAGE1 = """url: http://127.0.0.1:8001/demo/form-a/
title: Imperial internship application
frame: top
textbox "Full legal name" [ref_1]
textbox "University email" [ref_2]
link "Continue" [ref_3]
"""

FORM_A_PAGE2 = """url: http://127.0.0.1:8001/demo/form-a/education/
title: Imperial internship — education
frame: top
textbox "Course" [ref_1]
combobox "Year of study" [ref_2]
link "Continue" [ref_3]
"""

LOGIN_PAGE = """url: https://accounts.example.com/signin
title: Sign in
frame: top
textbox "Email" [ref_1]
textbox "Password" [ref_2]
button "Sign in" [ref_3]
"""


def _form_a_skill():
    skill = Skill.objects.create(
        name="Internship",
        goal="Apply for the internship",
        ready=True,
        start_url="http://127.0.0.1:8001/demo/form-a/",
        site_host="127.0.0.1:8001",
        page_kind="personal details",
        playbook=["Fill name", "Continue", "Fill course", "Continue", "Fill why"],
        memory_keys=["full_name", "email", "course"],
    )
    Stage.objects.create(
        skill=skill,
        index=0,
        name="Personal details",
        url_pattern="/demo/form-a/",
        page_kind="personal details",
        playbook=["Fill name", "Fill email", "Continue"],
        demonstration=[
            {"action": "fill", "field": "Full legal name", "source": "memory", "key": "full_name", "demo_value": "Alex Example"},
            {"action": "fill", "field": "University email", "source": "memory", "key": "email", "demo_value": "a@x.com"},
            {"action": "click", "field": "Continue"},
        ],
        memory_keys=["full_name", "email"],
        exit="/demo/form-a/education/",
    )
    Stage.objects.create(
        skill=skill,
        index=1,
        name="Education history",
        url_pattern="/demo/form-a/education/",
        page_kind="education history",
        playbook=["Fill course", "Continue"],
        demonstration=[
            {"action": "fill", "field": "Course", "source": "memory", "key": "course", "demo_value": "Computing"},
            {"action": "click", "field": "Continue"},
        ],
        memory_keys=["course"],
        exit="/demo/form-a/review/",
    )
    Stage.objects.create(
        skill=skill,
        index=2,
        name="Review",
        url_pattern="/demo/form-a/review/",
        page_kind="application review",
        playbook=["Fill why", "Stop before Submit"],
        demonstration=[
            {"action": "fill", "field": "Why this internship", "source": "input", "key": "why", "demo_value": "I like compilers"},
            {"action": "click", "field": "Submit application"},
        ],
        memory_keys=[],
        exit="",
    )
    return skill


class StageRunTests(TestCase):
    def setUp(self):
        MemoryFact.objects.create(key="full_name", label="Name", value="Alex Example", category="identity")
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        MemoryFact.objects.create(key="course", label="Course", value="Computing", category="work")

    def test_stage_advances_on_the_new_demo_pages(self):
        skill = _form_a_skill()
        run = Run.objects.create(
            skill=skill,
            kind="skill",
            status="running",
            current_url="http://127.0.0.1:8001/demo/form-a/",
            checkpoint={"filled_stage": 0},
        )
        with patch("core.services.agent._messages_create") as mocked:
            mocked.side_effect = AssertionError("changing URL must not call the model")
            payload = turn(run, current_url="http://127.0.0.1:8001/demo/form-a/education/")
        self.assertEqual(payload["stage_index"], 1)
        self.assertEqual(payload["stage"]["name"], "Education history")
        self.assertEqual(payload["pending_actions"][0]["name"], "read_page")
        self.assertEqual(model_call_count(run), 0)

    def test_resume_does_not_replay_stage_0(self):
        skill = _form_a_skill()
        run = Run.objects.create(
            skill=skill,
            kind="skill",
            status="running",
            current_url="http://127.0.0.1:8001/demo/form-a/education/",
            stage_index=1,
            checkpoint={"stage_index": 1, "filled_stage": 0, "finished": [0]},
        )
        with patch("core.services.agent._messages_create") as mocked:
            mocked.side_effect = AssertionError("resume must not call the model to re-read stage 0")
            payload = turn(run)
        self.assertEqual(payload["stage_index"], 1)
        self.assertTrue(payload["pending_actions"])
        self.assertEqual(payload["pending_actions"][0]["name"], "read_page")
        briefing = start_messages(run)
        text = briefing[0]["content"][0]["text"]
        self.assertIn("Education history", text)
        self.assertIn("Course", text)
        self.assertNotIn("Full legal name", text)
        self.assertNotIn("University email", text)

    def test_a_later_sell_url_on_the_same_site_is_not_the_wrong_page(self):
        skill = Skill.objects.create(
            name="List an item on eBay",
            ready=True,
            start_url="https://www.ebay.co.uk/sl/prelist/suggest",
            site_host="www.ebay.co.uk",
        )
        Stage.objects.create(
            skill=skill,
            index=0,
            name="Suggest",
            url_pattern="/sl/prelist/suggest",
            page_kind="eBay sell flow",
        )
        run = Run.objects.create(
            skill=skill,
            kind="skill",
            status="running",
            current_url="https://www.ebay.co.uk/lstng?draftId=1",
        )
        self.assertFalse(unexpected_page(run, run.current_url))
        self.assertNotEqual(
            classify_page_heuristic(run, {"url": run.current_url, "fields": [], "buttons": []}),
            "wrong",
        )
        self.assertTrue(unexpected_page(run, "https://www.vinted.co.uk/items/new"))

    def test_replay_does_not_open_the_photo_picker(self):
        skill = Skill.objects.create(
            name="Vinted",
            ready=True,
            start_url="https://www.vinted.co.uk/items/new",
            site_host="www.vinted.co.uk",
        )
        stage = Stage.objects.create(
            skill=skill,
            index=0,
            demonstration=[
                {
                    "action": "fill",
                    "field": "Tell buyers what you're selling",
                    "source": "input",
                    "key": "title",
                    "demo_value": "Blue mug",
                },
                {
                    "action": "fill",
                    "field": "Upload photos",
                    "source": "input",
                    "key": "photo",
                    "demo_value": "C:\\fakepath\\listing-mug.jpg",
                },
                {"action": "click", "field": "Upload photos"},
                {"action": "click", "field": "Select a brand"},
            ],
        )
        page = parse_page(
            "url: https://www.vinted.co.uk/items/new\n"
            'textbox "Tell buyers what you\'re selling" [ref_1]\n'
            'button "Upload photos" [ref_2]\n'
            'button "Select a brand" [ref_3]\n'
        )
        run = Run.objects.create(
            skill=skill,
            kind="skill",
            inputs={"title": "Blue mug", "photo": "C:\\fakepath\\listing-mug.jpg"},
        )
        actions, _missing = local_actions(run, stage, page, [])
        targets = [(item["name"], (item.get("input") or {}).get("target")) for item in actions]
        self.assertIn(("form_input", "ref_1"), targets)
        self.assertNotIn(("form_input", "ref_2"), targets)
        self.assertNotIn(("left_click", "ref_2"), targets)
        self.assertIn(("left_click", "ref_3"), targets)

    def test_dropdown_option_is_clicked_on_the_next_pass_not_skipped(self):
        skill = Skill.objects.create(name="Vinted", ready=True, start_url="https://www.vinted.co.uk/items/new")
        stage = Stage.objects.create(
            skill=skill,
            index=0,
            demonstration=[
                {"action": "click", "field": "Select a brand"},
                {"action": "click", "field": "No Label"},
            ],
        )
        run = Run.objects.create(skill=skill, kind="skill")
        closed = parse_page(
            "url: https://www.vinted.co.uk/items/new\n" 'button "Select a brand" [ref_1]\n'
        )
        first, _missing = local_actions(run, stage, closed, [])
        self.assertEqual([item["input"]["target"] for item in first], ["ref_1"])
        opened = parse_page(
            "url: https://www.vinted.co.uk/items/new\n"
            'button "Select a brand" [ref_1]\n'
            'button "No Label" [ref_2]\n'
        )
        second, _missing = local_actions(run, stage, opened, [])
        self.assertEqual([item["input"]["target"] for item in second], ["ref_2"])

    def test_publish_stays_blocked(self):
        self.assertTrue(is_dangerous_name("Publish"))
        self.assertTrue(is_dangerous_name("Submit application"))
        self.assertFalse(is_dangerous_name("Continue"))
        skill = _form_a_skill()
        stage = skill.stages.get(index=2)
        page = parse_page(
            'url: http://127.0.0.1:8001/demo/form-a/review/\n'
            'textbox "Why this internship" [ref_1]\n'
            'button "Submit application" [ref_2]\n'
            'button "Publish" [ref_3]\n'
        )
        run = Run.objects.create(skill=skill, kind="skill", inputs={"why": "I like compilers"})
        actions, missing = local_actions(run, stage, page, [])
        names = [item["name"] for item in actions]
        self.assertIn("form_input", names)
        self.assertNotIn("left_click", names)

    def test_filled_page_continues_without_asking(self):
        skill = _form_a_skill()
        run = Run.objects.create(
            skill=skill,
            kind="skill",
            status="running",
            current_url="http://127.0.0.1:8001/demo/form-a/",
            stage_index=0,
            checkpoint={"filled_stage": 0},
        )
        with patch("core.services.agent._messages_create") as mocked:
            mocked.side_effect = AssertionError("continuing must not call the model")
            payload = turn(run, current_url="http://127.0.0.1:8001/demo/form-a/")
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["pending_actions"][0]["name"], "navigate")
        self.assertIn("/demo/form-a/education/", payload["pending_actions"][0]["input"]["url"])
        self.assertNotEqual((payload.get("prompt") or {}).get("kind"), "continue")

    def test_absent_demo_label_still_clicks_continue(self):
        skill = _form_a_skill()
        stage = skill.stages.get(index=0)
        stage.demonstration = list(stage.demonstration) + [
            {"action": "fill", "field": "Nickname", "source": "memory", "key": "nickname", "demo_value": "Al"},
        ]
        stage.save()
        page = parse_page(
            'url: http://127.0.0.1:8001/demo/form-a/\n'
            'textbox "Full legal name" [ref_1]\n'
            'textbox "University email" [ref_2]\n'
            'link "Continue" [ref_3]\n'
        )
        run = Run.objects.create(skill=skill, kind="skill")
        memory = [
            {"key": "full_name", "value": "Alex Example"},
            {"key": "email", "value": "a@x.com"},
        ]
        actions, missing = local_actions(run, stage, page, memory)
        self.assertEqual(missing, [])
        self.assertEqual(
            [item["name"] for item in actions],
            ["form_input", "form_input", "left_click"],
        )

    def test_continue_question_does_not_pause_the_run(self):
        run = Run.objects.create(
            kind="attempt",
            status="running",
            intent="Fill the form",
            current_url="http://127.0.0.1:8001/demo/form-b/",
        )
        reply = _tool_response(
            {"id": "toolu_fill", "name": "form_input", "input": {"target": "ref_1", "value": "Alex"}},
            {"id": "toolu_ask", "name": "request_user_confirm", "input": {"summary": "Continue to the next page?"}},
        )
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", return_value=reply
        ):
            payload = turn(run)
        self.assertEqual(payload["status"], "running")
        self.assertEqual([item["name"] for item in payload["pending_actions"]], ["form_input"])

    def test_submit_confirm_waits_until_the_fills_have_run(self):
        run = Run.objects.create(
            kind="attempt",
            status="running",
            intent="Fill the form",
            current_url="http://127.0.0.1:8001/demo/form-b/",
        )
        reply = _tool_response(
            {"id": "toolu_fill", "name": "form_input", "input": {"target": "ref_1", "value": "Alex"}},
            {"id": "toolu_pay", "name": "request_user_confirm", "input": {"summary": "Submit the application"}},
        )
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", return_value=reply
        ) as mocked:
            first = turn(run)
            self.assertEqual(first["status"], "running")
            self.assertEqual([item["name"] for item in first["pending_actions"]], ["form_input"])
            second = turn(
                run,
                tool_results=[{"tool_use_id": first["pending_actions"][0]["tool_use_id"], "content": "Filled"}],
            )
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(second["status"], "awaiting_confirm")
        self.assertIn("Submit", second["confirm_summary"])

    def test_finished_skill_starts_the_next_one(self):
        first = _form_a_skill()
        second = Skill.objects.create(
            name="Parcel",
            goal="Send a parcel",
            ready=True,
            start_url="http://127.0.0.1:8001/demo/parcel/",
            site_host="127.0.0.1:8001",
        )
        Stage.objects.create(
            skill=second,
            index=0,
            name="Address",
            url_pattern="/demo/parcel/",
            demonstration=[
                {"action": "fill", "field": "Full name", "source": "memory", "key": "full_name", "demo_value": "Alex"},
            ],
        )
        run = Run.objects.create(
            skill=first,
            kind="job",
            status="running",
            current_url="http://127.0.0.1:8001/demo/form-a/review/",
            stage_index=2,
            checkpoint={"filled_stage": 2, "skill_queue": [str(second.id)]},
            job={"procedure": {"status": "sequence"}},
        )
        with patch("core.services.agent._messages_create") as mocked:
            mocked.side_effect = AssertionError("the next skill must start without a model call")
            payload = turn(run, current_url="http://127.0.0.1:8001/demo/form-a/review/")
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["skill_id"], str(second.id))
        self.assertEqual(payload["pending_actions"][0]["name"], "navigate")
        self.assertIn("/demo/parcel/", payload["pending_actions"][0]["input"]["url"])

    def test_known_one_page_run_does_not_call_the_model_when_labels_match(self):
        skill = Skill.objects.create(
            name="Ambassador",
            goal="Fill the form",
            ready=True,
            start_url="http://127.0.0.1:8001/demo/form-b/",
            site_host="127.0.0.1:8001",
        )
        Stage.objects.create(
            skill=skill,
            index=0,
            name="Intake",
            url_pattern="/demo/form-b/",
            demonstration=[
                {"action": "fill", "field": "Email address", "source": "memory", "key": "email", "demo_value": "a@x.com"},
                {"action": "click", "field": "Send intake form"},
            ],
            memory_keys=["email"],
        )
        run = Run.objects.create(
            skill=skill,
            kind="skill",
            status="running",
            current_url="http://127.0.0.1:8001/demo/form-b/",
        )
        dump = (
            "url: http://127.0.0.1:8001/demo/form-b/\n"
            'textbox "Email address" [ref_1]\n'
            'button "Send intake form" [ref_2]\n'
        )
        with patch("core.services.agent._messages_create") as mocked:
            mocked.side_effect = AssertionError("matching labels must fill locally")
            first = turn(run)
            self.assertEqual(first["pending_actions"][0]["name"], "read_page")
            second = turn(
                run,
                tool_results=[
                    {
                        "tool_use_id": first["pending_actions"][0]["tool_use_id"],
                        "content": dump,
                    }
                ],
            )
        self.assertEqual(model_call_count(run), 0)
        fills = [item for item in second["pending_actions"] if item["name"] == "form_input"]
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["input"]["value"], "a@x.com")
        clicks = [item for item in second["pending_actions"] if item["name"] == "left_click"]
        self.assertEqual(clicks, [])

    def test_turn_payload_has_no_document_body_or_earlier_screenshot(self):
        first = {
            "role": "user",
            "content": [
                {"type": "text", "text": json.dumps({"extracted_text": "MEng Computer Science from the CV"})},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAOLD"}},
            ],
        }
        second = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": FORM_A_PAGE1 + "x" * 500,
                }
            ],
        }
        third = {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "Filled email"}],
        }
        original = [first, {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}, second, third]
        compacted = compact_messages(original)
        self.assertLess(len(json.dumps(compacted)), len(json.dumps(original)))
        self.assertFalse(payload_has_earlier_screenshot(compacted))
        self.assertFalse(payload_has_document_body(compacted))
        blob = json.dumps(compacted)
        self.assertNotIn("AAAOLD", blob)
        self.assertNotIn("MEng Computer Science from the CV", blob)

    def test_login_wall_pauses_without_storing_cookies(self):
        skill = _form_a_skill()
        run = Run.objects.create(
            skill=skill,
            kind="skill",
            status="running",
            current_url="http://127.0.0.1:8001/demo/form-a/",
        )
        with patch("core.services.agent._messages_create") as mocked:
            mocked.side_effect = AssertionError("login must not call the model")
            first = turn(run)
            payload = turn(
                run,
                tool_results=[{"tool_use_id": first["pending_actions"][0]["tool_use_id"], "content": LOGIN_PAGE}],
            )
        self.assertEqual(payload["status"], "paused")
        self.assertEqual(payload["prompt"]["kind"], "login")
        self.assertIn("Sign in", payload["prompt"]["summary"])
        self.assertNotIn("set-cookie", json.dumps(run.checkpoint).lower())
        self.assertNotIn("cookie", json.dumps(run.checkpoint).lower())
        self.assertEqual(run.checkpoint.get("stage_index"), 0)

    def test_confirm_after_local_fill_does_not_click_submit(self):
        skill = Skill.objects.create(name="One", goal="Fill", ready=True, start_url="http://127.0.0.1:8001/demo/form-b/")
        Stage.objects.create(
            skill=skill,
            index=0,
            name="Intake",
            url_pattern="/demo/form-b/",
            demonstration=[
                {"action": "fill", "field": "Email address", "source": "memory", "key": "email", "demo_value": "a@x.com"},
                {"action": "click", "field": "Send intake form"},
            ],
            memory_keys=["email"],
        )
        run = Run.objects.create(skill=skill, kind="skill", status="running", current_url="http://127.0.0.1:8001/demo/form-b/")
        dump = 'url: http://127.0.0.1:8001/demo/form-b/\ntextbox "Email address" [ref_1]\nbutton "Send intake form" [ref_2]\n'
        with patch("core.services.agent._messages_create") as mocked:
            mocked.side_effect = AssertionError("local fill must not call the model")
            first = turn(run)
            filled = turn(run, tool_results=[{"tool_use_id": first["pending_actions"][0]["tool_use_id"], "content": dump}])
            done = turn(
                run,
                tool_results=[{"tool_use_id": item["tool_use_id"], "content": "Filled"} for item in filled["pending_actions"]],
            )
        self.assertEqual(done["status"], "awaiting_confirm")
        approved = resolve_confirm(run, True)
        self.assertEqual(approved["status"], "completed")
        self.assertIn("Submit/Pay", approved["outcome"])


FORM_A_APPLY_PAGE1 = """url: http://127.0.0.1:8001/demo/form-a/
title: Imperial internship application
frame: top
textbox "Full legal name" [ref_1]
textbox "University email" [ref_2]
textbox "Address line 1" [ref_3]
textbox "City" [ref_4]
textbox "Postcode" [ref_5]
link "Continue" [ref_6]
"""

FORM_A_UPLOAD_PAGE = """url: http://127.0.0.1:8001/demo/form-a/upload/
title: Imperial internship — upload
frame: top
file "Transcript" [ref_1]
link "Continue" [ref_2]
"""

FORM_A_REVIEW_PAGE = """url: http://127.0.0.1:8001/demo/form-a/review/
title: Imperial internship — review
frame: top
textbox "Why this internship" [ref_1]
button "Submit application" [ref_2]
"""


def _form_a_four_skill():
    skill = Skill.objects.create(
        name="Internship",
        goal="Apply for the internship",
        ready=True,
        start_url="http://127.0.0.1:8001/demo/form-a/",
        site_host="127.0.0.1:8001",
        page_kind="university internship application",
        playbook=["Fill personal details", "Fill education", "Attach transcript", "Stop before Submit"],
        memory_keys=["full_name", "email", "shipping.line1", "shipping.city", "shipping.postcode", "course"],
    )
    Stage.objects.create(
        skill=skill,
        index=0,
        name="Personal details",
        url_pattern="/demo/form-a/",
        page_kind="personal details",
        playbook=["Fill name, email, and address", "Continue"],
        demonstration=[
            {"action": "fill", "field": "Full legal name", "source": "memory", "key": "full_name", "demo_value": "Alex Example"},
            {"action": "fill", "field": "University email", "source": "memory", "key": "email", "demo_value": "a@x.com"},
            {"action": "fill", "field": "Address line 1", "source": "memory", "key": "shipping.line1", "demo_value": "1 Exhibition Rd"},
            {"action": "fill", "field": "City", "source": "memory", "key": "shipping.city", "demo_value": "London"},
            {"action": "fill", "field": "Postcode", "source": "memory", "key": "shipping.postcode", "demo_value": "SW7 2AZ"},
            {"action": "click", "field": "Continue"},
        ],
        memory_keys=["full_name", "email", "shipping.line1", "shipping.city", "shipping.postcode"],
        exit="/demo/form-a/education/",
    )
    Stage.objects.create(
        skill=skill,
        index=1,
        name="Education history",
        url_pattern="/demo/form-a/education/",
        page_kind="education history",
        playbook=["Fill course and year from the CV", "Continue"],
        demonstration=[
            {"action": "fill", "field": "Course", "source": "memory", "key": "course", "demo_value": "Computing"},
            {"action": "fill", "field": "Year of study", "source": "memory", "key": "study.year", "demo_value": "Year 3"},
            {"action": "click", "field": "Continue"},
        ],
        memory_keys=["course", "study.year"],
        exit="/demo/form-a/upload/",
    )
    Stage.objects.create(
        skill=skill,
        index=2,
        name="Transcript upload",
        url_pattern="/demo/form-a/upload/",
        page_kind="transcript upload",
        playbook=["Attach the transcript", "Continue"],
        demonstration=[{"action": "click", "field": "Continue"}],
        memory_keys=[],
        exit="/demo/form-a/review/",
    )
    Stage.objects.create(
        skill=skill,
        index=3,
        name="Review",
        url_pattern="/demo/form-a/review/",
        page_kind="application review",
        playbook=["Leave why blank", "Stop before Submit"],
        demonstration=[
            {"action": "fill", "field": "Why this internship", "source": "input", "key": "why", "demo_value": ""},
            {"action": "click", "field": "Submit application"},
        ],
        memory_keys=[],
        exit="",
    )
    return skill


def _tool_response(*tools):
    content = []
    for item in tools:
        content.append(
            SimpleNamespace(
                type="tool_use",
                id=item["id"],
                name=item["name"],
                input=item["input"],
                toolset_name=item.get("toolset_name", "browser"),
            )
        )
    return SimpleNamespace(content=content, stop_reason="tool_use")


class ApplyJobStackTests(TestCase):
    def setUp(self):
        MemoryFact.objects.create(key="full_name", label="Name", value="Alex Example", category="identity")
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        MemoryFact.objects.create(
            key="shipping.line1", label="Address line 1", value="1 Exhibition Rd", category="shipping"
        )
        MemoryFact.objects.create(key="shipping.city", label="City", value="London", category="shipping")
        MemoryFact.objects.create(key="shipping.postcode", label="Postcode", value="SW7 2AZ", category="shipping")
        MemoryFact.objects.create(
            key="course",
            label="Course",
            value="Computing",
            category="work",
            source="file",
            source_quote="MEng Computer Science, Imperial College London, 2024",
        )
        MemoryFact.objects.create(
            key="study.year",
            label="Year of study",
            value="2024",
            category="study",
            confirmed=False,
            source="file",
            source_quote="MEng Computer Science, Imperial College London, 2024",
        )
        self.skill = _form_a_four_skill()

    def _upload_transcript(self, folder):
        with self.settings(MEDIA_ROOT=folder):
            uploaded = self.client.post(
                "/api/documents/",
                data={
                    "title": "Transcript",
                    "file": _docx_upload(["Alex Example", "Year 3 Computing transcript"], name="transcript.docx"),
                },
            )
        self.assertEqual(uploaded.status_code, 201)
        return uploaded.json()["document"]["id"]

    def test_apply_prompt_compiles_procedure_facts_and_files(self):
        with tempfile.TemporaryDirectory() as folder:
            doc_id = self._upload_transcript(folder)
            with self.settings(MEDIA_ROOT=folder):
                response = self.client.post(
                    "/api/jobs/",
                    data={
                        "prompt": "apply with my CV and transcript, stop before submit",
                        "vault_ids": [doc_id],
                        "current_url": "http://127.0.0.1:8001/demo/",
                    },
                    content_type="application/json",
                )
        self.assertEqual(response.status_code, 201)
        job = response.json()["run"]["job"]
        self.assertEqual(job["procedure"]["status"], "known")
        self.assertEqual(job["procedure"]["skill_name"], "Internship")
        self.assertEqual(
            job["procedure"]["stages"],
            ["Personal details", "Education history", "Transcript upload", "Review"],
        )
        self.assertEqual(job["stop_before"], "Submit")
        self.assertIn("/demo/form-a/", job["start_url"])
        keys = {item["key"] for item in job["memory"]}
        self.assertIn("full_name", keys)
        self.assertIn("email", keys)
        self.assertIn("shipping.line1", keys)
        self.assertIn("course", keys)
        self.assertNotIn("study.year", keys)
        self.assertEqual(job["attachments"][0]["document_id"], doc_id)
        self.assertEqual(job["attachments"][0]["title"], "Transcript")
        self.assertNotIn("extracted_text", job["attachments"][0])

    def test_apply_job_hits_the_call_budget(self):
        education_reply = _tool_response(
            {
                "id": "toolu_year",
                "name": "form_input",
                "input": {"target": "ref_2", "value": "Year 3"},
            },
            {
                "id": "toolu_course",
                "name": "form_input",
                "input": {"target": "ref_1", "value": "Computing"},
            },
            {
                "id": "toolu_edu_continue",
                "name": "left_click",
                "input": {"target": "ref_3"},
            },
        )

        def fake_create(_client, kwargs):
            blob = json.dumps(kwargs.get("messages") or [])
            self.assertNotIn("MEng Computer Science from the CV", blob)
            if fake_create.calls:
                raise AssertionError("education may call the model once; later stages must stay local")
            fake_create.calls += 1
            return education_reply

        fake_create.calls = 0

        with tempfile.TemporaryDirectory() as folder:
            doc_id = self._upload_transcript(folder)
            with self.settings(MEDIA_ROOT=folder):
                created = self.client.post(
                    "/api/jobs/",
                    data={
                        "prompt": "apply with my CV and transcript, stop before submit",
                        "vault_ids": [doc_id],
                        "current_url": "http://127.0.0.1:8001/demo/form-a/",
                    },
                    content_type="application/json",
                )
                run_id = created.json()["run"]["id"]
                with patch("core.services.agent._client", return_value=object()), patch(
                    "core.services.agent._messages_create", side_effect=fake_create
                ):
                    started = self.client.post(
                        f"/api/jobs/{run_id}/go/",
                        data={"current_url": "http://127.0.0.1:8001/demo/form-a/"},
                        content_type="application/json",
                    )
                    self.assertEqual(started.status_code, 200)
                    run = started.json()["run"]
                    stored = Run.objects.get(id=run_id)
                    self.assertEqual(run["pending_actions"][0]["name"], "read_page")
                    self.assertEqual(model_call_count(stored), 0)

                    personal = self.client.post(
                        f"/api/runs/{run_id}/turn/",
                        data={
                            "current_url": "http://127.0.0.1:8001/demo/form-a/",
                            "tool_results": [
                                {
                                    "tool_use_id": run["pending_actions"][0]["tool_use_id"],
                                    "content": FORM_A_APPLY_PAGE1,
                                }
                            ],
                        },
                        content_type="application/json",
                    )
                    run = personal.json()["run"]
                    stored.refresh_from_db()
                    fills = {
                        item["input"]["value"]
                        for item in run["pending_actions"]
                        if item["name"] == "form_input"
                    }
                    self.assertEqual(
                        fills,
                        {"Alex Example", "a@x.com", "1 Exhibition Rd", "London", "SW7 2AZ"},
                    )
                    self.assertTrue(any(item["name"] == "left_click" for item in run["pending_actions"]))
                    self.assertEqual(model_call_count(stored), 0)

                    education = self.client.post(
                        f"/api/runs/{run_id}/turn/",
                        data={
                            "current_url": "http://127.0.0.1:8001/demo/form-a/education/",
                            "tool_results": [
                                {"tool_use_id": item["tool_use_id"], "content": "Filled"}
                                for item in run["pending_actions"]
                            ],
                        },
                        content_type="application/json",
                    )
                    run = education.json()["run"]
                    stored.refresh_from_db()
                    self.assertEqual(run["stage_index"], 1)
                    self.assertEqual(run["pending_actions"][0]["name"], "read_page")
                    self.assertEqual(model_call_count(stored), 0)

                    modeled = self.client.post(
                        f"/api/runs/{run_id}/turn/",
                        data={
                            "current_url": "http://127.0.0.1:8001/demo/form-a/education/",
                            "tool_results": [
                                {
                                    "tool_use_id": run["pending_actions"][0]["tool_use_id"],
                                    "content": FORM_A_PAGE2,
                                }
                            ],
                        },
                        content_type="application/json",
                    )
                    run = modeled.json()["run"]
                    stored.refresh_from_db()
                    self.assertEqual(model_call_count(stored), 1)
                    self.assertEqual(fake_create.calls, 1)
                    names = [item["name"] for item in run["pending_actions"]]
                    self.assertIn("form_input", names)
                    self.assertIn("left_click", names)

                    upload = self.client.post(
                        f"/api/runs/{run_id}/turn/",
                        data={
                            "current_url": "http://127.0.0.1:8001/demo/form-a/upload/",
                            "tool_results": [
                                {"tool_use_id": item["tool_use_id"], "content": FORM_A_UPLOAD_PAGE}
                                for item in run["pending_actions"]
                            ],
                        },
                        content_type="application/json",
                    )
                    run = upload.json()["run"]
                    stored.refresh_from_db()
                    self.assertEqual(run["stage_index"], 2)
                    attach = [item for item in run["pending_actions"] if item["name"] == "attach_from_vault"]
                    self.assertEqual(len(attach), 1)
                    self.assertEqual(attach[0]["input"]["document_id"], doc_id)
                    self.assertEqual(model_call_count(stored), 1)

                    review = self.client.post(
                        f"/api/runs/{run_id}/turn/",
                        data={
                            "current_url": "http://127.0.0.1:8001/demo/form-a/review/",
                            "tool_results": [
                                {"tool_use_id": item["tool_use_id"], "content": FORM_A_REVIEW_PAGE}
                                for item in run["pending_actions"]
                            ],
                        },
                        content_type="application/json",
                    )
                    run = review.json()["run"]
                    stored.refresh_from_db()
                    self.assertEqual(run["status"], "awaiting_confirm")
                    self.assertEqual(run["stage_index"], 3)
                    self.assertEqual(model_call_count(stored), 1)
                    self.assertLessEqual(model_call_count(stored), 4)
                    self.assertFalse(any(item["name"] == "left_click" for item in run["pending_actions"]))
                    self.assertEqual(run["pending_actions"][0]["name"], "request_user_confirm")
                    self.assertNotIn("Year 3 Computing transcript", json.dumps(stored.messages))


class ActivityExportTests(TestCase):
    def test_activity_lists_procedure_stage_facts_and_resume(self):
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        skill = Skill.objects.create(
            name="Internship",
            goal="Apply",
            ready=True,
            start_url="http://127.0.0.1:8001/demo/form-a/",
            site_host="127.0.0.1:8001",
            lessons=["The year dropdown wants Year 3, not 2024."],
        )
        Stage.objects.create(skill=skill, index=0, name="Personal details", url_pattern="/demo/form-a/")
        Stage.objects.create(skill=skill, index=1, name="Education history", url_pattern="/demo/form-a/education/")
        run = Run.objects.create(
            skill=skill,
            kind="skill",
            status="paused",
            intent="Apply",
            stage_index=1,
            fill_report={"lessons": ["The year dropdown wants Year 3, not 2024."]},
            outcome="Paused on education.",
        )
        from core.services.activity import stamp_activity

        stamp_activity(run)
        run.save()
        with patch("core.services.agent._messages_create") as mocked:
            mocked.side_effect = AssertionError("listing activity must not call the model")
            listed = self.client.get("/api/runs/")
        self.assertEqual(listed.status_code, 200)
        card = listed.json()["runs"][0]
        self.assertEqual(card["procedure"], "Internship")
        self.assertEqual(card["stage_reached"], "Education history")
        self.assertTrue(any(item["key"] == "email" for item in card["facts_used"]))
        self.assertIn("Year 3", card["lesson"])
        self.assertTrue(card["resumable"])

    def test_export_contains_profile_json_and_vault_files(self):
        MemoryFact.objects.create(key="email", label="Email", value="a@x.com", category="identity")
        MemoryFact.objects.create(
            key="hobby",
            label="Hobby",
            value="chess",
            category="other",
            confirmed=False,
        )
        with tempfile.TemporaryDirectory() as folder:
            with self.settings(MEDIA_ROOT=folder):
                uploaded = self.client.post(
                    "/api/documents/",
                    data={
                        "title": "Note",
                        "file": SimpleUploadedFile("note.txt", b"hello vault\n", content_type="text/plain"),
                    },
                )
                self.assertEqual(uploaded.status_code, 201)
                doc_id = uploaded.json()["document"]["id"]
                response = self.client.get("/api/export/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/zip")
                archive = zipfile.ZipFile(BytesIO(response.content))
                names = archive.namelist()
                self.assertIn("profile.json", names)
                profile = json.loads(archive.read("profile.json"))
                keys = {item["key"] for item in profile["facts"]}
                self.assertIn("email", keys)
                self.assertNotIn("hobby", keys)
                vault_name = "vault/" + doc_id + "/note.txt"
                self.assertIn(vault_name, names)
                self.assertEqual(archive.read(vault_name), b"hello vault\n")
                blob = archive.read("profile.json").decode("utf-8")
                self.assertNotIn("hello vault", blob)

    def test_delete_document_removes_facts_cited_only_by_that_file(self):
        MemoryFact.objects.create(
            key="email",
            label="Email",
            value="typed@example.com",
            category="identity",
            source="typed",
        )
        with tempfile.TemporaryDirectory() as folder:
            with self.settings(MEDIA_ROOT=folder):
                uploaded = self.client.post(
                    "/api/documents/",
                    data={"title": "CV", "file": _docx_upload(["Alex Example", "file@example.com"])},
                )
                doc_id = uploaded.json()["document"]["id"]
                proposals = uploaded.json()["document"]["proposals"]
                email = next(item for item in proposals if item["key"] == "email")
                name = next(item for item in proposals if item["key"] == "full_name")
                accepted = self.client.post(
                    "/api/documents/" + doc_id + "/accept/",
                    data={"fact_ids": [email["id"], name["id"]]},
                    content_type="application/json",
                )
                self.assertEqual(accepted.status_code, 200)
                self.assertTrue(MemoryFact.objects.filter(key="email", value="typed@example.com", confirmed=True).exists())
                self.assertTrue(MemoryFact.objects.filter(key="full_name", value="Alex Example", confirmed=True).exists())
                deleted = self.client.delete("/api/documents/" + doc_id + "/")
                self.assertEqual(deleted.status_code, 200)
                self.assertTrue(deleted.json()["deleted"])
                self.assertFalse(Document.objects.filter(id=doc_id).exists())
                self.assertTrue(MemoryFact.objects.filter(key="email", value="typed@example.com").exists())
                self.assertFalse(MemoryFact.objects.filter(key="full_name", value="Alex Example").exists())
                self.assertFalse(MemoryFact.objects.filter(source_document_id=doc_id).exists())


class NebiusCompletionTests(TestCase):
    def test_model_ids_skip_the_anthropic_alias(self):
        from config.settings import _anthropic_model, _model_id

        self.assertEqual(
            _model_id("deepseek-ai/DeepSeek-V4.1-Flash-1017", "x"),
            "deepseek-ai/DeepSeek-V4.1-Flash-1017",
        )
        self.assertEqual(_model_id("claude-sonnet-5.5", "x"), "claude-sonnet-5.5")
        self.assertEqual(_anthropic_model("claude-sonnet-5.5", "x"), "claude-sonnet-5-5")

    def test_health_reports_provider_keys_and_model_ids(self):
        with self.settings(
            NEBIUS_API_KEY="nebius",
            TAVILY_API_KEY="",
            ANTHROPIC_API_KEY="anthropic",
            RUN_MODEL="deepseek-ai/DeepSeek-V4-Pro-0813",
            COMPILE_MODEL="deepseek-ai/DeepSeek-V4-Pro-0813",
            VISION_MODEL="deepseek-ai/DeepSeek-V4.1-Flash-1017",
        ):
            response = self.client.get("/api/health/")
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["has_api_key"])
        self.assertTrue(body["has_nebius_key"])
        self.assertFalse(body["has_tavily_key"])
        self.assertTrue(body["has_anthropic_key"])
        self.assertEqual(body["run_model"], "deepseek-ai/DeepSeek-V4-Pro-0813")
        self.assertEqual(body["compile_model"], "deepseek-ai/DeepSeek-V4-Pro-0813")
        self.assertEqual(body["vision_model"], "deepseek-ai/DeepSeek-V4.1-Flash-1017")

    def _clients(self, nebius_create, claude_create):
        class _Completions:
            create = staticmethod(nebius_create)

        class _Nebius:
            class chat:
                completions = _Completions()

        class _ClaudeMessages:
            create = staticmethod(claude_create)

        class _Claude:
            messages = _ClaudeMessages()

        return _Nebius(), _Claude()

    def test_failed_nebius_completion_is_retried_once_on_claude(self):
        from django.conf import settings

        from core.services.llm import complete

        nebius_calls = []
        claude_calls = []

        def nebius_create(**kwargs):
            nebius_calls.append(kwargs)
            raise RuntimeError("Error code: 429")

        def claude_create(**kwargs):
            claude_calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="from claude")],
                stop_reason="end_turn",
            )

        nebius, claude = self._clients(nebius_create, claude_create)
        with self.settings(FALLBACK_MODEL="claude-sonnet-5-5"), patch(
            "core.services.llm._nebius_client", return_value=nebius
        ), patch("core.services.llm._anthropic_client", return_value=claude):
            result = complete(
                {
                    "model": "deepseek-ai/DeepSeek-V4-Pro-0813",
                    "max_tokens": 64,
                    "system": "Be brief.",
                    "messages": [{"role": "user", "content": "What does the form ask for?"}],
                }
            )
        self.assertEqual(len(nebius_calls), 1)
        self.assertEqual(len(claude_calls), 1)
        self.assertEqual(claude_calls[0]["model"], settings.FALLBACK_MODEL)
        self.assertNotIn("thinking", claude_calls[0])
        self.assertEqual(result.provider, "claude")
        self.assertEqual(result.content[0].text, "from claude")

    def test_reasoning_cutoff_is_retried_on_nebius_before_claude(self):
        from core.services.llm import complete

        budgets = []

        def nebius_create(**kwargs):
            budgets.append(kwargs["max_tokens"])
            if len(budgets) == 1:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="length",
                            message=SimpleNamespace(content="", tool_calls=None),
                        )
                    ]
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content='{"summary": "A CV and a transcript."}', tool_calls=None),
                    )
                ]
            )

        def claude_create(**kwargs):
            raise AssertionError("Claude must not run when a longer Nebius call returns text")

        nebius, claude = self._clients(nebius_create, claude_create)
        with patch("core.services.llm._nebius_client", return_value=nebius), patch(
            "core.services.llm._anthropic_client", return_value=claude
        ):
            result = complete(
                {
                    "model": "deepseek-ai/DeepSeek-V4-Pro-0813",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": "What does the internship ask for?"}],
                }
            )
        self.assertEqual(budgets, [1024, 8192])
        self.assertEqual(result.provider, "nebius")
        self.assertIn("transcript", result.content[0].text)

    def test_empty_nebius_message_is_retried_once_on_claude(self):
        from core.services.llm import complete

        nebius_calls = []
        claude_calls = []

        def nebius_create(**kwargs):
            nebius_calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="  ", tool_calls=None))]
            )

        def claude_create(**kwargs):
            claude_calls.append(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="toolu_1",
                        name="form_input",
                        input={"target": "ref_1", "value": "Maya"},
                    )
                ],
                stop_reason="tool_use",
            )

        nebius, claude = self._clients(nebius_create, claude_create)
        with patch("core.services.llm._nebius_client", return_value=nebius), patch(
            "core.services.llm._anthropic_client", return_value=claude
        ):
            result = complete({"messages": [{"role": "user", "content": "Fill the name"}]})
        self.assertEqual(len(nebius_calls), 1)
        self.assertEqual(len(claude_calls), 1)
        self.assertEqual(result.provider, "claude")
        self.assertEqual(result.stop_reason, "tool_use")
        self.assertEqual(result.content[0].name, "form_input")
        self.assertEqual(result.content[0].toolset_name, "browser")

    def test_nebius_success_does_not_call_claude_and_translates_tools(self):
        from core.services.llm import complete

        nebius_calls = []

        def nebius_create(**kwargs):
            nebius_calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    id="call_1",
                                    function=SimpleNamespace(
                                        name="form_input",
                                        arguments='{"target": "ref_1", "value": "Maya Chen"}',
                                    ),
                                )
                            ],
                        )
                    )
                ]
            )

        def claude_create(**kwargs):
            raise AssertionError("Claude must not run when Nebius returns a tool call")

        nebius, claude = self._clients(nebius_create, claude_create)
        messages = [
            {"role": "user", "content": "Fill the form"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "map the email", "signature": "sig"},
                    {"type": "tool_use", "id": "toolu_1", "name": "read_page", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "textbox name"}],
            },
        ]
        with patch("core.services.llm._nebius_client", return_value=nebius), patch(
            "core.services.llm._anthropic_client", return_value=claude
        ):
            result = complete(
                {
                    "model": "deepseek-ai/DeepSeek-V4-Pro-0813",
                    "messages": messages,
                    "tools": [
                        {
                            "name": "form_input",
                            "description": "Fill a field",
                            "input_schema": {"type": "object", "properties": {"target": {"type": "string"}}},
                        }
                    ],
                }
            )
        self.assertEqual(result.provider, "nebius")
        self.assertEqual(result.content[0].id, "call_1")
        self.assertEqual(result.content[0].input["value"], "Maya Chen")
        sent = nebius_calls[0]
        self.assertEqual(sent["model"], "deepseek-ai/DeepSeek-V4-Pro-0813")
        self.assertEqual(sent["tools"][0]["type"], "function")
        self.assertEqual(sent["tools"][0]["function"]["name"], "form_input")
        blob = json.dumps(sent["messages"])
        self.assertNotIn("thinking", blob)
        self.assertNotIn("map the email", blob)
        self.assertIn('"role": "tool"', blob)
        assistant = next(item for item in sent["messages"] if item["role"] == "assistant")
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "read_page")

    def test_image_request_uses_the_vision_model(self):
        from django.conf import settings

        from core.services.llm import complete

        nebius_calls = []

        def nebius_create(**kwargs):
            nebius_calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="A form.", tool_calls=None))]
            )

        def claude_create(**kwargs):
            raise AssertionError("Claude must not run when Nebius returns text")

        nebius, claude = self._clients(nebius_create, claude_create)
        with self.settings(VISION_MODEL="deepseek-ai/DeepSeek-V4.1-Flash-1017"), patch(
            "core.services.llm._nebius_client", return_value=nebius
        ), patch("core.services.llm._anthropic_client", return_value=claude):
            result = complete(
                {
                    "model": "deepseek-ai/DeepSeek-V4-Pro-0813",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "What is on this page?"},
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": "abc",
                                    },
                                },
                            ],
                        }
                    ],
                }
            )
        self.assertEqual(result.provider, "nebius")
        self.assertEqual(nebius_calls[0]["model"], settings.VISION_MODEL)
        part = nebius_calls[0]["messages"][0]["content"][1]
        self.assertEqual(part["type"], "image_url")
        self.assertEqual(part["image_url"]["url"], "data:image/jpeg;base64,abc")

    def test_turn_records_the_provider(self):
        run = Run.objects.create(kind="attempt", status="running", intent="Fill this")
        reply = SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", id="toolu_1", name="read_page", input={}, toolset_name="browser")],
            stop_reason="tool_use",
            provider="nebius",
        )
        with patch("core.services.agent._client", return_value=object()), patch(
            "core.services.agent._messages_create", return_value=reply
        ):
            payload = turn(run)
        self.assertEqual(payload["provider"], "nebius")
        self.assertEqual(payload["pending_actions"][0]["name"], "read_page")
        self.assertEqual(payload["pending_actions"][0]["tool_use_id"], "toolu_1")


class TavilyJobTests(TestCase):
    def test_localhost_url_does_not_call_tavily(self):
        from core.services.web import extract_for_run, extract_urls, is_public_http

        with patch("core.services.web.post_json") as posted:
            self.assertFalse(is_public_http("http://127.0.0.1:8001/demo/form-a/"))
            self.assertFalse(is_public_http("http://localhost/demo/form-a/"))
            self.assertFalse(is_public_http("chrome://extensions"))
            self.assertEqual(extract_urls(["http://127.0.0.1:8001/demo/form-a/", "http://localhost/x"], "apply"), [])
            run = Run.objects.create(kind="job", status="running", current_url="http://127.0.0.1:8001/demo/form-a/")
            self.assertEqual(extract_for_run(run, run.current_url, "apply with my CV"), "")
            job = compile_job(
                "apply with my CV and transcript, stop before submit",
                current_url="http://127.0.0.1:8001/demo/form-a/",
            )
        posted.assert_not_called()
        self.assertFalse(task_needs_public_page(job["prompt"]))
        self.assertIn("/demo/form-a/", job["start_url"])
        self.assertNotIn("sources", job)

    def test_public_job_searches_then_extracts_then_one_completion(self):
        calls = []
        completions = []

        def posted(path, payload):
            calls.append((path, payload))
            if path == "/search":
                self.assertNotIn("saved facts", payload["query"].lower())
                return {
                    "results": [
                        {
                            "url": "https://www.youtube.com/watch?v=not-the-internship",
                            "title": "A video",
                            "content": "Unrelated.",
                        },
                        {
                            "url": "https://www.imperial.ac.uk/jobs/summer",
                            "title": "Summer internship",
                            "content": "Send a CV and a transcript.",
                        },
                        {
                            "url": "https://www.imperial.ac.uk/jobs/apply",
                            "title": "How to apply",
                            "content": "Personal details and a course.",
                        },
                        {"url": "http://127.0.0.1:8001/demo/form-a/", "title": "local", "content": "no"},
                        {"url": "https://www.imperial.ac.uk/jobs/dates", "title": "Dates", "content": "June."},
                        {"url": "https://www.imperial.ac.uk/jobs/extra", "title": "Extra", "content": "More."},
                    ]
                }
            self.assertEqual(path, "/extract")
            self.assertNotIn("127.0.0.1", json.dumps(payload))
            self.assertLessEqual(len(payload["urls"]), 3)
            return {
                "results": [
                    {"url": url, "raw_content": "A CV, a transcript, and your degree."}
                    for url in payload["urls"]
                ]
            }

        def complete(kwargs):
            completions.append(kwargs)
            return SimpleNamespace(
                provider="nebius",
                content=[
                    SimpleNamespace(
                        type="text",
                        text=json.dumps(
                            {
                                "goal": "See what the Imperial summer internship asks for",
                                "summary": "It asks for a CV, a transcript, and a degree.",
                                "covered": [{"fact": "University", "covers": "degree"}],
                            }
                        ),
                    )
                ],
            )

        prompt = "what does the Imperial College summer internship actually ask for, and which of my saved facts cover it"
        with patch("core.services.web.post_json", side_effect=posted), patch(
            "core.services.llm.complete", side_effect=complete
        ):
            job = compile_job(prompt, current_url="http://127.0.0.1:8001/demo/")
        self.assertEqual([item[0] for item in calls], ["/search", "/extract"])
        search_body = calls[0][1]
        self.assertEqual(search_body["search_depth"], "advanced")
        self.assertEqual(search_body["max_results"], 5)
        self.assertFalse(search_body["include_answer"])
        self.assertFalse(search_body["include_raw_content"])
        extract_body = calls[1][1]
        self.assertEqual(extract_body["extract_depth"], "advanced")
        self.assertEqual(extract_body["format"], "markdown")
        self.assertEqual(extract_body["chunks_per_source"], 3)
        self.assertIn("Imperial", extract_body["query"])
        self.assertEqual(len(completions), 1)
        from django.conf import settings

        self.assertEqual(completions[0]["model"], settings.COMPILE_MODEL)
        self.assertEqual(job["provider"], "nebius")
        self.assertEqual(job["sources"][0]["url"], "https://www.imperial.ac.uk/jobs/summer")
        self.assertIn("CV", job["summary"])
        self.assertNotIn("127.0.0.1", job["start_url"])
        names = [tool["name"] for tool in _create_kwargs([{"role": "user", "content": "hi"}])["tools"]]
        self.assertIn("web_search", names)


class ListingTests(TestCase):
    def test_photo_post_is_a_listing_not_a_video_job(self):
        job = compile_job_heuristic(
            "post this",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000}],
        )
        self.assertEqual(job["mode"], "listing")
        self.assertEqual(
            [site["id"] for site in job["listing_sites"]],
            ["vinted", "ebay", "facebook"],
        )
        self.assertIn("vinted.co.uk/items/new", job["start_url"])
        self.assertNotIn("youtube", job["start_url"])
        self.assertEqual(job["listing"]["title"], "Blue ceramic mug")
        self.assertEqual(job["stop_before"], "Upload, List, or Publish")

    def test_prompt_price_and_condition_override_the_stock_card(self):
        job = attach_listing_copy(
            {
                "prompt": "sell this crusty blue mug, probably worth 2 pounds",
                "attachments": [],
            }
        )
        self.assertEqual(job["listing"]["price_gbp"], "2")
        self.assertEqual(job["listing"]["condition"], "Satisfactory")
        self.assertEqual(job["summary"], "Blue ceramic mug · £2")
        self.assertEqual(job["asks"], [])

    def test_listing_asks_for_price_and_condition_when_the_prompt_leaves_them_out(self):
        job = compile_job_heuristic(
            "post this",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000}],
        )
        self.assertEqual([item["key"] for item in job["asks"]], ["price_gbp", "condition"])

    def test_facebook_listing_type_page_opens_the_item_form(self):
        job = compile_job_heuristic(
            "sell this on facebook",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="sell this on facebook",
            job=job,
            current_url="https://www.facebook.com/marketplace/create",
            plan_approved=True,
        )
        form = {
            "url": "https://www.facebook.com/marketplace/create",
            "title": "Create new listing",
            "fields": [],
            "buttons": [{"ref": "ref_1", "name": "Choose listing type"}],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        urls = [item["input"].get("url") for item in result["pending_actions"] if item["name"] == "navigate"]
        self.assertEqual(urls, ["https://www.facebook.com/marketplace/create/item"])

    def test_filled_facebook_colour_is_not_reopened(self):
        job = compile_job_heuristic(
            "sell this on facebook",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="sell this on facebook",
            job=job,
            current_url="https://www.facebook.com/marketplace/create/item",
            plan_approved=True,
            checkpoint={"listing_state": {"index": 0, "attached": True, "clicked": [], "tried": []}},
        )
        form = {
            "url": "https://www.facebook.com/marketplace/create/item",
            "title": "Create new listing",
            "fields": [
                {"ref": "ref_1", "role": "file", "label": "Photos", "placeholder": "", "value": "1"},
                {"ref": "ref_2", "role": "textbox", "label": "Title", "placeholder": "", "value": "Blue ceramic mug"},
                {"ref": "ref_3", "role": "textbox", "label": "Price", "placeholder": "", "value": "2"},
                {"ref": "ref_8", "role": "textbox", "label": "Description", "placeholder": "", "value": "A blue mug."},
            ],
            "buttons": [
                {"ref": "ref_4", "name": "Category Household"},
                {"ref": "ref_5", "name": "Condition Used - good"},
                {"ref": "ref_6", "name": "Colour Blue"},
                {"ref": "ref_7", "name": "Publish"},
            ],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertEqual(result["status"], "awaiting_confirm")
        self.assertEqual(
            [item["input"].get("text") for item in result["pending_actions"] if item["name"] == "click_text"],
            ["Publish"],
        )

    def test_facebook_audience_step_asks_before_publish(self):
        job = compile_job_heuristic(
            "sell this on facebook",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="sell this on facebook",
            job=job,
            current_url="https://www.facebook.com/marketplace/create/item?step=audience",
            plan_approved=True,
            checkpoint={"listing_state": {"index": 0, "attached": True, "clicked": ["category", "condition"], "tried": ["category", "condition"]}},
        )
        form = {
            "url": "https://www.facebook.com/marketplace/create/item?step=audience",
            "title": "List in more places",
            "fields": [],
            "buttons": [
                {"ref": "ref_1", "name": "Previous"},
                {"ref": "ref_2", "name": "Publish"},
            ],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertEqual(result["status"], "awaiting_confirm")
        self.assertIn("Facebook", result["confirm_summary"])
        clicks = [item["input"].get("text") for item in result["pending_actions"] if item["name"] == "click_text"]
        self.assertEqual(clicks, ["Publish"])

    def test_en_dash_condition_matches_used_fair(self):
        job = compile_job_heuristic(
            "sell this crusty mug on facebook, worth 2 pounds",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="sell this crusty mug on facebook, worth 2 pounds",
            job={**job, "listing": {**job["listing"], "condition": "Satisfactory", "price_gbp": "2"}},
            current_url="https://www.facebook.com/marketplace/create/item",
            plan_approved=True,
            checkpoint={"listing_state": {"index": 0, "attached": True, "clicked": ["condition-open"], "picking": "condition", "tried": ["category"]}},
        )
        form = {
            "url": "https://www.facebook.com/marketplace/create/item",
            "title": "Create new listing",
            "fields": [
                {"ref": "ref_1", "role": "file", "label": "Photos", "placeholder": "", "value": "1"},
                {"ref": "ref_2", "role": "textbox", "label": "Title", "placeholder": "", "value": "Blue ceramic mug"},
                {"ref": "ref_3", "role": "textbox", "label": "Price", "placeholder": "", "value": "2"},
            ],
            "buttons": [{"ref": "ref_4", "name": "Used – fair"}, {"ref": "ref_5", "name": "Publish"}],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertEqual(
            [item["input"].get("text") for item in result["pending_actions"] if item["name"] == "click_text"],
            ["Used – fair"],
        )

    def test_facebook_condition_dropdown_opens_before_publish(self):
        job = compile_job_heuristic(
            "sell this on facebook",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="sell this on facebook",
            job=job,
            current_url="https://www.facebook.com/marketplace/create/item",
            plan_approved=True,
            checkpoint={"listing_state": {"index": 0, "attached": True, "clicked": [], "tried": []}},
        )
        form = {
            "url": "https://www.facebook.com/marketplace/create/item",
            "title": "Create new listing",
            "fields": [
                {"ref": "ref_1", "role": "file", "label": "Photos", "placeholder": "", "value": "1"},
                {"ref": "ref_2", "role": "textbox", "label": "Title", "placeholder": "", "value": "Blue ceramic mug"},
                {"ref": "ref_3", "role": "textbox", "label": "Price", "placeholder": "", "value": "8"},
            ],
            "buttons": [
                {"ref": "ref_4", "name": "Category Household"},
                {"ref": "ref_5", "name": "Condition"},
                {"ref": "ref_6", "name": "Publish"},
            ],
        }
        first = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertEqual(
            [item["input"].get("text") for item in first["pending_actions"] if item["name"] == "click_text"],
            ["Condition"],
        )
        form["buttons"] = [
            {"ref": "ref_4", "name": "Category Household"},
            {"ref": "ref_7", "name": "Used - good"},
            {"ref": "ref_6", "name": "Publish"},
        ]
        second = apply_stage_turn(run, [{"tool_use_id": "local_click_1", "content": json.dumps(form)}])
        self.assertEqual(
            [item["input"].get("text") for item in second["pending_actions"] if item["name"] == "click_text"],
            ["Used - good"],
        )

    def test_ebay_suggest_page_searches_after_the_title(self):
        job = compile_job_heuristic(
            "post this on ebay",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this on ebay",
            job=job,
            current_url="https://www.ebay.co.uk/sl/prelist/suggest",
            plan_approved=True,
        )
        form = {
            "url": "https://www.ebay.co.uk/sl/prelist/suggest",
            "title": "Start your listing",
            "fields": [
                {
                    "ref": "ref_1",
                    "role": "textbox",
                    "label": "Tell us what you're selling",
                    "placeholder": "",
                    "value": "",
                }
            ],
            "buttons": [{"ref": "ref_2", "name": "Search"}],
        }
        first = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        fills = [item["input"]["value"] for item in first["pending_actions"] if item["name"] == "form_input"]
        self.assertIn("Blue ceramic mug", fills)
        form["fields"][0]["value"] = "Blue ceramic mug"
        second = apply_stage_turn(run, [{"tool_use_id": "local_fill_1", "content": json.dumps(form)}])
        clicks = [item["input"].get("text") for item in second["pending_actions"] if item["name"] == "click_text"]
        self.assertIn("Search", clicks)
        self.assertNotIn("click_text", [item["name"] for item in second["pending_actions"] if item["input"].get("text") == "List it"])

    def test_filled_vinted_confirms_upload_when_the_button_is_missing_from_the_map(self):
        job = compile_job_heuristic(
            "post this",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this",
            job=job,
            current_url="https://www.vinted.co.uk/items/new",
            plan_approved=True,
            checkpoint={"listing_state": {"index": 0, "stuck": 2, "attached": True, "clicked": ["category"], "tried": []}},
        )
        form = {
            "url": "https://www.vinted.co.uk/items/new",
            "title": "Sell",
            "fields": [
                {"ref": "ref_1", "role": "file", "label": "Photos", "placeholder": "", "value": "1"},
                {
                    "ref": "ref_2",
                    "role": "textbox",
                    "label": "Title",
                    "placeholder": "Tell buyers what you're selling",
                    "value": "Blue ceramic mug",
                },
                {"ref": "ref_3", "role": "textbox", "label": "Price", "placeholder": "", "value": "8"},
            ],
            "buttons": [{"ref": "ref_4", "name": "Save draft"}],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertEqual(result["status"], "awaiting_confirm")
        self.assertIn("Vinted", result["confirm_summary"])
        clicks = [item["input"].get("text") for item in result["pending_actions"] if item["name"] == "click_text"]
        self.assertEqual(clicks, ["upload"])

    def test_filled_form_without_a_file_field_pauses_for_a_reload(self):
        job = compile_job_heuristic(
            "post this on vinted",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this on vinted",
            job=job,
            current_url="https://www.vinted.co.uk/items/new",
            plan_approved=True,
            checkpoint={"listing_state": {"index": 0, "stuck": 2, "attached": False, "clicked": ["category"], "tried": ["category"]}},
        )
        form = {
            "url": "https://www.vinted.co.uk/items/new",
            "title": "Sell",
            "fields": [
                {"ref": "ref_1", "role": "textbox", "label": "Title", "placeholder": "", "value": "Blue ceramic mug"},
                {"ref": "ref_2", "role": "textbox", "label": "Price", "placeholder": "", "value": "8"},
            ],
            "buttons": [{"ref": "ref_3", "name": "Save draft"}],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertEqual(result["status"], "paused")
        self.assertIn("photo", result["last_status_text"].lower())
        self.assertNotIn("navigate", [item["name"] for item in result["pending_actions"]])

    def test_add_photo_is_not_clicked_when_the_file_field_is_missing(self):
        job = compile_job_heuristic(
            "post this on facebook",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this on facebook",
            job=job,
            current_url="https://www.facebook.com/marketplace/create/item",
            plan_approved=True,
        )
        form = {
            "url": "https://www.facebook.com/marketplace/create/item",
            "title": "Create new listing",
            "fields": [
                {"ref": "ref_1", "role": "textbox", "label": "Title", "placeholder": "", "value": ""},
            ],
            "buttons": [{"ref": "ref_2", "name": "Add photo"}],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        names = [item["name"] for item in result["pending_actions"]]
        self.assertIn("form_input", names)
        self.assertNotIn("click_text", names)

    def test_unbranded_picks_no_label_before_upload(self):
        job = compile_job_heuristic(
            "post this on vinted",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this on vinted",
            job=job,
            current_url="https://www.vinted.co.uk/items/new",
            plan_approved=True,
            checkpoint={
                "listing_state": {
                    "index": 0,
                    "attached": True,
                    "clicked": ["select a brand"],
                    "tried": ["category"],
                }
            },
        )
        form = {
            "url": "https://www.vinted.co.uk/items/new",
            "title": "Sell",
            "fields": [
                {"ref": "ref_1", "role": "file", "label": "Photos", "placeholder": "", "value": "1"},
                {"ref": "ref_2", "role": "textbox", "label": "Title", "placeholder": "", "value": "Blue ceramic mug"},
                {"ref": "ref_3", "role": "textbox", "label": "Price", "placeholder": "", "value": "8"},
            ],
            "buttons": [
                {"ref": "ref_4", "name": "No Label"},
                {"ref": "ref_5", "name": "Upload"},
            ],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertEqual(result["status"], "running")
        self.assertEqual(
            [item["input"].get("text") for item in result["pending_actions"] if item["name"] == "click_text"],
            ["No Label"],
        )

    def test_ebay_results_continue_without_listing(self):
        job = compile_job_heuristic(
            "post this on ebay",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this on ebay",
            job=job,
            current_url="https://www.ebay.co.uk/sl/prelist/identify",
            plan_approved=True,
            checkpoint={"listing_state": {"index": 0, "attached": True, "clicked": ["ebay-search"], "tried": []}},
        )
        form = {
            "url": "https://www.ebay.co.uk/sl/prelist/identify",
            "title": "Find a match",
            "fields": [
                {"ref": "ref_1", "role": "textbox", "label": "Title", "placeholder": "", "value": "Blue ceramic mug"},
                {"ref": "ref_2", "role": "textbox", "label": "Price", "placeholder": "", "value": "8"},
            ],
            "buttons": [
                {"ref": "ref_3", "name": "Continue without a product match"},
                {"ref": "ref_4", "name": "List it for free"},
            ],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        clicks = [item["input"].get("text") for item in result["pending_actions"] if item["name"] == "click_text"]
        self.assertEqual(clicks, ["Continue without a product match"])
        self.assertNotEqual(result["status"], "awaiting_confirm")

    def test_vinted_opens_material_then_picks_ceramic(self):
        job = compile_job_heuristic(
            "post this on vinted",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000, "document_id": "doc-1"}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this on vinted",
            job=job,
            current_url="https://www.vinted.co.uk/items/new",
            plan_approved=True,
            checkpoint={"listing_state": {"index": 0, "attached": True, "clicked": ["category"], "tried": ["category"]}},
        )
        form = {
            "url": "https://www.vinted.co.uk/items/new",
            "title": "Sell",
            "fields": [
                {"ref": "ref_1", "role": "file", "label": "Photos", "placeholder": "", "value": "1"},
                {"ref": "ref_2", "role": "textbox", "label": "Title", "placeholder": "Tell buyers what you're selling", "value": "Blue ceramic mug"},
                {"ref": "ref_3", "role": "textbox", "label": "Price", "placeholder": "", "value": "8"},
            ],
            "buttons": [{"ref": "ref_4", "name": "Select a material"}],
        }
        first = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertEqual(
            [item["input"].get("text") for item in first["pending_actions"] if item["name"] == "click_text"],
            ["Select a material"],
        )
        form["buttons"] = [{"ref": "ref_5", "name": "Ceramic"}, {"ref": "ref_6", "name": "Select your parcel size"}]
        second = apply_stage_turn(run, [{"tool_use_id": "local_click_1", "content": json.dumps(form)}])
        self.assertIn(
            "Ceramic",
            [item["input"].get("text") for item in second["pending_actions"] if item["name"] == "click_text"],
        )

    def test_named_sites_only(self):
        job = compile_job_heuristic(
            "post this on vinted and facebook",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000}],
        )
        self.assertEqual([site["id"] for site in job["listing_sites"]], ["vinted", "facebook"])

    def test_youtube_upload_is_not_a_listing(self):
        job = compile_job_heuristic(
            "upload this onto youtube with a caption",
            attachments=[{"name": "clip.mp4", "media_type": "video/mp4", "size": 2000}],
        )
        self.assertNotEqual(job["mode"], "listing")

    def test_vinted_fill_does_not_call_the_model_or_publish(self):
        job = compile_job_heuristic(
            "post this",
            attachments=[
                {
                    "name": "mug.jpg",
                    "media_type": "image/jpeg",
                    "size": 2000,
                    "document_id": "doc-1",
                }
            ],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this",
            job=job,
            current_url="https://www.vinted.co.uk/items/new",
            plan_approved=True,
        )
        form = {
            "url": "https://www.vinted.co.uk/items/new",
            "title": "Sell",
            "fields": [
                {"ref": "ref_1", "role": "file", "label": "Photos", "placeholder": "", "value": ""},
                {
                    "ref": "ref_2",
                    "role": "textbox",
                    "label": "Title",
                    "placeholder": "Tell buyers what you're selling",
                    "value": "",
                },
                {
                    "ref": "ref_3",
                    "role": "textbox",
                    "label": "Description",
                    "placeholder": "Tell buyers more about it",
                    "value": "",
                },
                {"ref": "ref_4", "role": "textbox", "label": "Price", "placeholder": "", "value": "0.00"},
            ],
            "buttons": [
                {"ref": "ref_5", "name": "Upload photos"},
                {"ref": "ref_6", "name": "Select a category"},
                {"ref": "ref_7", "name": "Upload"},
            ],
        }
        first = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertEqual([item["name"] for item in first["pending_actions"]], ["attach_from_vault"])
        self.assertEqual(model_call_count(run), 0)
        form["fields"][0]["value"] = "1"
        second = apply_stage_turn(run, [{"tool_use_id": "local_vault_1", "content": json.dumps(form)}])
        fills = [item["input"]["value"] for item in second["pending_actions"] if item["name"] == "form_input"]
        self.assertIn("Blue ceramic mug", fills)
        self.assertIn("8", fills)
        self.assertNotIn("click_text", [item["name"] for item in second["pending_actions"]])
        self.assertNotEqual(second["status"], "awaiting_confirm")
        self.assertEqual(model_call_count(run), 0)

    def test_resume_on_the_vinted_sell_page_does_not_say_sign_in(self):
        job = compile_job_heuristic(
            "post this on vinted",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000}],
        )
        run = Run.objects.create(
            kind="job",
            status="paused",
            intent="post this on vinted",
            job=job,
            current_url="https://www.vinted.co.uk/items/new",
            plan_approved=True,
            checkpoint={
                "listing_state": {
                    "index": 1,
                    "stuck": 2,
                    "skipped": [{"id": "vinted", "reason": "no further controls"}],
                }
            },
        )
        form = {
            "url": "https://www.vinted.co.uk/items/new",
            "title": "Sell an item",
            "fields": [
                {
                    "ref": "ref_1",
                    "role": "textbox",
                    "label": "Title",
                    "placeholder": "Tell buyers what you're selling",
                    "value": "",
                }
            ],
            "buttons": [{"ref": "ref_2", "name": "Upload"}],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertNotIn("Sign in", result["last_status_text"])
        self.assertIn("vinted.co.uk", result["current_url"])

    def test_saying_post_this_opens_the_vinted_sell_form(self):
        job = compile_job_heuristic(
            "post this on vinted",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this on vinted",
            job=job,
            current_url="https://www.vinted.co.uk/",
            plan_approved=True,
        )
        result = apply_stage_turn(run, [])
        self.assertEqual(result["pending_actions"][0]["name"], "navigate")
        self.assertIn("/items/new", result["pending_actions"][0]["input"]["url"])

    def test_sign_in_pauses_instead_of_skipping_the_site(self):
        job = compile_job_heuristic(
            "post this on vinted",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this on vinted",
            job=job,
            current_url="https://www.vinted.co.uk/member/login",
            plan_approved=True,
        )
        result = apply_stage_turn(run, [])
        self.assertEqual(result["status"], "paused")
        self.assertIn("Sign in on Vinted", result["last_status_text"])
        self.assertEqual(result["pending_actions"], [])

    def test_vinted_opens_brand_and_condition_when_the_label_is_part_of_the_name(self):
        job = compile_job_heuristic(
            "post this on vinted",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000}],
        )
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this on vinted",
            job=job,
            current_url="https://www.vinted.co.uk/items/new",
            plan_approved=True,
            checkpoint={"listing_state": {"index": 0, "attached": True, "clicked": ["category"], "tried": ["category"]}},
        )
        form = {
            "url": "https://www.vinted.co.uk/items/new",
            "title": "Sell",
            "fields": [
                {"ref": "ref_1", "role": "file", "label": "Photos", "placeholder": "", "value": "1"},
                {"ref": "ref_2", "role": "textbox", "label": "Title", "placeholder": "", "value": "Blue ceramic mug"},
                {"ref": "ref_3", "role": "textbox", "label": "Price", "placeholder": "", "value": "8"},
            ],
            "buttons": [
                {"ref": "ref_4", "name": "Brand Select a brand"},
                {"ref": "ref_5", "name": "Condition Select condition"},
                {"ref": "ref_6", "name": "Upload"},
            ],
        }
        first = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        self.assertEqual(
            [item["input"].get("text") for item in first["pending_actions"] if item["name"] == "click_text"],
            ["Condition Select condition"],
        )
        self.assertNotEqual(first["status"], "awaiting_confirm")
        form["buttons"] = [
            {"ref": "ref_4", "name": "Brand Select a brand"},
            {"ref": "ref_7", "name": "Good"},
            {"ref": "ref_8", "name": "Very good"},
            {"ref": "ref_6", "name": "Upload"},
        ]
        second = apply_stage_turn(run, [{"tool_use_id": "local_click_1", "content": json.dumps(form)}])
        self.assertEqual(
            [item["input"].get("text") for item in second["pending_actions"] if item["name"] == "click_text"],
            ["Good"],
        )

    def test_a_mug_is_a_medium_parcel(self):
        job = compile_job_heuristic(
            "post this on vinted",
            attachments=[{"name": "mug.jpg", "media_type": "image/jpeg", "size": 2000}],
        )
        self.assertEqual(job["listing"]["package"], "Medium")
        run = Run.objects.create(
            kind="job",
            status="running",
            intent="post this on vinted",
            job=job,
            current_url="https://www.vinted.co.uk/items/new",
            plan_approved=True,
            checkpoint={
                "listing_state": {
                    "index": 0,
                    "attached": True,
                    "clicked": ["category", "condition", "good", "unbranded", "blue", "ceramic"],
                    "tried": ["category"],
                }
            },
        )
        form = {
            "url": "https://www.vinted.co.uk/items/new",
            "title": "Sell",
            "fields": [
                {"ref": "ref_1", "role": "file", "label": "Photos", "placeholder": "", "value": "1"},
                {"ref": "ref_2", "role": "textbox", "label": "Title", "placeholder": "", "value": "Blue ceramic mug"},
                {"ref": "ref_3", "role": "textbox", "label": "Price", "placeholder": "", "value": "8"},
            ],
            "buttons": [
                {"ref": "ref_4", "name": "Small For items that'd fit in a large envelope."},
                {"ref": "ref_5", "name": "Medium For items that'd fit in a shoebox. Recommended"},
                {"ref": "ref_6", "name": "Large For items that'd fit in a moving box."},
            ],
        }
        result = apply_stage_turn(run, [{"tool_use_id": "local_map_1", "content": json.dumps(form)}])
        clicks = [item["input"].get("text") for item in result["pending_actions"] if item["name"] == "click_text"]
        self.assertEqual(clicks, ["Medium For items that'd fit in a shoebox. Recommended"])

    def test_parcel_size_asks_the_flash_model(self):
        card = {
            "title": "Blue ceramic mug",
            "description": "A used mug.",
            "category": "Home",
            "category_search": "mug",
        }
        reply = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"package": "Medium"}')])
        with patch("sys.argv", ["manage.py", "runserver"]), patch("core.services.llm.complete", return_value=reply) as mocked:
            chosen = _choose_package(card)
        self.assertEqual(chosen, "Medium")
        sent = mocked.call_args.args[0]
        self.assertEqual(sent["model"], settings.VISION_MODEL)
        text = sent["messages"][0]["content"][0]["text"]
        self.assertIn("Blue ceramic mug", text)
        self.assertIn("shoebox", text)
        self.assertIn("large envelope", text)
        self.assertIn("moving box", text)

