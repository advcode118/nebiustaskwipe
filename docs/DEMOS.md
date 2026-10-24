# Two demos

These are the two in-depth runs for Taskwipe on this machine. The pages live at `http://127.0.0.1:8001/demo/`. The side panel is the Chrome extension.

Do Demo 1 first. It is the whole stack: vault, a four-page job, confirm before Submit, then the same facts on a page with different labels. Demo 2 is memory plus writing: an address checkout, then a caption you keep and reuse.

Submit, Place order, and Publish always wait. If a demo ever clicks those without asking, stop and treat it as a bug.

## Machine setup

Need Python 3.11+, Node 20+, Chrome, and `NEBIUS_API_KEY` in `backend/.env`. Leave `ANTHROPIC_API_KEY` there too; Claude runs only if a Nebius call fails. Taskwipe uses port **8001**.

```powershell
cd C:\Users\epicd\Projects\nebiustaskwipe
.\.venv\Scripts\Activate.ps1
python backend\manage.py runserver 127.0.0.1:8001 --noreload
```

Load unpacked: Chrome → `chrome://extensions` → Developer mode → Load unpacked → `extension` folder. Pin Taskwipe and open the side panel. Health check: `GET http://127.0.0.1:8001/api/health/` should return `"ok": true` and `"has_api_key": true`.

The Django API field in the panel should be `http://127.0.0.1:8001`. Opening the panel, Profile, Vault, or Activity must not call the model.

Use a clean profile if last week's facts will confuse the story. You can delete vault files and leftover skills from the panel. Deleting a vault file also deletes facts cited only by that file.

## Persona

Every typed value in both demos is this person. The pack files already parse to these keys.

| What | Value | Typical key |
| --- | --- | --- |
| Full name | Maya Chen | `full_name` / `first_name` + `last_name` |
| University email | maya.chen24@imperial.ac.uk | `school_email` |
| Phone | +44 7700 900123 | `phone` |
| Address | 14 Exhibition Road | `shipping.line1` (type this in Profile; the CV only guarantees postcode) |
| City | London | type in Profile |
| Postcode | SW7 2AZ | `shipping.postcode` |
| University | Imperial College London | `university` |
| Course | MEng Computer Science | `degree`, `major`, `course` |
| Years | 2024–2028, Year 3 | `study.start_year`, `study.end_year`; the Year 3 dropdown often needs one model call |
| Parcel notes | Leave with the porter | this-run, not memory |
| Internship why | I want to spend the summer on systems work and teaching | this-run |

Pack files (also linked from `/demo/`):

- `/demo/files/maya-chen-cv.docx/`
- `/demo/files/maya-chen-transcript.docx/`
- `/demo/files/clip-one.mp4/`
- `/demo/files/clip-two.mp4/`
- `/demo/files/caption-rule.txt/` (optional, for the writing-rule beat)

Plain-text copies sit next to the `.docx` files if you want to read them.

---

## Demo 1 — Apply with my CV and transcript

**What this proves.** A prompt plus files becomes a job card. The agent fills a four-page application from stored facts, attaches the transcript by vault id, and stops before Submit. A second site with different labels uses the same memory. Listing Activity and exporting do not call the model.

**Time.** About 12 minutes if the vault is empty. Add 6 minutes if you also record Form A by hand.

**Talking line.** "I am not recording a macro. I am handing it the task and the files. It already knows who I am."

### Act I — Vault and Profile

1. Open `http://127.0.0.1:8001/demo/form-a/` so the tab is the internship, not the hub.
2. In the side panel, open **Vault**.
3. Upload `maya-chen-cv.docx`, then `maya-chen-transcript.docx`. Extraction runs at upload, not later inside a run turn.
4. Open the CV. Keep every sensible suggestion: first name, last name, school email, phone, university, degree, major, start/end year, postcode. Skip only if a line is junk.
5. Open the transcript the same way. Duplicate keys are fine; do not overwrite unless the new value is actually better. Existing facts are never silently overwritten.
6. Open **Profile**. If Address line 1 or City are missing, add them by hand:

   - key `shipping.line1`, label Address line 1, value `14 Exhibition Road`, category `shipping`
   - key `city` or `shipping.city`, label City, value `London`, category `shipping`

   Profile is a database read. It does not call the model.

**You should see.** Suggestions stay unconfirmed until Keep. Each one has a source quote from the file. Card numbers would have been skipped; these files have none.

**If it goes wrong.** Empty suggestions usually mean the wrong file, or a scanned PDF with no text. Use the `.txt` copies from the same folder; they parse the same way. If Keep is blocked by a conflict, that is the no-silent-overwrite rule — tick overwrite only if you mean it.

### Act II — The job on Form A

1. Stay on Form A page 1 (cream, serif, "Full legal name").
2. In **What are we doing?**, type exactly:

   `apply with my CV and transcript, stop before submit`

3. Drop the CV and transcript onto the dashed well if they are not already listed. Click **Deal with this**.
4. Wait for the job card: **I'll deal with this**. Open **Details**.

   **Point at:**
   - Goal is an application, not "whatever is on this tab".
   - Procedure stages: personal → education → transcript → review.
   - Stored data: Maya's name, school email, address, course.
   - This run: the transcript file, by name, not the extracted body.
   - The line **It will stop before Submit.**

5. Click **Go**. Do not touch the form.

**What happens, in order.**

| Page | What fills | Model? |
| --- | --- | --- |
| Personal details and address | Full legal name, university email, line 1, city, postcode | No, if the labels still match stored facts |
| Education | Course from the CV; Year of study dropdown | At most one call. Year 3 often does not match a raw `2028` |
| Transcript upload | The vault transcript attaches by document id | No document body in the turn |
| Review | "Why this internship" may stay blank or ask once | No model for the rest of the page |
| Submit application | Overlay confirm | Never auto-clicked |

From Go to that confirm, at most four model calls. URL changes advance the stage in Django and do not call the model.

6. When the overlay asks to confirm Submit, **stop and show the filled pages**. Clicking confirm is optional. Clicking Don't is the safer pitch.

**You should see.** Fields fill in a batch, not one keystroke at a time. Continue is clicked for you. The tab never leaves localhost.

**If it goes wrong.**

- Host mismatch: you started on `/demo/` instead of Form A. Open Form A and Deal with this again, or let it navigate — localhost apply/CV/transcript is supposed to point at `/demo/form-a/`.
- It asks for email you already stored: answer it, then check Profile; a `never` sensitivity or unconfirmed-only miss can do that. A job is allowed to use unconfirmed file facts.
- Education sits on "Choose one": that is the dropdown beat. Give it a few seconds. If it asks you, type `Year 3` and remember it if you want.
- It tries to screenshot forever: pause, send a correction "click Continue", resume.
- Submit fires with no prompt: stop the demo. That is a product failure.

### Act III — Form B, different labels

1. Open `http://127.0.0.1:8001/demo/form-b/`. Navy page, "Student ambassador intake".
2. If **Accept cookies** is up, you may click it, or let the run dismiss it locally. Do not make a speech about cookies; one sentence is enough: "A banner is a local click, not a model call."
3. Click **Try this page** (no skill, fills from memory) **or** select the internship skill and **Run on this tab**.

   Try this page is the cleaner story: "I have never recorded this form."

4. Watch Name, Email address, Programme, Graduation year. Motivation is this-run; if it pauses with `ask_for_value`, type:

   `I want to spend the summer on systems work and teaching`

   Tick remember only if you want that sentence in the profile. Usually leave it as this-run.

5. **Send intake form** still waits for confirm.

**You should see.** The labels are not the same as Form A (`Name` vs `Full legal name`, `Email address` vs `University email`). Memory still matches. This is the "not a macro" beat.

**If it goes wrong.** If it wants to navigate back to Form A, you forced a known skill on the wrong host — use Try this page instead. If Programme stays empty, the course fact might be stored as `major` only; answer once and remember.

### Act IV — Activity and export

1. Open **Activity**. Read the last two cards out loud: procedure, stage reached, facts used, any lesson.
2. Click **Download profile and vault**. Unzip it later if you want; `profile.json` is confirmed facts, never `sensitivity=never`, plus the original vault files.

No model call for listing or exporting.

### Optional Act 0 — Record Form A yourself

Do this when the audience needs to see compile, not just Deal with this.

1. Empty Form A page 1. **Record this**.
2. Type Maya's personal details. Click **Continue**.
3. Course `MEng Computer Science`, Year of study `Year 3`. Continue.
4. Attach `maya-chen-transcript.docx`. Continue.
5. Type a one-line why. **Stop before Submit application.**
6. **That's the task.** Wait for compile.
7. Review screen, not the Skills list:
   - Four pages on the strip. Rename them if the names are vague (`Personal`, `Education`, `Transcript`, `Review`).
   - Yours: name, email, address, course, year.
   - This-run: the why, maybe the file.
   - Do not tick overwrite unless you mean to replace Maya's profile.
8. **Keep this skill.**
9. Jump to Act III and **Run on this tab** on Form B.

A same-URL reload stays one stage. Three Continues to new paths become four stages.

---

## Demo 2 — Send the parcel, then post the clip

**What this proves.** Values that are you stay in the profile. A writing rule is stored without a browser. A prompt with a file drafts a caption from kept examples, attaches the clip, and stops before Publish. The second clip reuses the voice. Place order and Publish are the same kind of gate as Submit.

**Time.** About 10 minutes. Skip the writing-rule beat if you are short on time; do not skip the second clip.

**Talking line.** "The address is me. The caption is how I write. Neither is baked into a playbook."

### Act I — Remember, no page

1. Any tab is fine, including `/demo/studio/`.
2. Prompt:

   `remember: never smash that like. keep captions dry and specific`

3. **Deal with this**. There is no Go onto a site. The run completes. That sentence is now an example (`kind: rule`), not a MemoryFact.

**If it goes wrong.** If it opens a browser anyway, the prompt was parsed as a job. Start the sentence with `remember:`.

### Act II — Record the parcel once

1. Open `http://127.0.0.1:8001/demo/parcel/`.
2. **Record this**. Fill:

   - Full name: Maya Chen
   - Phone: +44 7700 900123
   - Address line 1: 14 Exhibition Road
   - City: London
   - Postcode: SW7 2AZ
   - Delivery notes: Leave with the porter

3. Do not click **Place order**. **That's the task**.
4. On review: name, phone, and address are yours. Notes can move to this-run if the split put them in yours. **Keep this skill**.

**You should see.** Place order sits below a dashed paywall. The executor will refuse that click until you confirm, even on a later run.

### Act III — The same parcel from memory

1. Reload the parcel page so the form is blank.
2. Either **Run on this tab** on the parcel skill, or Deal with this:

   `send this parcel, stop before Place order`

3. Go. Watch the address fill. Stop on the confirm. Prefer **Don't** unless you are showing that confirm actually clicks.

**If it goes wrong.** Empty phone usually means Act I of Demo 1 was skipped and the CV was never kept. Paste the phone once, remember it, resume. If it wants card details, that is out of scope — this page has no card fields on purpose.

### Act IV — First clip, keep the writing

1. Open `http://127.0.0.1:8001/demo/studio/`. This desk exists so live YouTube Studio is not the only path.
2. Drop `clip-one.mp4` on the panel. Prompt:

   `upload this with a suitable caption`

3. **Deal with this**. The card should include a drafted caption (Sonnet, from kept examples if you did Act I). Edit it if the draft is hype:

   Title: `Year 3 lab bench, no talking`
   Caption: `Short walkthrough of the bench setup for the computing internship. Dry, specific, no like-begging.`

4. Tick **Keep this writing if it was good**. **Go**.
5. Visibility: Private or Unlisted. **Publish** waits. Confirm or Don't.

**You should see.** It navigates to `/demo/studio/` when the ask is upload/youtube and the tab is localhost. The file is this-run. The caption is a craft, not a playbook step.

### Act V — Second clip, same voice

1. Reload the desk so title and description are empty.
2. Remove clip-one from the well if it is still there. Drop `clip-two.mp4`.
3. Same prompt: `upload this with a suitable caption`.
4. The drafted text should sound like the one you kept, not a new brand voice.
5. Go. Stop before Publish.
6. Optionally **Keep as skill** for the walk, and **Keep this writing** if you edited again. Those two keeps are independent.

**If it goes wrong.** If it opens real YouTube, you were not on localhost or the job inferred a live start URL — drag the tab back to `/demo/studio/` and Go with the current page. If the second caption is unrelated, the first keep did not save; check the example list after the first run. If the file is larger than 8MB (these clips are not), it will pause on the OS picker.

---

## What to say, and what not to fake

Say:

- The demonstration stays on the skill. Memory is a soft profile.
- Passwords, card numbers, CVV, and tokens are never stored.
- Submit / Pay / Place order / Publish are blocked in the executor before the click.
- Opening Profile, Vault, or Activity does not call the model.
- A known page with matching labels fills locally.

Do not:

- Log into a site that will 2FA mid-talk.
- Make live YouTube Studio the only path.
- Overwrite Maya's email to "show editing" unless you tick overwrite on purpose.
- Run both demos in one breath. Demo 1 then a break, then Demo 2, or Demo 1 alone.

## Live fallback, only after these two work

If localhost is clean and you want a live pitch:

1. Two Google Forms with Maya's facts and different labels. Record A, run on B. Be logged into Google first.
2. A real send-a-parcel or checkout you are already logged into. Stop before payment. Fallback: Gmail compose or Calendar, with a per-run subject or time.

If a live site fights the executor, that is why these local pages exist. Come back here.

## Reset between run-throughs

- Vault: delete the demo files if you want Keep/Skip to play again. Facts cited only by those files go with them. Typed address rows stay unless you also delete them in Profile.
- Skills: Forget skill on anything you do not want matching the next tab.
- Examples: delete the kept caption if Demo 2 Act V must draft from nothing.
- Demo forms suppress Chrome's saved name, email, and address. Reload the tab after a wipe so leftover field values and a stale side-panel run are gone.

The hub at `/demo/` is the run of show. This file is the same two demos with the failure cases written out.
