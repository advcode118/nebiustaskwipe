# Hackathon build — Tavily + Nebius, Claude only if that call fails

Work in this repo. `docs/HOW_IT_WAS_BUILT.md` is the product. This file is the only change: who answers each step.

Do not rebuild the extension, the overlay, the recorder, the executor, or the demo pages. Do not start a slice from `docs/NEXT.md`.

## You, before the Cursor chat

1. Token Factory → API keys → create a key. Tavily → API key (the account that shows the credits page you already have).
2. Put both in `backend/.env`. Leave `ANTHROPIC_API_KEY` there too, for the fallback. Do not paste any key into chat.

```
NEBIUS_API_KEY=
TAVILY_API_KEY=
ANTHROPIC_API_KEY=
RUN_MODEL=deepseek-ai/DeepSeek-V4-Pro-0813
COMPILE_MODEL=deepseek-ai/DeepSeek-V4-Pro-0813
VISION_MODEL=deepseek-ai/DeepSeek-V4.1-Flash-1017
FALLBACK_MODEL=claude-sonnet-5-5
```

3. New Agent chat. Paste only the kickoff. Wait until it hits the Stop line. Then run the demo at the bottom of this file.

## Kickoff

```
Read docs/HOW_IT_WAS_BUILT.md. That is the product. Do not rebuild it.

Read docs/HACKATHON.md and implement that file only. Tavily fetches public pages. DeepSeek V4 Pro on Nebius Token Factory makes every model decision. Claude runs only when that Nebius call fails. The overlay, content.js, and pending_actions stay as they are.

Stop when the Stop line in docs/HACKATHON.md is met.
```

## Who does what

| Step | Who |
| --- | --- |
| Labels on the live tab still match the demonstration or stored facts | Django. No Tavily, no model. |
| The task needs a public page the user is not already on | Tavily search, then Tavily extract. Then one DeepSeek call to turn that text into the job card. |
| The live tab is a public `https` page and local fill cannot do it | Tavily extract of that URL, then one DeepSeek call. |
| The live tab is `localhost`, `127.0.0.1`, or any non-public URL | Extension `read_page`. Tavily never sees it. Then one DeepSeek call only if local fill cannot do the page. |
| A screenshot is actually in the request | `VISION_MODEL` (DeepSeek V4.1 Flash). V4 Pro is text-only. |
| Nebius times out, returns 429/5xx, or comes back with no text and no valid tool call | One retry on Claude (`FALLBACK_MODEL`). Record `provider: claude` on that turn. |
| Opening Profile, Vault, or Activity | No Tavily, no model. |

Skip batch, fast, LoRA, and fine-tune ids. Skip DeepSeek-R1. Those are the wrong shape for a live tool-calling demo. Pass model ids through unchanged. Do not run them through the Anthropic name alias in `settings.py`.

Nebius: OpenAI SDK, `base_url` `https://api.tokenfactory.nebius.com/v1/`, `NEBIUS_API_KEY`. Tools are `{"type":"function","function":{name,description,parameters}}`. Results come back as `tool_calls`. A screenshot is an `image_url` part with `data:image/jpeg;base64,…`, and only on `VISION_MODEL`.

## Tavily

New module `backend/core/services/web.py`. Stdlib or `requests` only. No new agent framework.

Auth: `Authorization: Bearer <TAVILY_API_KEY>`. Base `https://api.tavily.com`.

Search, once per job, only when the start URL is not already known:

`POST /search` with `search_depth: "advanced"`, `max_results: 5`, `include_answer: false`, `include_raw_content: false`.

Extract, on the top result URLs (at most 3) or on the current public tab:

`POST /extract` with `extract_depth: "advanced"`, `format: "markdown"`, `chunks_per_source: 3`, and `query` set to the goal.

That is 2 search credits plus 2 extract credits. Use advanced. Do not call `/crawl` or `/map` on the demo path. A crawl can sit for two minutes.

Refuse Tavily for `localhost`, `127.0.0.1`, `0.0.0.0`, `::1`, `chrome:`, `chrome-extension:`, and `file:`. If extract fails, continue with `read_page`. Do not fall through to Claude just because Tavily missed.

Cache a URL's extract on the run. One extract per URL per run.

Add one agent tool, `web_search`, implemented as that same search-then-extract. The model uses it for a fact that is not in memory and not on the page. It is not a substitute for `form_input`.

## Model client

Anthropic is constructed only in `backend/core/services/agent.py` and `backend/core/services/compile.py`. `job.py` and `pagekind.py` use `compile._client`.

One Nebius client. Claude client only inside the fallback. Order on every completion:

1. Call Nebius. Text requests use `RUN_MODEL` / `COMPILE_MODEL` (both V4 Pro). A request that contains an image uses `VISION_MODEL`.
2. If the call throws, or the message has neither text nor a parseable tool call, call Claude once with the same intent.
3. Store which provider answered.

`browser_toolset_20260801` does not exist on Nebius. Expose the six browser actions already named in `HOW_IT_WAS_BUILT.md` (`read_page`, `left_click`, `form_input`, `type`, `screenshot`, `navigate`) plus `web_search` and the goal tools the loop already has (`propose_plan`, `ask_for_value`, `attach_file`, `attach_from_vault`, `request_file_pick`, `request_user_confirm`, `mark_goal_complete`, `mark_goal_blocked`).

At the boundary:

- assistant `tool_use` → assistant `tool_calls`
- user `tool_result` → `role: "tool"`
- drop Anthropic `thinking` / `redacted_thinking` and the thinking-beta header
- keep `pending_actions` as `{tool_use_id, name, input}` so `content.js` does not change

Job compile (`job.py`): Tavily first when the prompt needs a public page, then one V4 Pro call for the card and any caption. Do not call Claude to draft the caption.

Page kind (`pagekind.py`): heuristic first, as now. If still unsure, one V4 Pro call. Not Haiku, not Tavily (the dump is already in hand).

`GET /api/health/` reports `has_nebius_key`, `has_tavily_key`, `has_anthropic_key`, and the three model ids.

Class components only. Passwords and card numbers stay unstored. Submit, Pay, Place order, and Publish stay blocked until the person confirms. Existing facts stay unless `overwrite_keys` is sent.

## Stop

`python backend/manage.py test core` is green. Health shows the Nebius and Tavily keys. A test covers: a localhost URL does not call Tavily; a public job search calls `/search` then `/extract` and then one chat completion; a failed Nebius completion is retried once on Claude.

Manual: server `python backend/manage.py runserver 127.0.0.1:8001 --noreload`. Demo 1 Act II in `docs/DEMOS.md` still fills Form A personal details with no model call and stops before Submit. Then one public prompt, "what does the Imperial summer internship require, using my CV", returns Tavily sources plus a V4 Pro answer, and the panel can say the provider was Nebius.

## Demo, after the Stop line

1. Vault Maya's CV. On Form A: `apply with my CV and transcript, stop before submit`. Go. Local fill. Stop on Submit. Say: this page never left the machine, so Tavily was not involved.
2. New prompt, any tab: `what does the Imperial College summer internship actually ask for, and which of my saved facts cover it`. The card should show the public URLs Tavily returned. Say: search and extract were Tavily; the judgment was DeepSeek V4 Pro; Claude did not run.
3. If a turn badge says `claude`, that is the failure path. Say so. Do not hide it.
