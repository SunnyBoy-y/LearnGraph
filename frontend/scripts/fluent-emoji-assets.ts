import { createReadStream } from 'node:fs';
import { cp, mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Plugin } from 'vite';

const version = '1.1.0';
const assetDirectory = fileURLToPath(new URL('../node_modules/@lobehub/fluent-emoji-3d/assets/', import.meta.url));
const license = fileURLToPath(new URL('../licenses/fluent-emoji.txt', import.meta.url));
const prefix = `fluent-emoji/${version}/`;

/** Ship trusted image assets on the same origin, outside the JavaScript bundle. */
export function fluentEmojiAssets(): Plugin {
  let outputDirectory = '';
  let base = '/';
  return {
    name: 'learngraph-fluent-emoji',
    configResolved(config) {
      outputDirectory = path.resolve(config.root, config.build.outDir, prefix);
      base = config.base;
    },
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const pathname = request.url?.split('?')[0] ?? '';
        const localPrefix = `${base}${prefix}`;
        if (!pathname.startsWith(localPrefix)) return next();
        const filename = pathname.slice(localPrefix.length);
        if (!/^[0-9a-f]+(?:[-_][0-9a-f]+)*\.webp$/.test(filename) && filename !== 'LICENSE.txt') {
          response.statusCode = 404;
          response.end();
          return;
        }
        const source = createReadStream(filename === 'LICENSE.txt' ? license : path.join(assetDirectory, filename));
        source.on('error', () => {
          if (!response.headersSent) response.statusCode = 404;
          response.end();
        });
        response.setHeader('Content-Type', filename === 'LICENSE.txt' ? 'text/plain; charset=utf-8' : 'image/webp');
        response.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
        source.pipe(response);
      });
    },
    async writeBundle() {
      // Reading the license also makes a missing attribution fail the build.
      await readFile(license);
      await mkdir(outputDirectory, { recursive: true });
      await cp(assetDirectory, outputDirectory, { recursive: true });
      await cp(license, path.join(outputDirectory, 'LICENSE.txt'));
    },
  };
}
