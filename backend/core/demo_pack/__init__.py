from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent

DEMO_FILES = {
    "maya-chen-cv.txt": "text/plain",
    "maya-chen-cv.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "maya-chen-transcript.txt": "text/plain",
    "maya-chen-transcript.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "caption-rule.txt": "text/plain",
    "clip-one.mp4": "video/mp4",
    "clip-two.mp4": "video/mp4",
}


def pack_path(name):
    if name not in DEMO_FILES:
        return None
    path = PACK_DIR / name
    if not path.is_file():
        return None
    return path
