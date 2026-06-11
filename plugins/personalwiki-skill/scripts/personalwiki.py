#!/usr/bin/env python3
"""Inspect and query a local Personal Wiki.

The script is intentionally dependency-free so Codex can run it with any
available Python 3 runtime.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_ROOT = os.environ.get("PERSONALWIKI_ROOT", "auto")
DEFAULT_URL = os.environ.get("PERSONALWIKI_URL", "http://localhost:6885")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
SENSITIVE_PATTERNS = (
    "api key",
    "apikey",
    "secret",
    "token",
    "password",
    "credential",
    "serial",
    "license",
    "nsfw",
    "\u0061\u0070\u0069\ud0a4",
    "\uac1c\uc778\uc815\ubcf4",
    "\uc2dc\ub9ac\uc5bc",
    "\uc81c\ud488\ud0a4",
    "\uc778\uc99d\uc815\ubcf4",
)


@dataclass
class Doc:
    id: int | None
    title: str
    slug: str
    path: Path | None
    created_at: str = ""
    updated_at: str = ""
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    templates: list[str] = field(default_factory=list)
    content: str = ""

    @property
    def sensitive(self) -> bool:
        haystack = " ".join([self.title, self.slug, *self.tags]).lower()
        return any(pattern.lower() in haystack for pattern in SENSITIVE_PATTERNS)


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")


def is_wiki_root(path: Path) -> bool:
    return (path / "doc").is_dir() and (
        (path / "wiki.db").is_file()
        or (path / "PersonalWiki.exe").is_file()
        or (path / "wiki_fts.db").is_file()
    )


def discover_root(start: Path | None = None) -> Path | None:
    raw_env = os.environ.get("PERSONALWIKI_ROOT")
    candidates: list[Path] = []
    if raw_env:
        candidates.append(Path(raw_env).expanduser())
    base = (start or Path.cwd()).resolve()
    candidates.extend([base, *base.parents])
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if is_wiki_root(resolved):
            return resolved
    return None


def root_path(args: argparse.Namespace) -> Path | None:
    raw = str(args.root or "auto")
    if raw.casefold() in {"auto", ""}:
        return discover_root()
    candidate = Path(raw).expanduser().resolve()
    return candidate if is_wiki_root(candidate) else None


def require_root(args: argparse.Namespace) -> Path | None:
    root = root_path(args)
    if root is None:
        print(
            "Could not discover the Personal Wiki root. Run from the wiki root, "
            "set PERSONALWIKI_ROOT, or pass --root <path>. Server-only commands "
            "such as editor, preview, and tag-suggestions with explicit content still work.",
            file=sys.stderr,
        )
    return root


def root_label(root: Path | None) -> str:
    return str(root) if root is not None else "(not discovered; server-only mode)"


def wiki_db(root: Path) -> Path:
    return root / "wiki.db"


def connect_db(root: Path) -> sqlite3.Connection | None:
    db = wiki_db(root)
    if not db.exists():
        return None
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def json_dump(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def unique_items(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = item.strip()
        key = normalize(value)
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def rel_path(path: Path | None, root: Path) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def load_docs(root: Path, include_content: bool = True) -> list[Doc]:
    con = connect_db(root)
    docs: list[Doc] = []
    if con is not None:
        rows = con.execute(
            """
            SELECT id, title, slug, file_path, created_at, updated_at
            FROM docs
            ORDER BY updated_at DESC, title COLLATE NOCASE
            """
        ).fetchall()
        tags_by_doc: dict[int, list[str]] = defaultdict(list)
        for row in con.execute(
            """
            SELECT dt.doc_id, t.name
            FROM doc_tags dt
            JOIN tags t ON t.id = dt.tag_id
            ORDER BY t.name COLLATE NOCASE
            """
        ):
            tags_by_doc[int(row["doc_id"])].append(row["name"])

        refs_by_doc: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for row in con.execute(
            """
            SELECT source_doc_id, ref_type, raw_target
            FROM doc_references
            ORDER BY raw_target COLLATE NOCASE
            """
        ):
            refs_by_doc[int(row["source_doc_id"])][row["ref_type"]].append(row["raw_target"])

        for row in rows:
            path = Path(row["file_path"])
            if not path.is_absolute():
                path = root / path
            doc = Doc(
                id=int(row["id"]),
                title=row["title"],
                slug=row["slug"],
                path=path,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                tags=tags_by_doc[int(row["id"])],
                links=refs_by_doc[int(row["id"])]["link"],
                templates=refs_by_doc[int(row["id"])]["template"],
            )
            if include_content and path.exists():
                doc.content = read_text(path)
            docs.append(doc)
        con.close()
        return docs

    doc_dir = root / "doc"
    for index, path in enumerate(sorted(doc_dir.glob("*.md")), start=1):
        content = read_text(path) if include_content else ""
        docs.append(
            Doc(
                id=index,
                title=path.stem.replace("-", " "),
                slug=path.stem,
                path=path,
                content=content,
                links=extract_wikilinks(content),
                templates=extract_templates(content),
            )
        )
    return docs


def extract_wikilinks(content: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r"(?<!!)\[\[([^\]]+)\]\]", content):
        target = match.group(1).split("|", 1)[0].strip()
        if target and not target.lower().startswith(("file/", "http://", "https://")):
            links.append(target)
    return sorted(set(links), key=normalize)


def extract_templates(content: str) -> list[str]:
    return sorted(set(m.strip() for m in re.findall(r"\{\{([^{}]+)\}\}", content)), key=normalize)


def find_doc(docs: list[Doc], target: str) -> Doc | None:
    needle = normalize(target)
    exact = [
        doc
        for doc in docs
        if normalize(doc.title) == needle
        or normalize(doc.slug) == needle
        or (doc.path and normalize(doc.path.name) == needle)
    ]
    if exact:
        return exact[0]
    partial = [
        doc
        for doc in docs
        if needle in normalize(doc.title)
        or needle in normalize(doc.slug)
        or (doc.path and needle in normalize(doc.path.name))
    ]
    return partial[0] if partial else None


def doc_to_brief(doc: Doc, root: Path) -> dict[str, object]:
    return {
        "id": doc.id,
        "title": doc.title,
        "slug": doc.slug,
        "path": rel_path(doc.path, root),
        "updated_at": doc.updated_at,
        "tags": doc.tags,
        "sensitive": doc.sensitive,
    }


def tokenize_query(query: str) -> list[str]:
    terms = re.findall(r'"([^"]+)"|(\S+)', query)
    return [a or b for a, b in terms]


def boolean_match(text: str, query: str) -> bool:
    tokens = tokenize_query(query)
    if not tokens:
        return True
    groups: list[list[tuple[str, bool]]] = [[]]
    negate_next = False
    for token in tokens:
        upper = token.upper()
        if upper == "OR":
            groups.append([])
            negate_next = False
            continue
        if upper == "AND":
            continue
        if upper == "NOT":
            negate_next = True
            continue
        groups[-1].append((token, negate_next))
        negate_next = False

    folded = normalize(text)
    for group in groups:
        if not group:
            continue
        matched = True
        for term, negated in group:
            present = normalize(term) in folded
            if (present and negated) or (not present and not negated):
                matched = False
                break
        if matched:
            return True
    return False


def score_doc(doc: Doc, query: str) -> tuple[int, list[str]]:
    terms = [t for t in tokenize_query(query) if t.upper() not in {"AND", "OR", "NOT"}]
    haystacks = {
        "title": doc.title,
        "slug": doc.slug,
        "tags": " ".join(doc.tags),
        "links": " ".join([*doc.links, *doc.templates]),
        "content": doc.content,
    }
    score = 0
    reasons: list[str] = []
    for term in terms:
        folded = normalize(term)
        if not folded:
            continue
        if folded in normalize(haystacks["title"]):
            score += 30
            reasons.append(f"title:{term}")
        if folded in normalize(haystacks["tags"]):
            score += 20
            reasons.append(f"tag:{term}")
        if folded in normalize(haystacks["links"]):
            score += 12
            reasons.append(f"link:{term}")
        content_hits = normalize(haystacks["content"]).count(folded)
        if content_hits:
            score += min(15, content_hits * 3)
            reasons.append(f"content:{term}x{content_hits}")
    return score, reasons


def make_snippet(content: str, query: str, max_len: int = 220) -> str:
    clean = re.sub(r"\s+", " ", content).strip()
    if not clean:
        return ""
    terms = [normalize(t) for t in tokenize_query(query) if t.upper() not in {"AND", "OR", "NOT"}]
    folded = normalize(clean)
    positions = [folded.find(term) for term in terms if term and folded.find(term) >= 0]
    start = max(min(positions) - 60, 0) if positions else 0
    snippet = clean[start : start + max_len]
    if start > 0:
        snippet = "..." + snippet
    if start + max_len < len(clean):
        snippet += "..."
    return snippet


def command_health(args: argparse.Namespace) -> int:
    root = root_path(args)
    docs = load_docs(root, include_content=False) if root is not None else []
    data = {
        "root": root_label(root),
        "server": args.url,
        "server_ok": check_server(args.url),
        "wiki_db": str(root / "wiki.db") if root is not None else "",
        "wiki_db_exists": (root / "wiki.db").exists() if root is not None else False,
        "wiki_fts_db_exists": (root / "wiki_fts.db").exists() if root is not None else False,
        "wiki_token_db_exists": (root / "wiki_token.db").exists() if root is not None else False,
        "doc_dir_exists": (root / "doc").exists() if root is not None else False,
        "doc_count": len(docs),
        "recent_docs": [doc_to_brief(doc, root) for doc in docs[: args.limit]] if root is not None else [],
    }
    json_dump(data) if args.json else print_health(data)
    return 0


def check_server(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, TimeoutError):
        return False


def endpoint_url(base_url: str, endpoint: str) -> str:
    return base_url.rstrip("/") + "/" + endpoint.lstrip("/")


def post_json(url: str, payload: dict[str, object], timeout: int = 12) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return {"data": parsed}
    except json.JSONDecodeError:
        return {"raw": raw}


def fetch_text(url: str, timeout: int = 12) -> str:
    request = urllib.request.Request(url, headers={"Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def slugify_title(title: str) -> str:
    value = title.strip().casefold()
    value = re.sub(r"[^\w\s.-]+", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s_]+", "-", value, flags=re.UNICODE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def parse_tags_value(raw: str | list[str]) -> list[str]:
    if isinstance(raw, list):
        return unique_items(str(item) for item in raw)
    return unique_items(part.strip() for part in raw.split(","))


def extract_attr(html: str, pattern: str) -> str:
    match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
    return html_lib.unescape(match.group(1)) if match else ""


def parse_editor_html(html: str, url: str) -> dict[str, object]:
    current_slug = extract_attr(html, r'<form[^>]+class="[^"]*\bedit-form\b[^"]*"[^>]+data-current-slug="([^"]*)"')
    title = extract_attr(html, r'<input[^>]+id="title"[^>]+value="([^"]*)"')
    tags_text = extract_attr(html, r'<input[^>]+id="tags"[^>]+value="([^"]*)"')
    content = extract_attr(html, r'<textarea[^>]+id="content"[^>]*>(.*?)</textarea>')
    initial_raw = extract_attr(html, r'<div[^>]+id="tag-suggestions"[^>]+data-initial=\'([^\']*)\'')
    try:
        initial_tags = json.loads(initial_raw) if initial_raw else []
    except json.JSONDecodeError:
        initial_tags = []
    if not isinstance(initial_tags, list):
        initial_tags = []
    syntax_snippets = [
        html_lib.unescape(value)
        for value in re.findall(r'class="syntax-copy"[^>]+data-copy="([^"]*)"', html, flags=re.IGNORECASE)
    ]
    return {
        "url": url,
        "mode": "new" if not current_slug else "edit",
        "current_slug": current_slug,
        "title": title,
        "tags": parse_tags_value(tags_text),
        "content": content,
        "content_length": len(content),
        "initial_tag_suggestions": [str(item) for item in initial_tags],
        "syntax_snippet_count": len(syntax_snippets),
        "syntax_snippets": syntax_snippets[:10],
        "features": [
            "GET /new renders a blank editor; note that /new/ may 404.",
            "GET /edit/<slug> renders the existing document editor.",
            "POST /new creates a document; POST /edit/<slug> saves edits.",
            "POST /api/tag-suggestions powers tag auto-suggestions.",
            "POST /preview renders Markdown preview HTML.",
            "Editor UI has syntax copy buttons, preview jump buttons, unsaved-change warning, and title linkability warning.",
            "Save may show tag and spellcheck warnings with override buttons.",
        ],
    }


def parse_doc_list_html(html: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for raw_item in re.findall(r'<li class="doc-item[^"]*">(.*?)</li>', html, flags=re.IGNORECASE | re.DOTALL):
        link_match = re.search(
            r'<a[^>]+class="doc-title"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            raw_item,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not link_match:
            continue
        href = html_lib.unescape(link_match.group(1))
        title = re.sub(r"<[^>]+>", "", link_match.group(2))
        title = html_lib.unescape(re.sub(r"\s+", " ", title).strip())
        slug = ""
        if "/doc/" in href:
            slug = urllib.parse.unquote(href.split("/doc/", 1)[1])
        time_match = re.search(
            r'<span[^>]+class="doc-time"[^>]*>(.*?)</span>',
            raw_item,
            flags=re.IGNORECASE | re.DOTALL,
        )
        tags = [
            html_lib.unescape(re.sub(r"<[^>]+>", "", tag).lstrip("#").strip())
            for tag in re.findall(
                r'<a[^>]+class="tag"[^>]*>(.*?)</a>',
                raw_item,
                flags=re.IGNORECASE | re.DOTALL,
            )
        ]
        items.append(
            {
                "title": title,
                "slug": slug,
                "href": href,
                "updated_at": html_lib.unescape(time_match.group(1).strip()) if time_match else "",
                "tags": tags,
                "source": "server",
            }
        )
    return items


def editor_url(base_url: str, slug: str | None = None) -> str:
    if slug:
        return endpoint_url(base_url, "/edit/" + urllib.parse.quote(slug, safe=""))
    return endpoint_url(base_url, "/new")


def fetch_editor_document(base_url: str, slug: str) -> Doc | None:
    try:
        data = parse_editor_html(fetch_text(editor_url(base_url, slug)), editor_url(base_url, slug))
    except urllib.error.HTTPError:
        return None
    title = str(data.get("title") or slug)
    return Doc(
        id=None,
        title=title,
        slug=str(data.get("current_slug") or slug),
        path=None,
        tags=[str(tag) for tag in data.get("tags", [])],
        content=str(data.get("content") or ""),
        links=extract_wikilinks(str(data.get("content") or "")),
        templates=extract_templates(str(data.get("content") or "")),
    )


def print_health(data: dict[str, object]) -> None:
    print(f"Root: {data['root']}")
    print(f"Server: {data['server']} ({'ok' if data['server_ok'] else 'not reachable'})")
    print(f"Documents: {data['doc_count']}")
    print(f"wiki.db: {'ok' if data['wiki_db_exists'] else 'missing'}")
    print(f"wiki_fts.db: {'ok' if data['wiki_fts_db_exists'] else 'missing'}")
    print(f"wiki_token.db: {'ok' if data['wiki_token_db_exists'] else 'missing'}")
    print("Recent:")
    for doc in data["recent_docs"]:
        print(f"- {doc['title']} ({doc['updated_at']}) [{doc['slug']}]")


def command_overview(args: argparse.Namespace) -> int:
    root = require_root(args)
    if root is None:
        return 2
    docs = load_docs(root, include_content=False)
    tag_counter = Counter(tag for doc in docs for tag in doc.tags)
    link_counter = Counter(link for doc in docs for link in doc.links)
    data = {
        "root": str(root),
        "doc_count": len(docs),
        "tag_count": len(tag_counter),
        "link_target_count": len(link_counter),
        "top_tags": tag_counter.most_common(args.limit),
        "top_link_targets": link_counter.most_common(args.limit),
        "recent_docs": [doc_to_brief(doc, root) for doc in docs[: args.limit]],
        "orphan_docs": [doc.title for doc in orphan_docs(docs)[: args.limit]],
    }
    json_dump(data) if args.json else print_overview(data)
    return 0


def print_overview(data: dict[str, object]) -> None:
    print(f"Root: {data['root']}")
    print(f"Documents: {data['doc_count']}")
    print(f"Tags: {data['tag_count']}")
    print("Top tags:")
    for tag, count in data["top_tags"]:
        print(f"- #{tag}: {count}")
    print("Recent docs:")
    for doc in data["recent_docs"]:
        print(f"- {doc['title']} ({doc['updated_at']})")
    print("Orphan candidates:")
    for title in data["orphan_docs"]:
        print(f"- {title}")


def orphan_docs(docs: list[Doc]) -> list[Doc]:
    incoming = incoming_map(docs)
    outgoing_titles = {normalize(link) for doc in docs for link in doc.links}
    return [
        doc
        for doc in docs
        if not doc.links
        and not incoming.get(doc.title)
        and normalize(doc.title) not in outgoing_titles
    ]


def command_search(args: argparse.Namespace) -> int:
    root = root_path(args)
    if root is None:
        query = urllib.parse.urlencode({"q": args.query})
        url = endpoint_url(args.url, "/search") + "?" + query
        data = parse_doc_list_html(fetch_text(url))[: args.limit]
        json_dump(data) if args.json else print_server_results(data, url)
        return 0
    docs = load_docs(root, include_content=True)
    results = []
    for doc in docs:
        haystack = " ".join([doc.title, doc.slug, " ".join(doc.tags), doc.content, " ".join(doc.links)])
        if not boolean_match(haystack, args.query):
            continue
        score, reasons = score_doc(doc, args.query)
        if score == 0 and args.query.strip():
            score = 1
        snippet = "" if doc.sensitive and not args.include_sensitive else make_snippet(doc.content, args.query)
        if doc.sensitive and not args.include_sensitive:
            snippet = "[sensitive snippet hidden; pass --include-sensitive only with explicit user intent]"
        results.append((score, doc.updated_at, doc, reasons, snippet))

    results.sort(key=lambda item: (item[0], item[1]), reverse=True)
    payload = [
        {
            **doc_to_brief(doc, root),
            "score": score,
            "reasons": reasons,
            "snippet": snippet,
        }
        for score, _, doc, reasons, snippet in results[: args.limit]
    ]
    json_dump(payload) if args.json else print_results(payload)
    return 0


def print_server_results(results: list[dict[str, object]], url: str) -> None:
    print(f"Server search: {url}")
    for item in results:
        tags = " ".join(f"#{tag}" for tag in item["tags"])
        print(f"- {item['title']} [{item['slug']}] updated={item['updated_at']}")
        if tags:
            print(f"  tags: {tags}")


def print_results(results: list[dict[str, object]]) -> None:
    for item in results:
        tags = " ".join(f"#{tag}" for tag in item["tags"])
        print(f"- {item['title']} [{item['slug']}] score={item['score']} updated={item['updated_at']}")
        if tags:
            print(f"  tags: {tags}")
        if item.get("reasons"):
            print(f"  reasons: {', '.join(item['reasons'])}")
        if item.get("snippet"):
            print(f"  snippet: {item['snippet']}")


def command_read(args: argparse.Namespace) -> int:
    root = root_path(args)
    if root is None:
        doc = fetch_editor_document(args.url, slugify_title(args.target))
        if doc is None:
            print(f"No document matched: {args.target}", file=sys.stderr)
            return 2
        data = {
            **doc_to_brief(doc, Path.cwd()),
            "created_at": doc.created_at,
            "links": doc.links,
            "templates": doc.templates,
            "backlinks": [],
            "content": doc.content,
            "source": "server /edit/<slug>",
        }
        json_dump(data) if args.json else print_read(data)
        return 0
    docs = load_docs(root, include_content=True)
    doc = find_doc(docs, args.target)
    if doc is None:
        print(f"No document matched: {args.target}", file=sys.stderr)
        return 2
    incoming = incoming_map(docs).get(doc.title, [])
    data = {
        **doc_to_brief(doc, root),
        "created_at": doc.created_at,
        "links": doc.links,
        "templates": doc.templates,
        "backlinks": [doc_to_brief(item, root) for item in incoming],
        "content": doc.content,
    }
    if doc.sensitive and not args.include_sensitive:
        data["content"] = "[sensitive content hidden; pass --include-sensitive only with explicit user intent]"
    json_dump(data) if args.json else print_read(data)
    return 0


def command_tag_suggestions(args: argparse.Namespace) -> int:
    root = root_path(args)
    doc: Doc | None = None
    if args.target:
        if root is not None:
            docs = load_docs(root, include_content=True)
            doc = find_doc(docs, args.target)
        if doc is None:
            slug = args.slug or slugify_title(args.target)
            doc = fetch_editor_document(args.url, slug)
        if doc is None:
            print(f"No document matched: {args.target}", file=sys.stderr)
            return 2
        if doc.sensitive and not args.include_sensitive:
            print(
                "Refusing to send sensitive document content to the tag suggestion API. "
                "Pass --include-sensitive only with explicit user intent.",
                file=sys.stderr,
            )
            return 3

    if args.content_file:
        content = read_text(Path(args.content_file))
    elif args.content:
        content = args.content
    elif doc is not None:
        content = doc.content
    else:
        print("Provide a target document, --content, or --content-file.", file=sys.stderr)
        return 2

    extra_tags: list[str] = []
    if args.tags:
        extra_tags.extend(part.strip() for part in args.tags.split(","))
    extra_tags.extend(args.tag or [])
    tags = unique_items([*(doc.tags if doc is not None else []), *extra_tags])
    payload = {
        "title": args.title or (doc.title if doc is not None else ""),
        "content": content,
        "slug": args.slug or (doc.slug if doc is not None else ""),
        "tags": tags,
    }
    result = post_json(endpoint_url(args.url, args.endpoint), payload)
    output = {
        "request": {
            "title": payload["title"],
            "slug": payload["slug"],
            "tags": payload["tags"],
            "content_length": len(content),
        },
        "response": result,
    }
    json_dump(output) if args.json else print_tag_suggestions(output)
    return 0


def command_preview(args: argparse.Namespace) -> int:
    root = root_path(args)
    doc: Doc | None = None
    if args.target:
        if root is not None:
            docs = load_docs(root, include_content=True)
            doc = find_doc(docs, args.target)
        if doc is None:
            slug = args.slug or slugify_title(args.target)
            doc = fetch_editor_document(args.url, slug)
        if doc is None:
            print(f"No document matched: {args.target}", file=sys.stderr)
            return 2
        if doc.sensitive and not args.include_sensitive:
            print(
                "Refusing to send sensitive document content to the preview API. "
                "Pass --include-sensitive only with explicit user intent.",
                file=sys.stderr,
            )
            return 3

    if args.content_file:
        content = read_text(Path(args.content_file))
    elif args.content:
        content = args.content
    elif doc is not None:
        content = doc.content
    else:
        print("Provide a target document, --content, or --content-file.", file=sys.stderr)
        return 2

    result = post_json(endpoint_url(args.url, args.endpoint), {"content": content})
    html = str(result.get("html", ""))
    output = {
        "request": {"content_length": len(content)},
        "response": {
            "html_length": len(html),
            "html": html if args.full else html[: args.max_chars],
        },
    }
    json_dump(output) if args.json else print_preview(output)
    return 0


def print_preview(data: dict[str, object]) -> None:
    response = data["response"]
    print(f"Content length: {data['request']['content_length']}")
    print(f"HTML length: {response['html_length']}")
    print(response["html"])


def command_editor(args: argparse.Namespace) -> int:
    root = root_path(args)
    slug = args.slug
    if not slug and args.target:
        if root is not None:
            doc = find_doc(load_docs(root, include_content=False), args.target)
            slug = doc.slug if doc else ""
        if not slug:
            slug = slugify_title(args.target)
    url = editor_url(args.url, slug if slug else None)
    try:
        data = parse_editor_html(fetch_text(url), url)
    except urllib.error.HTTPError as error:
        print(f"Editor page returned HTTP {error.code}: {url}", file=sys.stderr)
        return 2
    if not slug:
        data["mode"] = "new"
    json_dump(data) if args.json else print_editor(data)
    return 0


def print_editor(data: dict[str, object]) -> None:
    print(f"URL: {data['url']}")
    print(f"Mode: {data['mode']}")
    if data.get("current_slug"):
        print(f"Current slug: {data['current_slug']}")
    if data.get("title"):
        print(f"Title: {data['title']}")
    tags = data.get("tags") or []
    print(f"Tags: {', '.join(tags) if tags else '(none)'}")
    print(f"Content length: {data['content_length']}")
    suggestions = data.get("initial_tag_suggestions") or []
    print(f"Initial tag suggestions: {', '.join(suggestions) if suggestions else '(none)'}")
    print(f"Syntax copy snippets: {data['syntax_snippet_count']}")
    print("Features:")
    for feature in data.get("features", []):
        print(f"- {feature}")


def print_tag_suggestions(data: dict[str, object]) -> None:
    request = data["request"]
    response = data["response"]
    print(f"Title: {request['title']}")
    print(f"Slug: {request['slug']}")
    print(f"Existing tags: {', '.join(request['tags']) if request['tags'] else '(none)'}")
    tags = response.get("tags") if isinstance(response, dict) else None
    if isinstance(tags, list):
        print("Suggested tags:")
        if tags:
            for tag in tags:
                print(f"- {tag}")
        else:
            print("- (none)")
    else:
        print("Response:")
        print(json.dumps(response, ensure_ascii=False, indent=2))


def print_read(data: dict[str, object]) -> None:
    print(f"Title: {data['title']}")
    print(f"Slug: {data['slug']}")
    print(f"Path: {data['path']}")
    print(f"Updated: {data['updated_at']}")
    if data["tags"]:
        print("Tags: " + " ".join(f"#{tag}" for tag in data["tags"]))
    if data["links"]:
        print("Links: " + ", ".join(data["links"]))
    if data["templates"]:
        print("Templates: " + ", ".join(data["templates"]))
    if data["backlinks"]:
        print("Backlinks: " + ", ".join(item["title"] for item in data["backlinks"]))
    print("\n--- content ---")
    print(data["content"])


def incoming_map(docs: list[Doc]) -> dict[str, list[Doc]]:
    by_key = {normalize(doc.title): doc for doc in docs}
    by_key.update({normalize(doc.slug): doc for doc in docs})
    incoming: dict[str, list[Doc]] = defaultdict(list)
    for source in docs:
        for target in [*source.links, *source.templates]:
            target_doc = by_key.get(normalize(target))
            if target_doc:
                incoming[target_doc.title].append(source)
    return incoming


def command_links(args: argparse.Namespace) -> int:
    root = require_root(args)
    if root is None:
        return 2
    docs = load_docs(root, include_content=True)
    doc = find_doc(docs, args.target)
    if doc is None:
        print(f"No document matched: {args.target}", file=sys.stderr)
        return 2
    incoming = incoming_map(docs).get(doc.title, [])
    data = {
        "document": doc_to_brief(doc, root),
        "outgoing_links": doc.links,
        "templates": doc.templates,
        "backlinks": [doc_to_brief(item, root) for item in incoming],
    }
    json_dump(data) if args.json else print_links(data)
    return 0


def print_links(data: dict[str, object]) -> None:
    doc = data["document"]
    print(f"Document: {doc['title']} [{doc['slug']}]")
    print("Outgoing links:")
    for link in data["outgoing_links"]:
        print(f"- {link}")
    print("Templates:")
    for template in data["templates"]:
        print(f"- {template}")
    print("Backlinks:")
    for item in data["backlinks"]:
        print(f"- {item['title']} [{item['slug']}]")


def command_related(args: argparse.Namespace) -> int:
    root = require_root(args)
    if root is None:
        return 2
    docs = load_docs(root, include_content=True)
    seed = find_doc(docs, args.target)
    if seed is None:
        print(f"No document matched: {args.target}", file=sys.stderr)
        return 2

    incoming = incoming_map(docs)
    seed_incoming = {doc.title for doc in incoming.get(seed.title, [])}
    seed_links = {normalize(link) for link in seed.links}
    seed_templates = {normalize(template) for template in seed.templates}
    seed_tags = set(seed.tags)
    ranked = []
    for doc in docs:
        if doc.title == seed.title:
            continue
        score = 0
        reasons: list[str] = []
        shared_tags = sorted(seed_tags.intersection(doc.tags), key=normalize)
        if shared_tags:
            score += len(shared_tags) * 10
            reasons.append("shared tags: " + ", ".join(shared_tags))
        if normalize(doc.title) in seed_links:
            score += 30
            reasons.append("linked from seed")
        if normalize(doc.title) in seed_templates:
            score += 20
            reasons.append("used as template by seed")
        if doc.title in seed_incoming:
            score += 30
            reasons.append("links to seed")
        common_links = sorted(
            {normalize(link) for link in seed.links}.intersection(normalize(link) for link in doc.links)
        )
        if common_links:
            score += min(20, len(common_links) * 5)
            reasons.append(f"shared outgoing links: {len(common_links)}")
        if args.query:
            query_score, query_reasons = score_doc(doc, args.query)
            score += query_score
            reasons.extend(query_reasons)
        if score:
            ranked.append((score, doc.updated_at, doc, reasons))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    payload = [
        {**doc_to_brief(doc, root), "score": score, "reasons": reasons}
        for score, _, doc, reasons in ranked[: args.limit]
    ]
    json_dump(payload) if args.json else print_related(seed, payload)
    return 0


def print_related(seed: Doc, related: list[dict[str, object]]) -> None:
    print(f"Seed: {seed.title} [{seed.slug}]")
    for item in related:
        print(f"- {item['title']} [{item['slug']}] score={item['score']}")
        print(f"  reasons: {', '.join(item['reasons'])}")


def command_graph(args: argparse.Namespace) -> int:
    root = require_root(args)
    if root is None:
        return 2
    docs = load_docs(root, include_content=False)
    focus = find_doc(docs, args.focus) if args.focus else None
    selected = select_graph_docs(docs, focus, args.depth, args.limit)
    edges = graph_edges(selected)
    if args.format == "json":
        json_dump(
            {
                "nodes": [doc_to_brief(doc, root) for doc in selected],
                "edges": [{"source": a.title, "target": b.title, "type": kind} for a, b, kind in edges],
            }
        )
    else:
        print_mermaid(selected, edges)
    return 0


def select_graph_docs(docs: list[Doc], focus: Doc | None, depth: int, limit: int) -> list[Doc]:
    if focus is None:
        return docs[:limit]
    by_key = {normalize(doc.title): doc for doc in docs}
    by_key.update({normalize(doc.slug): doc for doc in docs})
    selected = {focus.title: focus}
    frontier = [focus]
    incoming = incoming_map(docs)
    for _ in range(depth):
        next_frontier: list[Doc] = []
        for doc in frontier:
            neighbors: list[Doc] = []
            for link in [*doc.links, *doc.templates]:
                target = by_key.get(normalize(link))
                if target:
                    neighbors.append(target)
            neighbors.extend(incoming.get(doc.title, []))
            for neighbor in neighbors:
                if neighbor.title not in selected:
                    selected[neighbor.title] = neighbor
                    next_frontier.append(neighbor)
                    if len(selected) >= limit:
                        return list(selected.values())
        frontier = next_frontier
    return list(selected.values())[:limit]


def graph_edges(docs: list[Doc]) -> list[tuple[Doc, Doc, str]]:
    by_key = {normalize(doc.title): doc for doc in docs}
    by_key.update({normalize(doc.slug): doc for doc in docs})
    selected_titles = {doc.title for doc in docs}
    edges: list[tuple[Doc, Doc, str]] = []
    for doc in docs:
        for link in doc.links:
            target = by_key.get(normalize(link))
            if target and target.title in selected_titles:
                edges.append((doc, target, "link"))
        for template in doc.templates:
            target = by_key.get(normalize(template))
            if target and target.title in selected_titles:
                edges.append((doc, target, "template"))
    return edges


def mermaid_id(title: str) -> str:
    value = re.sub(r"\W+", "_", title, flags=re.UNICODE).strip("_")
    return "n_" + (value or "node")


def print_mermaid(docs: list[Doc], edges: list[tuple[Doc, Doc, str]]) -> None:
    print("graph TD")
    for doc in docs:
        label = doc.title.replace('"', '\\"')
        print(f'  {mermaid_id(doc.title)}["{label}"]')
    for source, target, kind in edges:
        arrow = "-->|template|" if kind == "template" else "-->"
        print(f"  {mermaid_id(source.title)} {arrow} {mermaid_id(target.title)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and query a local Personal Wiki.")
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="Wiki root directory, or 'auto' to discover from PERSONALWIKI_ROOT/current directory.",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Personal Wiki server URL.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="Check server and local data availability.")
    health.add_argument("--limit", type=int, default=5)
    health.set_defaults(func=command_health)

    overview = sub.add_parser("overview", help="Summarize docs, tags, links, and orphan candidates.")
    overview.add_argument("--limit", type=int, default=10)
    overview.set_defaults(func=command_overview)

    search = sub.add_parser("search", help="Search documents with simple AND/OR/NOT support.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-sensitive", action="store_true")
    search.set_defaults(func=command_search)

    read = sub.add_parser("read", help="Read one document by title, slug, or filename.")
    read.add_argument("target")
    read.add_argument("--include-sensitive", action="store_true")
    read.set_defaults(func=command_read)

    tag_suggestions = sub.add_parser("tag-suggestions", help="Call /api/tag-suggestions.")
    tag_suggestions.add_argument("target", nargs="?", help="Existing document title, slug, or filename.")
    tag_suggestions.add_argument("--title", default="", help="Title for a new draft or override.")
    tag_suggestions.add_argument("--slug", default="", help="Slug for a new draft or override.")
    tag_suggestions.add_argument("--content", default="", help="Inline content for a new draft.")
    tag_suggestions.add_argument("--content-file", default="", help="Markdown file to send as content.")
    tag_suggestions.add_argument("--tag", action="append", default=[], help="Existing tag; repeatable.")
    tag_suggestions.add_argument("--tags", default="", help="Comma-separated existing tags.")
    tag_suggestions.add_argument("--endpoint", default="/api/tag-suggestions")
    tag_suggestions.add_argument("--include-sensitive", action="store_true")
    tag_suggestions.set_defaults(func=command_tag_suggestions)

    preview = sub.add_parser("preview", help="Call /preview and return rendered HTML.")
    preview.add_argument("target", nargs="?", help="Existing document title, slug, or filename.")
    preview.add_argument("--slug", default="", help="Slug to use when loading target via /edit/<slug>.")
    preview.add_argument("--content", default="", help="Inline Markdown content.")
    preview.add_argument("--content-file", default="", help="Markdown file to send as content.")
    preview.add_argument("--endpoint", default="/preview")
    preview.add_argument("--max-chars", type=int, default=1200)
    preview.add_argument("--full", action="store_true", help="Print full HTML instead of truncating.")
    preview.add_argument("--include-sensitive", action="store_true")
    preview.set_defaults(func=command_preview)

    editor = sub.add_parser("editor", help="Inspect /new or /edit/<slug> editor capabilities.")
    editor.add_argument("target", nargs="?", help="Existing document title or slug; omitted means /new.")
    editor.add_argument("--slug", default="", help="Explicit edit slug.")
    editor.set_defaults(func=command_editor)

    links = sub.add_parser("links", help="Show outgoing links, templates, and backlinks.")
    links.add_argument("target")
    links.set_defaults(func=command_links)

    related = sub.add_parser("related", help="Rank related documents for a seed document.")
    related.add_argument("target")
    related.add_argument("--query", default="")
    related.add_argument("--limit", type=int, default=10)
    related.set_defaults(func=command_related)

    graph = sub.add_parser("graph", help="Print a local document graph.")
    graph.add_argument("--focus", default="")
    graph.add_argument("--depth", type=int, default=1)
    graph.add_argument("--limit", type=int, default=50)
    graph.add_argument("--format", choices=("mermaid", "json"), default="mermaid")
    graph.set_defaults(func=command_graph)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
