# Tests

| Command | What it does |
|---|---|
| `python3 tests/golden.py capture [BASE]` | Records the JSON of 40 data-API calls into `tests/golden/` (git-ignored: the rows carry device SNs/MACs). Run it against a known-good version **before** changing queries. |
| `python3 tests/golden.py check [BASE]` | Compares the running API with the recorded files. Any changed number fails. List endpoints whose original ORDER BY had ties are compared as multisets. |

`BASE` defaults to `http://127.0.0.1:8000` (the local `wifi-local` compose stack).

History: the 2026-10-20 backend refactor (routers/services, `mv_unit`) was verified with
40/40 identical responses against the pre-refactor capture (tag `baseline-20261020`).
