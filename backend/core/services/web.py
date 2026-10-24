import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings

TAVILY_BASE = "https://api.tavily.com"
_REFUSED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def is_public_http(url):
    parsed = urlparse(str(url or "").strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().strip("[]")
    if not host or host in _REFUSED_HOSTS or host.endswith(".local"):
        return False
    return True


def post_json(path, payload):
    if not settings.TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY is missing. Put it in backend/.env")
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        TAVILY_BASE + path,
        data=body,
        headers={
            "Authorization": "Bearer " + settings.TAVILY_API_KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        raise


def _clip(text, limit=4000):
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _source(url, title, excerpt):
    return {
        "url": url,
        "title": title or url,
        "excerpt": _clip(excerpt),
    }


def search(query):
    data = post_json(
        "/search",
        {
            "query": query,
            "search_depth": "advanced",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False,
        },
    )
    rows = []
    for item in data.get("results") or []:
        url = item.get("url") or ""
        if not is_public_http(url):
            continue
        rows.append(_source(url, item.get("title") or "", item.get("content") or ""))
        if len(rows) >= 5:
            break
    return rows


def extract_urls(urls, query):
    chosen = []
    for url in urls or []:
        if is_public_http(url) and url not in chosen:
            chosen.append(url)
        if len(chosen) >= 3:
            break
    if not chosen:
        return []
    try:
        data = post_json(
            "/extract",
            {
                "urls": chosen,
                "extract_depth": "advanced",
                "format": "markdown",
                "chunks_per_source": 3,
                "query": query or "",
            },
        )
    except Exception:
        return [_source(url, url, "") for url in chosen]
    by_url = {}
    for item in data.get("results") or []:
        url = item.get("url") or ""
        if not is_public_http(url):
            continue
        by_url[url] = item.get("raw_content") or item.get("content") or ""
    return [_source(url, url, by_url.get(url) or "") for url in chosen]


_NOISE_HOSTS = ("youtube.com", "youtu.be", "facebook.com", "reddit.com", "tiktok.com", "instagram.com")


def _focus_query(prompt):
    text = re.sub(r",?\s*and which of my\b.*", "", prompt or "", flags=re.I)
    text = re.sub(r"\bwhich of my saved facts\b.*", "", text, flags=re.I).strip(" ,.")
    if re.search(r"\bimperial\b", prompt or "", re.I):
        return "Imperial College London official summer internship application requirements"
    return text or (prompt or "")


def _prefer(rows, host):
    kept = []
    for row in rows or []:
        name = (urlparse(row.get("url") or "").hostname or "").lower()
        if any(noise in name for noise in _NOISE_HOSTS):
            continue
        kept.append(row)
    if not host:
        return kept
    official = [row for row in kept if host in (urlparse(row.get("url") or "").hostname or "")]
    return official or kept


def search_then_extract(query, run=None, prefer_host=""):
    found = _prefer(search(query), prefer_host)
    urls = [item["url"] for item in found[:3]]
    extracted = extract_urls(urls, query) if urls else []
    by_url = {item["url"]: item for item in extracted}
    sources = []
    for item in found[:3]:
        extra = by_url.get(item["url"]) or {}
        excerpt = extra.get("excerpt") or item.get("excerpt") or ""
        sources.append(_source(item["url"], item.get("title") or "", excerpt))
    _remember(run, sources)
    return sources


def _remember(run, sources):
    if run is None:
        return
    checkpoint = dict(run.checkpoint or {})
    cache = dict(checkpoint.get("extracts") or {})
    for item in sources or []:
        url = item.get("url") or ""
        if url and url not in cache:
            cache[url] = item.get("excerpt") or ""
    checkpoint["extracts"] = cache
    run.checkpoint = checkpoint


def extract_for_run(run, url, query):
    if not is_public_http(url):
        return ""
    if "test" in sys.argv:
        return ""
    checkpoint = dict(run.checkpoint or {})
    cache = dict(checkpoint.get("extracts") or {})
    if url in cache:
        return cache[url]
    try:
        rows = extract_urls([url], query or "")
    except Exception:
        rows = []
    text = (rows[0].get("excerpt") if rows else "") or ""
    cache[url] = text
    checkpoint["extracts"] = cache
    run.checkpoint = checkpoint
    return text


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_THIS_PAGE_RE = re.compile(r"\b(this page|this tab|this site)\b", re.I)


def sources_for_prompt(prompt, current_url=""):
    pasted = ""
    for match in _URL_RE.finditer(prompt or ""):
        candidate = match.group(0).rstrip(".,);]")
        if is_public_http(candidate):
            pasted = candidate
            break
    if pasted:
        return {"searched": False, "sources": extract_urls([pasted], prompt)}
    if _THIS_PAGE_RE.search(prompt or "") and is_public_http(current_url):
        return {"searched": False, "sources": extract_urls([current_url], prompt)}
    prefer = "imperial.ac.uk" if re.search(r"\bimperial\b", prompt or "", re.I) else ""
    return {"searched": True, "sources": search_then_extract(_focus_query(prompt), prefer_host=prefer)}
