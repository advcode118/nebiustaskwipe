import json
import zipfile
from io import BytesIO
from pathlib import Path

from core.models import Document, MemoryFact


def profile_payload():
    facts = MemoryFact.objects.filter(confirmed=True, rejected=False).exclude(sensitivity="never")
    return {
        "facts": [fact.to_dict() for fact in facts],
    }


def build_export_zip():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "profile.json",
            json.dumps(profile_payload(), indent=2, ensure_ascii=False),
        )
        for document in Document.objects.all():
            if not document.original:
                continue
            name = Path(document.original.name).name or "file"
            arcname = "vault/" + str(document.id) + "/" + name
            try:
                handle = document.original.open("rb")
            except Exception:
                continue
            try:
                archive.writestr(arcname, handle.read())
            finally:
                handle.close()
    buf.seek(0)
    return buf
