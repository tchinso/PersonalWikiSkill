---
name: personalwiki
description: Use the user's local Personal Wiki PKM for question analysis, document lookup, backlink/link graph analysis, related-note discovery, editor/API inspection, and wiki maintenance. Trigger when the user asks about their personal wiki, PKM, notes, document relationships, tags, backlinks, local wiki server, localhost:6885, or the wiki root/doc/wiki.db files.
---

# PersonalWiki Skill

Use this skill when the user wants Codex to reason over their local Personal Wiki.

## Local Wiki Shape

- The wiki root directory is variable. Do not assume `D:\Codex`; that may only be a temporary copy.
- Stable server: `http://localhost:6885`
- Override server with `PERSONALWIKI_URL`.
- Prefer `PERSONALWIKI_ROOT`, an explicit `--root <path>`, or the current working directory/parents when local files or DB access are needed.
- If the root is unknown, use server-only flows first: `/`, `/search`, `/doc/<slug>`, `/new`, `/edit/<slug>`, `/api/tag-suggestions`, and `/preview`.
- Primary content: `doc/*.md`
- Sidecar metadata: `doc/json/*.json`
- Main metadata DB: `wiki.db`
- Full-text index DB: `wiki_fts.db`
- Token index DB: `wiki_token.db`
- Attachments and images: `file/` and `img/`

The live server exposes useful human pages:

- `/` document list
- `/new` blank editor for creating a document. `/new/` may return 404 in the current app.
- `/edit/<slug>` editor for an existing document, where `<slug>` is the slugified document title or DB slug.
- `/search?q=...` search page, including `AND`, `OR`, and `NOT`
- `/doc/<slug>` document view
- `/tag/<tag>` tag page

It also exposes editor APIs:

- `POST /api/tag-suggestions`
  - Request body: `{"title": "...", "content": "...", "slug": "...", "tags": ["existing", "tags"]}`
  - Response body includes `{"tags": [...]}`.
  - Use this when drafting, retagging, cleaning up tags, or comparing Codex tag ideas with the app's own suggestions.
- `POST /preview`
  - Request body: `{"content": "..."}`
  - Use this only when rendered HTML preview matters.
- The editor pages also expose client-side functions:
  - tag suggestion refresh via `/api/tag-suggestions`
  - Markdown preview refresh via `/preview`
  - syntax-copy buttons for common wiki snippets
  - preview/editor jump buttons
  - unsaved-change warning
  - title linkability warning for titles starting with `file/`, `http://`, or `https://`
  - save-time tag and spellcheck warning flows with override buttons

Prefer server/API flows when the root is unknown. Prefer local files and SQLite for deeper relationship analysis once the root has been discovered or provided. Use the live server to confirm rendering, routes, and UI behavior.

## Standard Workflow

1. Classify the user's request:
   - direct lookup
   - question analysis / answer synthesis
   - document relationship or backlink analysis
   - related document discovery
   - tag cleanup or PKM maintenance
   - draft/edit/write request
2. Check that the wiki is available:
   - Use `scripts/personalwiki.py health`.
   - If `python` is unavailable, use the Codex bundled Python runtime or another available Python 3.
   - Treat root discovery as optional; `localhost:6885` is the stable anchor.
3. Search broadly first:
   - Use `scripts/personalwiki.py search "<query>" --limit 10`.
   - If root discovery fails, the helper falls back to the server `/search?q=...` page.
   - For complex Korean or mixed-language queries, search important nouns separately as well.
   - Use the server `/search?q=...` when you need to compare with the app's search behavior.
4. Read only the most relevant documents:
   - Use `scripts/personalwiki.py read "<title-or-slug>"`.
   - Use `--include-sensitive` only when the user explicitly asks to inspect a sensitive document.
5. Map relationships when useful:
   - `scripts/personalwiki.py links "<title-or-slug>"`
   - `scripts/personalwiki.py related "<title-or-slug>" --limit 10`
   - `scripts/personalwiki.py graph --focus "<title-or-slug>" --format mermaid`
6. For tag work, compare DB/tag-neighbor evidence with the app API:
   - Existing note: `scripts/personalwiki.py tag-suggestions "<title-or-slug>"`
   - New draft: `scripts/personalwiki.py tag-suggestions --title "Draft title" --content-file draft.md --tag existing`
7. For editor and rendering behavior:
   - Blank editor: `scripts/personalwiki.py editor`
   - Existing editor: `scripts/personalwiki.py editor "<title-or-slug>"`
   - Markdown render check: `scripts/personalwiki.py preview --content-file draft.md`
8. Answer with citations by title and local path when possible. If only server data was available, cite page title and URL/route.
9. When proposing new links, use the wiki syntax `[[Target Title]]` or `[[Target Title|label]]`.

## Sensitive Notes

This wiki can include private or secret-bearing notes such as API keys, serial keys, credentials, NSFW prompts, or personal records. Do not quote secrets, keys, passwords, tokens, or license codes back to the user unless the user explicitly asks for that exact material. Prefer metadata, titles, tags, and redacted summaries.

## Link Model

The wiki uses:

- Document links: `[[Target Title]]`
- Aliased links: `[[Target Title|label]]`
- File links: `[[file/name.ext]]`
- Image embeds: `![[image.webp]]`
- Templates/transclusions: `{{Template Title}}`

The `wiki.db` tables to know:

- `docs`: document title, slug, file path, timestamps, metadata
- `tags`: tag names
- `doc_tags`: document to tag join table
- `doc_references`: outgoing document links and templates

Use DB relationship data first, then verify with source markdown if a result looks surprising.

## Maintenance Guidance

- Do not edit wiki documents unless the user explicitly asks.
- Do not POST to `/new` or `/edit/<slug>` unless the user explicitly asks to create or save a wiki document.
- Before editing a document, read the current source markdown and preserve its existing style.
- Keep new tags short, reusable, and consistent with nearby tags.
- When suggesting cleanup, group suggestions into:
  - missing links
  - duplicate/near-duplicate notes
  - tag normalization
  - orphan notes
  - stale notes by update date
- For answer synthesis, separate "what the wiki says" from your own inference.
