# Archived voice tests

These seven test files target voice modules removed in the voice runtime rewrite.
They are retained as historical regression specifications. Their relative module
imports no longer resolve, so they are outside Vitest's `src/**/*.test.{ts,tsx}`
discovery pattern. Current voice coverage lives in `src/features/voice/` and runs
in `npm test` and CI. Port these cases to current APIs before reactivating them.
