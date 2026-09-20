# {{NAME}} external memory

Working memory for {{NAME}}. Distilled facts and state only. Conversations never land here.

## Ritual

- Session start: `git pull --rebase --quiet`, read `INDEX.md`. When working an initiative, read its `initiatives/<name>.md` spoke. Do not preload spokes you are not using.
- Session end, if state changed: append dated entries to the spoke's State log (newest first), update the Active initiatives table in `INDEX.md` if status moved, run `python3 scripts/lint.py`, then commit and push.
- New initiative: copy `initiatives/_TEMPLATE.md`. Cross-cutting facts (people, systems, conventions) go in `INDEX.md`; anything belonging to one workstream goes in its spoke. Apply the deletion test to every line: would removing it cause a future session to make a mistake? If not, leave it out.

## Hard rules

- Never a secret value, token, password, or key. `ACCESS.md` holds pointers only. The linter blocks token shapes and runs as an enforced pre-commit hook (`core.hooksPath=hooks`); treat a failure as a stop.
- Never a transcript, recording, or chat log as content. Cite the document, ticket, or message, or write "per the conversation".
- Caps enforced by lint: `INDEX.md` 150 lines, spokes 100, `ACCESS.md` 200, this file 60, any other markdown 150. When a cap hits, prune resolved state and derivable facts before raising anything.
- Memory stays markdown: read whole, model-maintained, git-diffable. If search ever outgrows `rg`, derive a disposable index from these files; never replace them with a database.
- Machine-specific paths and quirks live in a gitignored `*.local.md`, never in tracked files.
- `lint-vocabulary.txt` names content that must never appear here, one regex per line. Keep it in step with wherever that content lives instead.
