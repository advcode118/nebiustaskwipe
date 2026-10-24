# How Taskwipe was built

This file is the rebuild spec. With a Nebius Token Factory key and this document you should be able to recreate the project from an empty folder. An Anthropic key is the fallback only. If the code and this file disagree, this file is wrong and must be updated.

## What it is

Taskwipe is a Chrome MV3 side panel plus a Django API.

1. You click **Record this** and do a web task in the current tab, **Try this page** to have a go with no skill, or **Deal with this** with a prompt and optional files.
2. The extension captures clicks, field values, navigations, and a few JPEG screenshots. It does not store passwords. Files on a job stay in the side panel; only names (and small text snippets) go to the API.
3. One model call plus heuristics split that one pass into three piles: a goal-level **playbook**, values that are **you**, and values that **change every run**. Navigations split the recording into stages locally (netloc + path); the model is not called once per page. One URL stays one stage. The skill is a draft until you review that split.
4. The review screen (not the Skills list) is where you confirm the piles, the starting URL / page kind, a strip of pages (rename, delete, merge), and whether to overwrite an existing memory fact. Only then is the skill ready.
5. **Run** sends the demonstration, screenshots, playbook, memory, and per-run inputs. If the skill has a start URL, Run focuses a tab already on that page, or opens one, and keeps later clicks on that tab. It does not take over a different tab that happens to be in front. The agent must repeat the demonstration, not invent a form fill from the accessibility tree alone. It finishes with `mark_goal_complete` or `mark_goal_blocked`, and lessons are stored on the skill. A recorded “Upload photos” click is not replayed, because that opens an OS file dialog the extension cannot finish.
6. **Try this page** is a run with no `skill_id`. The agent reads the tab and fills from memory. It does not wait for a plan. A fact that is already stored is looked up and the run continues with another model call. `ask_for_value` pauses only when nothing stored matches. The finish includes a `fill_report`. Keep as skill opens the review screen.
7. **Deal with this** compiles the prompt into a job card before any click: the goal, a crafted caption from kept examples if you asked for one, the procedure (one skill, a sequence, or a mix) plus its page names, the memory facts that will be used, the files, and the stop-before. “apply with my CV and transcript, stop before submit” on localhost points at Form A. Go starts the run. Two close skills are mixed and the run starts without asking which one. An official page on a different site stays open; the model fills it from stored facts, using related skills as hints. A caption you keep goes in the example book, not the playbook. A learned walk becomes a skill the same way an attempt does. A photo plus “post this” is a listing job: one vision call writes the title, a full description, price, condition, brand, colour, size, material, and measurements, then Vinted, eBay, and Facebook Marketplace are filled from that card without a model call per field. Go opens that site’s sell form. Depop is not in that list. If the sentence leaves out the price or the condition, the job card asks for those before Go. Dropdowns are opened and the matching option is chosen, including a control named “Brand Select a brand” or “Condition Select condition”. One flash call picks the parcel size from Small (a large envelope), Medium (a shoebox; a mug is Medium), or Large (a moving box). A recorded skill does the same, one menu at a time, and does not skip ahead to a later click. Blank spots keep ordinary second-hand mug details so the form is not sparse. Upload, List, and Publish wait for confirm. A sign-in wall pauses on that site; Resume continues there after you sign in. Tab moves focus to the next field when a step asks for it.
8. Skills that match the current tab (same host, or overlapping `page_kind` / title) rise to the top of the list. Jobs match skills against the **ask**, then navigate to the skill's page.
9. Submit / Pay / Place order / Publish is blocked in the executor **before** the click. Confirm, then the click runs. Pause can resume with a correction: voice notes become writing rules, page notes become skill lessons.
10. **Activity** lists past runs: the procedure, the stage reached, the facts used, the lesson, and Resume for a paused or blocked run. **Download profile and vault** zips confirmed facts plus the original vault files. Deleting a vault file also deletes facts that were cited only by that file. Listing Activity and exporting do not call the model.

It is not a macro recorder. The demonstration stays on the skill (`source_events`, `demonstration`, screenshot files). Memory is a soft profile: existing keys are never silently overwritten.

## Repo map

```
taskwipe/
  backend/                 Django API (SQLite)
    manage.py
    requirements.txt
    .env.example
    config/settings.py     env, models, CORS
    config/urls.py
    core/models.py         Skill (+ Stage), MemoryFact, Document, Recording, Run
    core/views.py          JSON endpoints
    core/http.py           csrf-exempt JSON helpers
    core/middleware.py     CORS for the extension
    core/services/compile.py
    core/services/split.py     three-pile split + demonstration
    core/services/commit.py    review → ready skill, no silent overwrite
    core/services/llm.py       Nebius completion; Claude only if that call fails
    core/services/web.py       Tavily search and extract for public pages only
    core/services/agent.py
    core/services/attempt.py    match skills + keep an attempt as a draft
    core/services/job.py       compile a prompt into a job card, pick skills, draft captions
    core/services/memory.py
    core/services/stages.py    split a recording at navigations; match/advance a run stage
    core/services/runner.py    local replay, compact history, login pause, page-kind fill
    core/services/pagekind.py  heuristic page classifier; V4 Pro only if unsure
    core/services/vault.py     extract text + propose facts at upload; delete file-cited facts
    core/services/activity.py  compact past-run cards
    core/services/export.py    zip profile JSON + vault files
    core/templates/demo/   local Form A, Form B, parcel checkout
  extension/               unpacked Chrome MV3
    manifest.json
    background.js          record buffer, screenshots, tab control
    content.js             recorder + a11y executor
    overlay/               Vite build output (do not edit by hand)
    overlay/                 React class-component side panel (source)
                             MemoryBook = Profile, Vault = files, Activity = past runs
  docs/HOW_IT_WAS_BUILT.md this file
  docs/NEXT.md             queued slices; not the current product
  docs/DEMOS.md            two in-depth local demos
  backend/core/demo_pack/  Maya Chen CV, transcript, two clips
```

There is no Next.js app. A Chrome extension cannot be a Next app. The side panel is Vite + React class components + Tailwind, built into `extension/overlay`.

The agent loop is HTTP, not Django Channels. The overlay POSTs `/api/runs/` then `/api/runs/<id>/turn/` with tool results until the run completes. That is the cheaper alternative to a websocket. Swap in Channels later if you want push; the Skill JSON and executor stay the same.

## Local setup

Need Python 3.11+ (this machine used 3.14 and Django 6.1), Node 20+, Chrome, and a Nebius key. Leave an Anthropic key in `backend/.env` for the fallback. On Windows, `--noreload` avoids the autoreloader hanging; PowerShell's `curl` is `Invoke-WebRequest`, so use a browser or Python to hit the API.

```powershell
cd C:\Users\epicd\Projects\nebiustaskwipe
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
copy backend\.env.example backend\.env
# put NEBIUS_API_KEY and ANTHROPIC_API_KEY in backend/.env
python backend\manage.py migrate
python backend\manage.py runserver 127.0.0.1:8001 --noreload
```

In a second terminal:

```powershell
cd overlay
npm install
npm run build
```

Load the extension:

1. Chrome → `chrome://extensions`
2. Enable Developer mode
3. Load unpacked → select the `extension` folder
4. Pin Taskwipe and open the side panel
5. Open `http://127.0.0.1:8001/demo/form-a/`

Taskwipe uses port **8001** so it does not collide with another Django app on 8000. Change the "Django API" field in the side panel if you need a different origin.

Health check: `GET http://127.0.0.1:8001/api/health/` returns `ok`, `has_nebius_key`, `has_tavily_key`, `has_anthropic_key`, `has_api_key` (true when either Nebius or Anthropic is set), and `compile_model`, `run_model`, `vision_model`.

## Who answers

A page whose labels still match the demonstration or stored facts is filled in Django. That path does not call a model and does not call Tavily. Opening Profile, Vault, or Activity does not call a model.

Tavily (`https://api.tavily.com`, `TAVILY_API_KEY`) fetches public pages. A job whose prompt asks what a public page requires searches once (`/search`, advanced, 5 results) when the start URL is not already known, then extracts at most 3 URLs (`/extract`, advanced, markdown, 3 chunks, query set to the goal). A public URL written in the prompt, or “this page” on a public tab, is extracted and not searched. `localhost`, `127.0.0.1`, `0.0.0.0`, `::1`, `chrome:`, `chrome-extension:`, and `file:` are never sent. Video and social hosts are dropped. A prompt about Imperial prefers `imperial.ac.uk`. A DeepSeek answer that stops because the token budget was spent on reasoning is retried once on Nebius with a larger budget before Claude runs. A localhost Form A job with no saved skill reads the page and fills matching labels locally. One extract is cached per URL on the run. If extract fails, the run continues with `read_page`. A Tavily miss does not call Claude. The model has a `web_search` tool for a fact that is not in memory and not on the page. It is not a substitute for `form_input`. The job card lists the public URLs and the provider that wrote the summary.

Every model decision goes to Nebius Token Factory first (`https://api.tokenfactory.nebius.com/v1/`, OpenAI SDK, `NEBIUS_API_KEY`). Text uses `RUN_MODEL` or `COMPILE_MODEL`. A request that contains an image uses `VISION_MODEL`. Those ids are passed through unchanged. They are not run through the Anthropic name alias. Defaults are `deepseek-ai/DeepSeek-V4-Pro-0813` and `deepseek-ai/DeepSeek-V4.1-Flash-1017`.

If that call throws, or the message has neither text nor a parseable tool call, Claude (`FALLBACK_MODEL`, default `claude-sonnet-5-5`) runs once with the same intent. The Anthropic client is constructed only inside that fallback. Dotted Claude ids are aliased to hyphens on the fallback only. The run checkpoint stores `provider` as `nebius` or `claude`, and the run payload includes it.

The model sees six browser actions as function tools: `read_page`, `left_click`, `form_input`, `type`, `screenshot`, `navigate`, plus `web_search`, `propose_plan`, `ask_for_value`, `attach_file`, `attach_from_vault`, `request_file_pick`, `request_user_confirm`, `mark_goal_complete`, and `mark_goal_blocked`. `browser_toolset_20260801` is not sent. At the HTTP boundary, assistant `tool_use` becomes `tool_calls` and a user `tool_result` becomes `role: "tool"`. The stored transcript and `pending_actions` stay `{tool_use_id, name, input}`. Thinking blocks and the thinking-beta header are dropped before the call. A screenshot is an `image_url` data URI, and only on `VISION_MODEL`.

## Data model

### Skill (a procedure)

A procedure is a Skill plus ordered Stage rows. A one-page skill is a procedure with one stage (`index=0`). Compile splits `source_events` at navigations and writes those stages; a one-page trace still gets one stage (and uses the model playbook). Commit replaces stages from the review payload, then rolls the piles up onto the skill. Keep-from-attempt still writes one stage from the skill fields. A run stores `stage_index` and a checkpoint. The agent is briefed only on the current stage. A URL change that matches the stage `exit` or the next `url_pattern` advances the stage in Django and does not call the model. If the page labels still match the demonstration, that stage fills locally.

| Field | Meaning |
| --- | --- |
| name, goal | Shown in the overlay |
| playbook | Full goal-level steps, edited on the review screen |
| inputs_schema | `this_run` pile: `{name, type, label, value_from_demo}` |
| memory_keys | Keys from the `yours` pile |
| start_url, site_host, page_kind | Where this skill applies |
| source_events, demonstration | The worked example sent on every run |
| screenshot_paths | JPEGs under `backend/media/skills/<id>/` |
| lessons | Notes written back after complete/blocked runs |
| ready | False until `POST /api/skills/<id>/commit/` |
| stages | Ordered pages. One-page skills have a single stage |

Existing skills were migrated to one stage: `url_pattern` from the path of `start_url`, plus the skill's `page_kind`, `playbook`, `demonstration`, and `memory_keys`. `vault_file_ids` and `exit` start empty.

### Stage

| Field | Meaning |
| --- | --- |
| index | 0-based order |
| name | Label on the review strip (`Personal details`) |
| url_pattern | Path this page matches (`/demo/form-a/`) |
| page_kind | In your words |
| playbook | Steps for this page (local heuristics when the recording spanned pages) |
| demonstration | Slice of the worked example |
| memory_keys | Profile keys this page uses |
| vault_file_ids | Document ids this page attaches |
| split_yours, split_this_run | This page's three-pile values |
| exit | Path of the next page, if any |

### CraftExample

| Field | Meaning |
| --- | --- |
| kind | `caption` / `title` / `description` / `post` / `rule` / `other` |
| text | The kept writing, or a rule such as “never smash that like” |
| notes | Optional one-liner on why it was kept |

This is the example book. Suitability is retrieved from these, not from MemoryFact. A job may draft a caption from them before it opens a browser. Nothing is stored until you keep it.

### MemoryFact

Existing rows were kept. Unique-on-key was dropped so two addresses can share `shipping.line1`. Values were not rewritten. New columns: `sensitivity=normal`, `source=recording` if the row had a `source_skill` else `typed`, `source_quote` empty, `is_default=true`, `group_key` empty.

| Field | Meaning |
| --- | --- |
| key | Dotted, boring: `email`, `shipping.postcode`. Not unique |
| label, value | Human text |
| category | `identity` / `shipping` / `work` / `study` / `sites` / `people` / `preferences` / `other` |
| sensitivity | `normal` / `private` / `never`. `never` is omitted from `memory_for_run` |
| confirmed | Suggestions stay false until accepted |
| rejected | Vault Skip: do not propose this key+value again |
| source | `typed` / `recording` / `file` / `run` |
| source_quote | Sentence or page the value came from |
| is_default | Which row to use when several share a key (or a `group_key` address) |
| group_key | Blank for a lone fact; `home` / `term` for a whole address |

Never stored: passwords, card numbers, CVV, tokens, SSNs. Existing keys are not overwritten unless the review sends `overwrite_keys` (or `POST /api/memory/` sends `overwrite`). A second value for the same key is created only when the request sends `add_another: true`. `memory_for_run` returns the default row for each key, only the categories that match the skill / page / goal, at most 30 facts, and never `sensitivity=never`. Opening the Profile pane is a database read. It does not call the model.

### Document

Original files live under `backend/media/vault/<id>/`. Upload extracts text once (PDF, DOCX, or plain text) and proposes unconfirmed facts with a source quote. A CV is split into first and last name, school vs other email, phone, university, degree, major, and years. A line that is a university, college, or transcript heading is not treated as a person's name. Nothing is confirmed until `POST /api/documents/<id>/accept/`. A job still uses those unconfirmed file facts for this run. Rejected suggestions stay rejected so the next upload does not ask again. Passwords and card numbers in a file are skipped, not stored as facts. Dropping a PDF/DOCX on Deal with this stores it in the vault so it can be parsed before Go.

A job may attach a vault file by id. The run receives the file id and title, not the extracted text. Dropped files in the side panel still work. Opening Profile, Vault, or Activity does not call the model. `DELETE /api/documents/<id>/` removes the file and any facts whose only citation is that file. Typed facts with the same key stay. `GET /api/export/` zips `profile.json` (confirmed facts, never `sensitivity=never`) and `vault/<id>/<filename>` originals.

| Field | Meaning |
| --- | --- |
| title | Editable name |
| original, mime, size | The uploaded file |
| extracted_text | Pulled out at upload; not sent on a run turn |
| skipped | Secrets that extraction refused |
| status | `stored` / `extracted` / `failed` |

### Recording / Run

A recording is events plus screenshots, compiled into a **draft** skill. A run is the conversation plus `current_url`, `stage_index`, and `checkpoint`. `kind` is `skill`, `attempt`, or `job` (`skill` may be null). A job stores the compiled card on `run.job`. Status includes `awaiting_job`, `awaiting_plan`, `awaiting_value`, `awaiting_file`, `completed` and `blocked`. Stopping without `mark_goal_complete` nudges once, then blocks and writes a lesson. Pause keeps the checkpoint; resume continues at that stage and does not replay earlier pages.

## Record / compile / review / run

### Record

`content.js` (all frames) listens for click, change, submit, and debounced keyup. It walks shadow DOM and same-origin iframes. Cross-origin frames get their own content script. Off-screen controls are listed and tagged `[offscreen]`.

Dangerous clicks return `blocked: true` **without** clicking. The overlay asks, then re-sends `allowDangerous: true`. Enter does not call `requestSubmit`.

### Compile

`POST /api/recordings/` → draft skill. Heuristics in `split.py` plus one model call for name, goal, yours, this_run, and the overall playbook. Screenshots in that call use `VISION_MODEL`. Stages are then cut locally at `navigate` events whose netloc+path changed. A same-URL reload stays one stage. Does **not** write MemoryFact yet. Overlay opens the review pane, not the Skills list.

### Review

`POST /api/skills/<id>/commit/` with the three piles, start URL, page kind, optional `stages`, and optional `overwrite_keys`. Then the skill is ready. The review strip lets you rename, delete, or merge a page before that commit. Existing facts are still never overwritten unless `overwrite_keys` is sent.

### Run

`POST /api/runs/` `{ "skill_id", "inputs", "current_url", "force" }` or `{ "intent", "current_url", "tab_title" }` with no skill.

Run calls `focusUrl` first: an existing tab with the same host and path is focused, otherwise a new tab opens on `start_url`. Actions for that run keep the returned tab id, so a later click in another tab does not steal the fill. If that focus fails and `site_host` does not match the tab, the overlay asks and must send `force: true`. Attempts and forced cross-site skill runs set `needs_plan`. That does not pause for a plan: the model reads the page and fills. A stored fact is looked up instead of asking.

The first model message includes the current stage's playbook and demonstration, at most 30 memory facts, this-run inputs, earlier lessons, and (on the first model call only) up to two compile screenshots. Later turns compact `run.messages`: screenshots and old page dumps are stripped, and document bodies never go in the payload. An attempt has memory and intent, no demonstration.

If the current page's labels still match the demonstration, Django emits a batched `form_input` list and does not call the model. After a model turn on one page, later pages can still fill locally; local tool ids are not appended onto the stored transcript. Empty this-run fields are skipped, not sent to the model. A demonstrated Continue / Next is clicked with the fills. A label from the demonstration that is not on this page does not block that click. Submit / Pay / Place order / Publish are never in that local list. A filled page that is not the last one keeps going: it follows the stage exit, and does not ask whether it may continue. The only confirm is the last page's Submit, Pay, Place order, or Publish. A login wall pauses and asks the person to sign in; cookies are not stored. A later page on the same site still counts as that skill: `www.ebay.co.uk` and `ebay.co.uk` match, and an eBay draft URL is not treated as the wrong site. A different site still pauses.

Captcha, recaptcha, and similar iframes are ignored. The current URL stays the real tab. Each page dump is classified before a model call: heuristic first, one `COMPILE_MODEL` call only if that is unsure, and at most once. The kinds are `expected` (fill this stage), `next_stage` (URL moved, advance, no model), `login` (pause), `blocker` (dismiss cookies/modals with a local click), and `wrong` (pause on a known skill; an off-script job still calls the model). An expected application page fills every label that matches stored facts in one batch, including a school-email field, and does not click Submit. Page dumps keep form controls first and drop offscreen nav links, so a mega-menu cannot hide the fields. Local fills still run when some demonstration keys are messy; leftover `Select an option` dropdowns go to the model as `left_click`, not another `read_page`. A file control that already has a filename, or a year dropdown that already shows Year 3, is not leftover: Continue is clicked locally so the run does not loop on screenshots. A screenshot/scroll-only model turn is rejected once and the model is told to click. That rejection still writes `tool_result` blocks for the skipped tools so the next model call is valid. A too-early `mark_goal_complete` is rejected once and the model is asked again. Empty `pending_actions` while the run is still `running` posts at most two extra turns instead of looping.

The Imperial internship job fills personal details and address from the profile with no model call, may call the model once for education (year from a CV often does not match the dropdown), attaches the transcript by document id on the upload page, and the review page does not call the model. From Go to the confirm-before-submit, that flow is at most four model calls.

Tools: the six browser function tools plus `propose_plan`, `ask_for_value`, `attach_file`, `attach_from_vault`, `request_file_pick`, `request_user_confirm`, `mark_goal_complete`, and `mark_goal_blocked`.

Statuses: `awaiting_value` (question + remember, only when the fact is not already stored), `awaiting_confirm`. A proposed plan does not pause; the next model call fills. Pause stashes leftover tool results and keeps `stage_index`. Resume may send a correction. `POST /api/runs/<id>/keep/` turns a finished attempt into a draft skill. `POST /api/runs/<id>/turn/` may include `current_url` so a Continue click can advance the stage.

### Job (Deal with this)

`POST /api/jobs/` `{ "prompt", "attachments": [{name, media_type, size, injectable, text}], "current_url", "tab_title" }`.

Heuristics in `job.py` (one model call to draft a caption when a key is present) split the sentence into a **goal**, **memory cues** (“suitable”, “like last time”), **this-run** files, and optional **crafts**. Matching is against the goal, not the current tab. Localhost + apply/internship/CV/transcript points at `/demo/form-a/`; localhost + youtube/upload points at `/demo/studio/` so live YouTube Studio is not the only path. A known application skill is picked even when the prompt only overlaps “apply”. With no skill yet, localhost apply is still Form A: the card lists the four pages, shipping facts are included, and matching labels fill locally (Continue is clicked; a year dropdown is not skipped). “stop before submit” sets `stop_before` to Submit. If the prompt names a CV or transcript and those files are already in the vault, they are attached by id even when the overlay did not drop them again. Go binds the transcript vault id onto the upload stage.

- `mode: remember` — the prompt is a rule, no browser. A CraftExample is saved and the run completes.
- `procedure.status: known | sequence | learn | mix` — pick the ready skill, run every distinct skill the prompt names (in that order), learn on the start URL, or mix close skills without asking which one. “Apply for the internship and send the parcel” is a sequence. “Upload a video” versus “upload a Short” on the same sentence is a mix: both are hints, and Go does not ask you to pick. The confirm is still only the last skill's Submit / Pay / Publish; finishing one skill starts the next without asking. A job application on a different site, or a link pasted in the prompt such as an official apply page, sets `off_script`: that page is the one that gets filled, from stored facts, and close skills are mixed without a pick. The card keeps facts, skills, and files behind Details. Go is the only click before it starts.
- Crafts are drafted from kept examples (or left empty). The overlay shows the card, including the memory facts that will be used; **Go** is `POST /api/jobs/<id>/go/`.

Go navigates to `start_url`, except when `off_script` is set, in which case the current official page stays open. Known skills on their own site skip the model when the labels still match. Learning, a mix, and an off-script page fill with a model call and do not wait for a plan. `attach_file` injects files under 8MB; larger files pause on `request_file_pick`. Publish is treated like Submit. Keep as skill and Keep this writing are independent: the walk is a procedure, the caption is an example.

The overlay executes `pending_actions` in the live tab, then:

`POST /api/runs/<id>/turn/` `{ "tool_results": [ { "tool_use_id", "toolset_name": "browser", "content": ... } ], "current_url", "tab_title" }`

Browser member results must echo `toolset_name`. Synthesized closes of an open browser `tool_use` (and overlay results that omitted the field) copy it from the paired `tool_use`. Screenshots are image blocks. Navigate results may include `browser_state`.

### Executor refs

`read_page` walks visible interactive nodes and tags them `[ref_1]`. Refs stay stable until navigation. A stale ref returns an error string asking Claude to re-read. Coordinates are fallback only. `form_input` sets the value with the native setter so React/Google Forms see the change.

Dangerous clicks (Submit, Pay, Place order, Send, Publish, …) pause in the overlay even if the model forgets `request_user_confirm`. A Continue or Next control is not blocked just because it is `type="submit"`. Enter is blocked only when that form's submit control is one of those irreversible actions. `request_user_confirm` is ignored unless its summary names an irreversible action (Submit, Pay, Place order, Purchase, Send, Publish), and any fills in that same turn run before the confirm.

`read_page` walks shadow roots and same-origin iframes, and asks every frame (refs look like `[f0::ref_3]`). Off-screen controls are included and tagged. A file input is included even when it is hidden, so a vault photo can be attached without opening the system file dialog. Dangerous clicks are blocked until `allowDangerous`. Implemented members: `navigate`, `screenshot`, `left_click`, `double_click`, `hover`, `scroll`, `scroll_to`, `type`, `key`, `wait`, `read_page`, `find`, `get_page_text`, `form_input`, `attach_file`. `attach_from_vault` downloads `/api/documents/<id>/file/` in the overlay and then uses `attach_file`; the turn never receives extracted text.

## How to rebuild from an empty folder

1. Create the folders in the repo map.
2. Copy `backend/` as-is. The API is plain Django JSON views, no DRF, no Channels, no auth.
3. Copy `overlay/` as-is. Class components only (`App`, `RecordDock`, `SkillList`, `MemoryBook` as the Profile pane, `Vault`, `Activity`, `RunRibbon`, `ReviewPane`). No hooks. RecordDock has Deal with this, Record this, and Try this page. Profile is grouped by category; suggestions stay unconfirmed until Keep. Vault lists files and the accept/reject screen. Activity lists past runs and can export the profile zip.
4. Copy `extension/manifest.json`, `background.js`, `content.js`. Run `npm run build` in `overlay` to produce `extension/overlay`.
5. `migrate`, set the key, load unpacked, open a demo page.
6. Model calls go through `core/services/llm.py`. The six browser actions are custom function tools. Keep the Skill JSON schema.

Decisions you can reverse without rewriting the product:

- HTTP turns instead of Django Channels
- Vite overlay instead of Next.js
- SQLite instead of Postgres
- Custom tools instead of `browser_toolset_20260801`

## Demo setup

The run of show is `http://127.0.0.1:8001/demo/` and `docs/DEMOS.md`. Two demos, one persona (Maya Chen). Pack files are at `/demo/files/<name>/`.

**Demo 1 — Apply** (~12 min). Vault the CV and transcript. Deal with this: “apply with my CV and transcript, stop before submit”. Form A is four pages: personal details and address → education → transcript upload → review. Personal details fill locally; education is at most one model call; the transcript attaches by id; review does not call the model; Submit waits. Then Form B (navy, different labels, cookie overlay) with Try this page. Activity and export do not call the model. Optional: record Form A yourself (three Continues → four stages) and Keep this skill.

**Demo 2 — Send, then post** (~10 min). `remember: never smash that like…` saves a writing rule with no browser. Record the parcel page; name/phone/address are yours; stop before Place order; run it again from memory. Creator desk: drop clip-one, “upload this with a suitable caption”, keep the writing; clip-two reuses it. Stop before Publish. Do not make live YouTube Studio the only path.

Pages:

1. `http://127.0.0.1:8001/demo/form-a/` — Imperial internship (serif, cream), four pages.
2. `http://127.0.0.1:8001/demo/form-b/` — Student ambassador (navy, different labels). Cookie banner is “Accept cookies”.
3. `http://127.0.0.1:8001/demo/parcel/` — Address checkout. Stop before Place order.
4. `http://127.0.0.1:8001/demo/studio/` — Creator desk. Stop before Publish.

Demo forms treat fields as one-time-code (not `type=email`) and keep them readonly until focus so Chrome does not drop the presenter's saved name, email, or address onto Maya's pages.

Live pitch (real sites):

1. Create two Google Forms with the same facts and different labels. Record A, run on B. Be logged into Google first. No 2FA mid-talk.
2. Memory-heavy second demo: a real send-a-parcel / checkout address page you are already logged into, **stop before payment**. Fallback: Gmail compose or Google Calendar, with a per-run subject/time input.

Do not make live YouTube Studio the only path.

## API cheat sheet

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health/` | Overlay ping; Nebius, Tavily, and Anthropic key flags plus the three model ids |
| GET/POST | `/api/skills/` | List / create |
| GET | `/api/skills/match/?url=&title=` | Ready skills that fit this tab |
| DELETE | `/api/skills/<id>/` | Forget skill |
| POST | `/api/skills/<id>/commit/` | Review the three piles and pages; skill becomes ready |
| GET/POST | `/api/memory/` | List / add fact (no silent overwrite; `add_another` for a second value) |
| PATCH/DELETE | `/api/memory/<id>/` | Edit / delete |
| POST | `/api/recordings/` | Compile demonstration into a draft skill |
| GET | `/api/runs/` | Activity cards: procedure, stage reached, facts used, lesson, resumable |
| POST | `/api/runs/` | `{ skill_id, inputs, current_url, force }` or `{ intent, current_url, tab_title }` |
| POST | `/api/runs/<id>/turn/` | Continue with tool results (optional `current_url`) |
| POST | `/api/runs/<id>/confirm/` | `{ "approved": true }` |
| POST | `/api/runs/<id>/plan/` | `{ "approved": true }` |
| POST | `/api/runs/<id>/answer/` | `{ "value", "remember" }` |
| POST | `/api/runs/<id>/resume/` | `{ "correction", "remember" }` |
| POST | `/api/runs/<id>/keep/` | Attempt → draft skill, then review |
| POST | `/api/runs/<id>/pause/` | Stop from overlay |
| POST | `/api/runs/<id>/file/` | `{ "done": true }` after an OS file picker |
| GET/POST | `/api/examples/` | List / keep a caption, title, or writing rule |
| DELETE | `/api/examples/<id>/` | Forget an example |
| POST | `/api/jobs/` | Compile a prompt + files into a job card (`attachments`, optional `vault_ids`) |
| POST | `/api/jobs/<id>/go/` | `{ skill_id, crafts, keep_crafts, answers, current_url }` then run |
| GET/POST | `/api/documents/` | List / store a file, extract, propose facts (multipart `file`) |
| GET/PATCH/DELETE | `/api/documents/<id>/` | Detail (includes `extracted_text` + proposals), retitle; delete also drops facts cited only by that file |
| GET | `/api/export/` | Zip of `profile.json` and vault originals |
| GET | `/api/documents/<id>/file/` | Download the original |
| POST | `/api/documents/<id>/accept/` | `{ "fact_ids", "overwrite_keys" }` confirm proposed facts |
| POST | `/api/documents/<id>/reject/` | `{ "fact_ids" }` remember not to ask again |
| GET/POST | `/api/skills/<id>/stages/` | List / append a stage |
| GET/PATCH/DELETE | `/api/skills/<id>/stages/<stage_id>/` | Edit a stage; a lone stage copies playbook back onto the skill |
