# Fluent Emoji in AI responses

The backend accepts optional semantic icon identifiers. Microsoft Fluent Emoji 3D assets are bundled by the frontend and served same-origin; icons do not require network access, image search, image nodes, or database migrations.

## Identifier contract

- Fluent icons use `fluent:<kebab-case-name>`, e.g. `fluent:rocket`, `fluent:light-bulb`, `fluent:brain`.
- The whole identifier is at most **100 characters**. The slug consists of nonempty ASCII lowercase-letter/digit segments separated by single hyphens: `^fluent:[a-z0-9]+(?:-[a-z0-9]+)*$`.
- URLs, paths, traversal, extensions, whitespace, uppercase letters, raw emoji, markup, and arbitrary styles are rejected in icon fields. Syntactic validation does not guarantee a slug exists in the bundled catalog.
- Legacy Lucide names remain supported: `zap`, `brain-circuit`, `search`, `layers`, `shield`, `lightbulb`, `circle-dot`, `check-check`, `blocks`, `sliders`, `file-text`, `database`, `chart`, `book-open`, `target`, `code`.
- Omitted or JSON `null` icons remain `None`, including historical snapshots. Empty strings are not identifiers.

Common Fluent names: `rocket`, `light-bulb`, `brain`, `books`, `direct-hit`, `check-mark-button`, `warning`, `sparkles`, `magnifying-glass-tilted-left`, `chart-increasing`.

## Supported locations

`Props.icon` decorates a StreamUI component heading. `Item.icon` decorates metric/timeline items, including items nested in a document's `visual`. Ordinary `DocumentNode` records may have an icon, including headings, sections, paragraphs, and rail items. A standalone `type: "icon"` node **requires** a non-null `icon`; it is not a layout and cannot have children. `item_status` remains exclusive to `rail_item`, and existing rail parent/child rules are unchanged.

```json
{
  "op": "mount",
  "block_id": "launch",
  "component": "document",
  "props": {"title": "Launch checklist", "icon": "fluent:rocket"},
  "upsert": {
    "blocks": [
      {"id": "overview", "type": "section", "label": "Overview", "icon": "fluent:books"},
      {"id": "heading", "parent_id": "overview", "type": "heading", "text": "Plan", "icon": "fluent:light-bulb"},
      {"id": "symbol", "parent_id": "overview", "type": "icon", "icon": "fluent:rocket"},
      {"id": "features", "parent_id": "overview", "type": "feature_list"},
      {"id": "learn", "parent_id": "features", "type": "rail_item", "label": "Learn", "text": "Review the supplied material.", "icon": "fluent:brain"}
    ]
  }
}
```

Icons are optional; choose them sparingly when they help communicate meaning. In ordinary Markdown, use normal Unicode emoji such as 🚀, 💡, or 🧠, **not** literal `fluent:slug` prose. The frontend converts recognized Unicode emoji to bundled artwork.

## Streaming and compatibility

The SUIP protocol version is unchanged. Existing mount, patch, revise, complete, preview, rollback, and history/replay mechanisms carry icon fields as ordinary validated snapshot data. `props` patches merge fields; collection upserts still replace entire records by ID, so resend an icon when replacing that same record if it should remain. Unmodified records retain their icons. Use `props.icon: null` to explicitly clear a heading icon. Preview still withholds incomplete measurements and incomplete identifier strings.

The shared presentation catalog teaches both direct rich-response and Agent modes. The `ui_stream` tool description and JSON schema expose the same contract. Direct inline documents also accept a standalone icon as useful content.

## Local assets and rendering

The frontend pins `@lobehub/fluent-emoji-3d@1.1.0` as a build dependency and includes its 3,391 WebP resources. The generated registry is `frontend/src/lib/fluent-emoji-data.json`; English Unicode names, common aliases (including `direct-hit`), Unicode graphemes, and codepoint identifiers resolve to the same local artwork. Tagged flags, skin tones, and ZWJ sequences remain whole. Unknown Unicode stays as text; failed images fall back to Unicode; unknown semantic names use a neutral existing icon.

`frontend/scripts/fluent-emoji-assets.ts` serves the pinned files in development and copies them plus their license into production output. Web and native renderer builds inherit the same plugin. URLs honor Vite's `BASE_URL`; images load individually with lazy loading and never require a third-party CDN. The image files are not embedded in JavaScript. `frontend/licenses/fluent-emoji.txt` and `THIRD_PARTY_NOTICES.md` record artwork, distribution, and Unicode licenses.

The shared Markdown pipeline replaces only prose emoji. Code fences, inline code, math, source URLs, image alt attributes, and explicit text-presentation symbols remain unchanged. Existing restricted image and HTML policies still apply to Stream UI Markdown.

Metadata regeneration is an explicit maintenance command from `frontend/` (requires network access only for this command):

```text
node scripts/generate-fluent-emoji.mjs
```

Builds and runtime use the checked-in index and installed pinned images, without fetching metadata.

## Verification

Focused tests (from `backend/`, Windows virtual environment):

```text
.venv/Scripts/python.exe -m pytest tests/unit/test_fluent_emoji.py tests/api/test_fluent_emoji_http.py
```

Frontend tests and build (from `frontend/`):

```text
npm run test -- src/lib/fluent-emoji.test.ts src/components/chat/answer-icon.test.tsx src/lib/stream-ui-icons.test.ts src/components/chat/stream-ui-icons.test.tsx
npm run build:check
```

The development-only `fluent-emoji-preview.html` uses the real message renderer, not a parallel rendering implementation. With a development server on port 5191, `node scripts/test-fluent-emoji-browser.mjs` exercises streaming, retained tab/input state, real local image decoding, unavailable-image fallbacks, legacy icons, code/math preservation, shared preview routes, and light/dark layouts at 1440px and 390px. Set `FLUENT_EMOJI_URL` to run it against a bundled fixture; set `FLUENT_EMOJI_SKIP_SHARED_PREVIEWS=1` when the deployment contains only that fixture. Browser evidence is written under `artifacts/fluent-emoji/`. Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` when using a different installed Chromium executable.
