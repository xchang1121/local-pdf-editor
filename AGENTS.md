# Project working agreement

## User instructions

- This project is maintained in `https://github.com/xchang1121/local-pdf-editor.git`.
- The user explicitly requests that every completed change to this project be committed and pushed to that repository. Finish implementation and relevant checks, then push the complete update. No additional push approval is required. Report the commit and whether the remote was verified. If authentication or a conflicting remote change prevents pushing, report the actual blocker; do not claim synchronization succeeded.
- Preserve existing edits and do not force-push or rewrite remote history unless explicitly instructed.
- Continue to improve original-text editing reliability, the visible undo/redo history and image replacement. The user prefers a familiar online PDF editor layout and plain Chinese interface text.

## Implementation and validation

- Python/FastAPI backend, vanilla HTML/CSS/JavaScript frontend; there is no frontend build step.
- Launch locally with `python run.py`. Sessions live in the server process: restarting a server discards its open files. When a user may be editing, run the new version on another available local port and let them export/migrate before stopping the old service.
- Keep the original PDF immutable. Validate a proposed page operation before committing it to browser history. Preview and export must replay the same operations.
- Image replacements must target only the selected occurrence, including images within shared Form XObjects. Preserve surrounding transforms, clipping and paint order.
- History retains up to 40 transitions, permits jumping to a saved state and preserves undo/redo across a refresh while the server session remains alive. Editing after rollback replaces the later branch.
- Run meaningful regression checks for the changed behavior. Standard commands (use `.venv/Scripts/python.exe` on Windows):
  - `python -m pytest -q`
  - `node --check app/static/app.js`
  - `python tests/browser_smoke.py --url http://127.0.0.1:8000`
  - `python tests/browser_text_regressions.py --url http://127.0.0.1:8000`
  - `python tests/browser_draft_regressions.py --url http://127.0.0.1:8000`
  - `python tests/browser_history_images.py --url http://127.0.0.1:8000`
- Do not commit `.venv`, `work`, test artifacts, private PDFs, credentials, local font files or runtime binaries. Keep verification evidence and known limitations accurate in `docs/TEST_REPORT.md`.
