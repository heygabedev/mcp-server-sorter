import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
execFileSync(
  process.execPath,
  ['node_modules/openapi-typescript/bin/cli.js', 'openapi.json', '-o', 'src/schema.ts'],
  { stdio: 'inherit' },
);
const source = readFileSync('src/schema.ts', 'utf8').replace(/^\/\*\*[\s\S]*?\*\/\s*/, '');
writeFileSync('src/schema.ts', source);
