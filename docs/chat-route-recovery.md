# New-chat production render recovery

## Reproduced failures

The reported `/w/:workspaceId/chat/new` route displayed the shared route error boundary.
Source-level tests and an empty production canvas passed, so a source-only render test did not cover the production failure.

A valid persisted Markdown draft (`#### 应当保留的格式化草稿`) reproduced the same error page in the existing production build on port 18000. The browser logged:

```text
TypeError: a is not a constructor
  at /assets/vendor~composer-rich-input-CcgGnpgg.js:2:11746
```

The failing initialization was ProseMirror history's `new PluginKey('history')`; `PluginKey` was imported from another editor vendor chunk. The generic size/entry-aware vendor splitting had introduced a cyclic cross-chunk initialization dependency. This failure does not require corrupted user data.

A separate malformed persisted draft (`files: null`, `pendingFiles: null`, `stickers: {}`, `queue: null`) also reproduced the error boundary with `Cannot read properties of null (reading 'length')` in `ChatCanvasPage`. The user's actual browser storage was not inspected, so this additional reproduction is not evidence that their data was corrupted.

## Changes

- `frontend/vite.config.ts`: keep Tiptap, ProseMirror and their editor support packages in one `composer-editor` chunk; disable entry-aware and size-based splitting for that group. The editor remains lazily loaded.
- `frontend/src/features/chat/composer-session-state.ts`: validate restored record/field shapes before consumers use them; repair invalid fields in the resident cache without clearing stored drafts or unrelated scopes. Reject malformed IndexedDB envelopes and skip invalid queue entries during binary restoration.
- `frontend/src/components/shared/route-error-boundary.tsx`: show expandable error details; reset them when retrying or changing routes. No draft storage is cleared.
- Add draft recovery and route-boundary regression tests, plus `scripts/qa-chat-route-recovery.cjs` for production-browser coverage.

## Verification

- `npm run typecheck`: passed.
- `npm run build`: passed.
- Targeted Vitest run with `--maxWorkers=1`: 4 files, 35 tests passed. Files: `composer-session-state.test.tsx`, `new-chat-draft.test.tsx`, `composer-safety.test.tsx`, `route-error-boundary.test.tsx`.
- A first parallel test run had one formatted-draft heading lookup timeout; its isolated retry and the complete serial run passed without changing that existing test.
- Browser scenarios against both the Vite production preview and port 18000: empty canvas, restored Markdown, malformed attachment fields, and typing Markdown into a fresh canvas. Every scenario passed an initial load and reload, retained draft text, logged zero JavaScript errors and made zero server writes.
- Browser tests use isolated contexts and mocked API responses, not the user's authentication session or actual conversation content.

Run the browser regression against the local service:

```bash
npx --yes --package=playwright -c "node scripts/qa-chat-route-recovery.cjs"
```

Set `CHAT_QA_URL` to test another frontend origin. Set `CHAT_QA_SCREENSHOT` to save a screenshot of the restored-Markdown scenario.

## Local runtime update

The rebuilt hashed assets were copied to `learngraph-app-1:/app/frontend-dist/assets/` before replacing `index.html`. Old hashed assets were retained for already-open tabs. No database writes, data-volume changes, backend restart, or image recreation were performed. `/api/v1/livez` remained healthy.

The source fixes are in the workspace. The running container's static assets are updated; recreating it from an older image would discard that runtime-only asset update, so future recreations must use an image built from the corrected source.

A browser reload (Ctrl+Shift+R if the old module graph is still open) loads the corrected entry. There is no need to clear browser site data.
