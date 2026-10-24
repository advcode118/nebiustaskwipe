# Next

This file is a queue. `docs/HOW_IT_WAS_BUILT.md` is the current product. Do not treat anything here as already true.

Work one slice per chat. Mark the slice done in this file, and update `HOW_IT_WAS_BUILT.md` in the same change, when the code actually behaves that way.

## Kickoff

New chat, Agent mode. Paste only:

```
Do the first undone slice in docs/NEXT.md.
Follow the standing rules and speed budget in that file.
Read docs/HOW_IT_WAS_BUILT.md for what already works.
Stop when that slice's Stop line is met.
```

Slice 1 is Plan mode first. Do not write code until the plan is approved, then start a new chat to implement that plan only.

## Standing rules

TaskWipe is a Chrome side panel (`overlay/`, React class components, no hooks) plus a Django JSON API (`backend/`, no DRF, no Channels, no auth). SQLite. Follow `docs/HOW_IT_WAS_BUILT.md`. If you change behaviour, update that file in the same change.

Do not rewrite the recorder, the executor, or the run loop. Extend them.
Class components only. Passwords, card numbers, CVV, and tokens are never stored.
Existing facts are never overwritten unless the request sends `overwrite_keys`.
Submit, Pay, Place order, and Publish stay blocked until the person confirms.

Stay inside this slice. Do not start the next one.
When you finish: run `python backend/manage.py test core` and fix failures.
A slice is done only if the old demo pages still behave as `HOW_IT_WAS_BUILT.md` describes.
After overlay changes: `npm run build` in `overlay`.

## Speed

Speed means the extension run, not the Cursor chat.

The wait is the model call. Count calls and payload size.

- Opening the panel, the profile, and the vault must not call the model.
- A known procedure on a matching page fills the whole page in one model call, returning every `form_input` together. If the field labels still match the demonstration, fill locally and do not call the model at all.
- A new page is one read, one plan, then one batched fill. Never one call per field.
- A multi-page procedure is one model call per stage. Changing URL advances the stage in Django. That does not call the model.
- Each turn sends only the current stage, at most 30 facts, and no document body. Strip screenshots and old page dumps from the stored history before the next call.
- Uploading and extracting a file happens when the file is saved, never inside `/api/runs/<id>/turn/`.
- Submit, Pay, Place order, and Publish still wait for a confirm. A missing fact still calls `ask_for_value`.

From slice 5 onward, a slice is not done without tests that fail if a known one-page run would call the model more than once, or if a turn payload contains a document body or a screenshot from an earlier turn.

## Out of scope until every slice below is done

Accounts, billing, hosted API, embeddings, document search, Django Channels, Next.js, auth.

---

## Slice 1 — Schema

Status: **done**

`MemoryFact` gained sensitivity, source, source quote, default, and group key. Unique-on-key was dropped; overwrite still required unless `add_another`. `Document` stores files under `backend/media/vault/`. A procedure is the current Skill plus ordered stages; existing skills and new one-page compiles get one stage. Routes: `/api/documents/` and `/api/skills/<id>/stages/`. No Profile screen, no extraction, no stage runner.

---

## Slice 2 — Profile you can edit

Status: **done**

Profile pane (`MemoryBook`) groups facts by category, keeps suggestions unconfirmed until Keep, and can mark a default or add another address without silent overwrite. `memory_for_run` returns a slice of at most 30 facts; the job card lists those facts. Listing and editing do not call the model. Vault is not built.

---

## Slice 3 — Vault and extraction

Status: **done**

Upload stores the file under `backend/media/vault/`, extracts PDF/DOCX/text once, and proposes unconfirmed facts with a source quote. Accept confirms; reject remembers so the next upload does not ask again. Card numbers are skipped. The Vault pane lists files and the Keep/Skip screen. A job can attach a vault file by id; the run gets id and title, not the document body. Dropped files still work. Recordings are not split into stages.

---

## Slice 4 — Multi-page compile

Status: **done**

Compile splits a recording at navigations (netloc + path) and writes ordered stages. One URL is still one stage. Review shows a page strip with rename / delete / merge; commit writes those stages and still refuses a silent memory overwrite. Form A is three pages under `/demo/form-a/`. The run loop is unchanged.

---

## Slice 5 — Multi-page run

Status: **done**

A run stores `stage_index` and a checkpoint. The briefing is the current stage only. A matching URL advances the stage without a model call; matching labels fill locally in one batch. History is compacted before the next model call. `attach_from_vault` attaches by document id. Login walls pause and ask the person to sign in. Submit / Pay / Place order / Publish stay blocked. Pause keeps the checkpoint; resume does not replay earlier stages.

---

## Slice 6 — One real job through the whole stack

Status: **done**

Form A is four pages. Deal with this on “apply with my CV and transcript, stop before submit” compiles a job card with the procedure stages, the profile facts, and the vault files. Personal details and address fill locally; education is at most one model call; the transcript attaches by id; review does not call the model; confirm still waits. From Go to that confirm, at most four model calls.

---

## Slice 7 — Activity and export

Status: **done**

Activity lists past runs (procedure, stage reached, facts used, lesson, Resume). Export zips confirmed profile facts and the original vault files. Deleting a document removes facts cited only by that file; independently typed facts stay. Listing and exporting do not call the model.

---

## Slice 8 — CV facts and a cheap page-kind check

Status: **done**

A CV/PDF/DOCX is parsed at upload into name, school email, phone, university, degree, major, and years. A job uses those facts even before Keep. Each page dump is classified by heuristic, Haiku only if unsure, as expected / next_stage / login / blocker / wrong. A cookie modal is dismissed locally. An expected application page fills matching labels from stored facts in one batch and does not click Submit.

Stop: `python backend/manage.py test core` is green. Form A still fills locally. A Jane Street-style dump with CV facts fills name/email/phone and does not submit. A cookie banner is a local click. Haiku is not called on a matching Form A page.
