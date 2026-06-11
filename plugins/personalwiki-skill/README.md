# PersonalWiki Skill

Local Codex plugin for the Personal Wiki served at `http://localhost:6885`.
The wiki root is intentionally treated as variable: set `PERSONALWIKI_ROOT`,
pass `--root <path>`, or run the helper from the wiki root when local DB/file
analysis is needed.

It gives Codex a reusable workflow for:

- question analysis against the wiki
- document search and reading
- backlink and outgoing-link inspection
- related-note discovery
- server tag suggestions through `/api/tag-suggestions`
- editor inspection through `/new` and `/edit/<slug>`
- Markdown preview rendering through `/preview`
- Mermaid link graph generation
- cautious handling of secret-bearing notes

The helper script is dependency-free:

```powershell
python .\scripts\personalwiki.py health
python .\scripts\personalwiki.py editor
python .\scripts\personalwiki.py editor "위키 문법 설명서"
python .\scripts\personalwiki.py preview --content-file .\draft.md
python .\scripts\personalwiki.py search "windows AND performance"
python .\scripts\personalwiki.py links "위키 문법 설명서"
python .\scripts\personalwiki.py tag-suggestions "위키 문법 설명서"
python .\scripts\personalwiki.py tag-suggestions --title "Draft" --content-file .\draft.md --tag existing
python .\scripts\personalwiki.py graph --focus "위키 문법 설명서" --format mermaid
```
