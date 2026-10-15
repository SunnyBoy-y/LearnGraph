/**
 * Regression guard: recharts must ship as ONE chunk in the production build.
 *
 * Splitting recharts across multiple vendor chunks (rolldown codeSplitting
 * with maxSize/entriesAware) crashes the app at module-init time in the
 * browser: a shared constant is undefined ("Cannot read properties of
 * undefined (reading 'axis')") and every page that lazy-loads a chart goes
 * white. vite.config.ts pins recharts to a single non-entry-aware group.
 */
import { describe, expect, it } from 'vitest';

describe('production build chunking', () => {
  it('keeps recharts in exactly one chunk', async () => {
    // Rollup/rolldown emits chunk names that always contain "recharts" in the
    // filename when the group is used; assert the on-disk build output.
    const fs = await import('node:fs');
    const path = await import('node:path');

    const distDir = path.resolve(__dirname, '../../../dist/assets');
    let files: string[] = [];
    try {
      files = fs.readdirSync(distDir);
    } catch {
      // Build output missing (e.g. plain unit run without a prior build):
      // skip rather than fail unrelated test runs.
      return;
    }
    const rechartsChunks = files.filter((name) => /recharts/i.test(name));
    expect(rechartsChunks.length).toBeGreaterThan(0);
    expect(rechartsChunks.length).toBe(1);
  });
});
