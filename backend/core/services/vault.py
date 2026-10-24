import re
from pathlib import Path

from core.models import MemoryFact
from core.services.memory import STABLE_HINTS, fact_for_key, is_forbidden, set_as_default

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{8,16}\d")
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.I)
UNI_RE = re.compile(
    r"\b(University of [^,\n]+|Imperial College(?: London)?|[^,\n]{2,60} (?:University|College|School|Institute))\b",
    re.I,
)
DEGREE_RE = re.compile(r"\b(MEng|BEng|BSc|BA|MSc|MA|MBA|MPhil|MSci|PhD|DPhil)\b", re.I)
COURSE_RE = re.compile(r"\b(MEng|BEng|BSc|BA|MSc|MA|MBA|MPhil|MSci|PhD|DPhil)\s+([^,\n]+)", re.I)
YEAR_RANGE_RE = re.compile(r"\b(20\d{2})\s*[-–]\s*(20\d{2}|present|now)\b", re.I)
YEAR_RE = re.compile(r"\b(20\d{2})\b")
SCHOOL_EMAIL_RE = re.compile(r"\.(ac\.uk|edu)(?:\W|$)|student", re.I)
NAME_PARTICLES = {"de", "van", "der", "da", "la", "le", "von", "di", "del", "st", "bin"}


def extract_text(document):
    name = ""
    if document.original:
        name = Path(document.original.name).name.lower()
    mime = (document.mime or "").lower()
    handle = document.original.open("rb")
    try:
        data = handle.read()
    finally:
        handle.close()
    if mime.startswith("text/") or name.endswith(".txt"):
        return data.decode("utf-8", errors="replace")
    if mime == "application/pdf" or name.endswith(".pdf"):
        return _pdf_text(data)
    if (
        "wordprocessingml" in mime
        or name.endswith(".docx")
        or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return _docx_text(data)
    return ""


def _pdf_text(data):
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages).strip()


def _docx_text(data):
    from io import BytesIO

    from docx import Document as Docx

    doc = Docx(BytesIO(data))
    return "\n".join(paragraph.text for paragraph in doc.paragraphs).strip()


def _looks_like_name(line):
    line = line.strip()
    if not line or "@" in line or any(ch.isdigit() for ch in line):
        return False
    if re.search(
        r"\b(curriculum|resume|cv|education|experience|skills|profile|university|college|institute|school|official|transcript|academic|programme|program)\b",
        line,
        re.I,
    ):
        return False
    words = [part for part in re.split(r"\s+", line) if part]
    if not 2 <= len(words) <= 5:
        return False

    def ok(word):
        core = word.replace("-", "").replace("'", "")
        if word.lower().strip(".") in NAME_PARTICLES:
            return True
        return bool(core) and core[0].isupper() and core.isalpha()

    return all(ok(word) for word in words)


def _split_name(full):
    words = [part for part in re.split(r"\s+", str(full or "").strip()) if part]
    if not words:
        return "", ""
    if len(words) == 1:
        return words[0], ""
    return words[0], " ".join(words[1:])


def _is_school_email(email):
    return bool(SCHOOL_EMAIL_RE.search(email or ""))


def candidates_from_text(text):
    skipped = []
    found = []
    seen = set()

    def add(key, label, value, category, quote):
        value = str(value or "").strip()
        quote = str(quote or "").strip()
        if not key or not value:
            return
        if is_forbidden(key, value):
            skipped.append({"key": key, "reason": "forbidden"})
            return
        fingerprint = (key, value.lower())
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        found.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "category": category if category in MemoryFact.CATEGORIES else STABLE_HINTS.get(key, "other"),
                "source_quote": quote[:400],
            }
        )

    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if is_forbidden("note", line) and not EMAIL_RE.search(line):
            skipped.append({"key": "secret", "reason": "forbidden", "quote": line[:80]})
            continue
        for email in EMAIL_RE.findall(line):
            if _is_school_email(email):
                add("school_email", "School email", email, "identity", line)
            add("email", "Email", email, "identity", line)
        phone_line = EMAIL_RE.sub("", line)
        if not is_forbidden("phone", phone_line):
            match = PHONE_RE.search(phone_line)
            if match and not re.search(r"(?:\d[ -]*?){13,19}", match.group(0)):
                add("phone", "Phone", re.sub(r"\s+", " ", match.group(0)).strip(), "identity", line)
        uni = UNI_RE.search(line)
        if uni:
            add("university", "University", uni.group(0).strip(" ,"), "study", line)
        course = COURSE_RE.search(line)
        if course:
            degree = course.group(1).strip()
            major = course.group(2).strip(" ,")
            add("degree", "Degree", degree, "study", line)
            if major:
                add("major", "Major", major, "study", line)
            add("course", "Course", (degree + " " + major).strip(), "study", line)
        elif DEGREE_RE.search(line) and not any(item["key"] == "degree" for item in found):
            add("degree", "Degree", DEGREE_RE.search(line).group(1), "study", line)
        postcode = POSTCODE_RE.search(line)
        if postcode:
            add("shipping.postcode", "Postcode", postcode.group(1).upper(), "shipping", line)
        span = YEAR_RANGE_RE.search(line)
        if span and (uni or course or DEGREE_RE.search(line)):
            add("study.start_year", "Start year", span.group(1), "study", line)
            end = span.group(2)
            if re.match(r"20\d{2}$", end):
                add("study.end_year", "End year", end, "study", line)
                add("study.year", "Year", end, "study", line)
        else:
            year = YEAR_RE.search(line)
            if year and (uni or course):
                add("study.year", "Year", year.group(1), "study", line)
        if _looks_like_name(line) and not any(item["key"] == "full_name" for item in found):
            add("full_name", "Full name", line, "identity", line)
            first, last = _split_name(line)
            if first:
                add("first_name", "First name", first, "identity", line)
            if last:
                add("last_name", "Last name", last, "identity", line)
    return found, skipped


def _already_settled(key, value):
    value = value.strip()
    for fact in MemoryFact.objects.filter(key=key):
        same = fact.value.strip() == value
        if key == "email":
            same = fact.value.strip().lower() == value.lower()
        if same and (fact.rejected or fact.confirmed):
            return True
    return False


def propose_facts(document, text):
    found, skipped = candidates_from_text(text)
    created = []
    for raw in found:
        if _already_settled(raw["key"], raw["value"]):
            continue
        if MemoryFact.objects.filter(
            source_document=document,
            key=raw["key"],
            value=raw["value"],
            rejected=False,
        ).exists():
            continue
        fact = MemoryFact.objects.create(
            key=raw["key"],
            label=raw["label"],
            value=raw["value"],
            category=raw["category"],
            source="file",
            source_quote=raw["source_quote"],
            source_document=document,
            confirmed=False,
            rejected=False,
            is_default=False,
        )
        created.append(fact)
    return created, skipped


def extract_and_propose(document):
    try:
        text = extract_text(document)
    except Exception as exc:
        document.status = "failed"
        document.extracted_text = ""
        document.skipped = [{"key": "extract", "reason": str(exc)[:200]}]
        document.save(update_fields=["status", "extracted_text", "skipped", "updated_at"])
        return document
    document.extracted_text = text
    if not text.strip():
        document.status = "stored"
        document.skipped = []
        document.save(update_fields=["status", "extracted_text", "skipped", "updated_at"])
        return document
    _created, skipped = propose_facts(document, text)
    document.status = "extracted"
    document.skipped = skipped
    document.save(update_fields=["status", "extracted_text", "skipped", "updated_at"])
    return document


def accept_proposals(document, fact_ids=None, overwrite_keys=None):
    overwrite_keys = set(overwrite_keys or [])
    qs = MemoryFact.objects.filter(source_document=document, confirmed=False, rejected=False)
    if fact_ids:
        qs = qs.filter(id__in=fact_ids)
    saved = []
    conflicts = []
    for fact in qs:
        existing = fact_for_key(fact.key)
        if existing and existing.id != fact.id:
            same = existing.value.strip() == fact.value.strip() or (
                fact.key == "email" and existing.value.strip().lower() == fact.value.strip().lower()
            )
            if same:
                fact.rejected = True
                fact.confirmed = False
                fact.save(update_fields=["rejected", "confirmed", "updated_at"])
                saved.append(existing.to_dict())
                continue
            if fact.key not in overwrite_keys:
                conflicts.append(
                    {
                        "key": fact.key,
                        "existing": existing.value,
                        "proposed": fact.value,
                    }
                )
                continue
            existing.value = fact.value
            existing.label = fact.label or existing.label
            existing.source = "file"
            existing.source_quote = fact.source_quote
            existing.source_document = document
            existing.confirmed = True
            existing.rejected = False
            existing.save()
            set_as_default(existing)
            fact.rejected = True
            fact.confirmed = False
            fact.save(update_fields=["rejected", "confirmed", "updated_at"])
            saved.append(existing.to_dict())
            continue
        fact.confirmed = True
        fact.rejected = False
        fact.save(update_fields=["confirmed", "rejected", "updated_at"])
        if not MemoryFact.objects.filter(key=fact.key, is_default=True, confirmed=True).exclude(pk=fact.pk).exists():
            set_as_default(fact)
        saved.append(fact.to_dict())
    return {"saved": saved, "conflicts": conflicts}


def reject_proposals(document, fact_ids=None):
    qs = MemoryFact.objects.filter(source_document=document, confirmed=False, rejected=False)
    if fact_ids:
        qs = qs.filter(id__in=fact_ids)
    updated = []
    for fact in qs:
        fact.rejected = True
        fact.confirmed = False
        fact.save(update_fields=["rejected", "confirmed", "updated_at"])
        updated.append(fact.to_dict())
    return {"rejected": updated}


def delete_document(document):
    removed = []
    for fact in list(MemoryFact.objects.filter(source_document=document)):
        payload = fact.to_dict()
        fact.delete()
        removed.append(payload)
    if document.original:
        document.original.delete(save=False)
    document.delete()
    return {"deleted": True, "removed_facts": removed}


def facts_from_vault(vault_ids, texts=None):
    from core.models import Document

    found = []
    seen = set()
    for raw in vault_ids or []:
        document = Document.objects.filter(id=raw).first()
        if not document:
            continue
        for fact in MemoryFact.objects.filter(source_document=document, rejected=False):
            fingerprint = (fact.key, fact.value.lower())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            payload = fact.to_dict()
            payload["from_file"] = True
            found.append(payload)
        extra, _skipped = candidates_from_text(document.extracted_text or "")
        for item in extra:
            fingerprint = (item["key"], item["value"].lower())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            found.append({**item, "from_file": True})
    for text in texts or []:
        extra, _skipped = candidates_from_text(text)
        for item in extra:
            fingerprint = (item["key"], item["value"].lower())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            found.append({**item, "from_file": True})
    return found[:30]


def attachments_from_vault(vault_ids):
    from core.models import Document

    items = []
    for raw in vault_ids or []:
        document = Document.objects.filter(id=raw).first()
        if not document:
            continue
        filename = Path(document.original.name).name if document.original else document.title
        items.append(
            {
                "name": filename or document.title,
                "media_type": document.mime,
                "size": document.size,
                "role": "this_run",
                "injectable": False,
                "document_id": str(document.id),
                "title": document.title,
            }
        )
    return items
