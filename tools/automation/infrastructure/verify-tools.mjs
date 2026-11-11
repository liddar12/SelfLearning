import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(fileURLToPath(import.meta.url));
const expected = { netlify: '27.17.0', supabase: '3.1.0', vercel: '65.14.0' };
const results = [];
for (const [name, version] of Object.entries(expected)) {
  const executable = path.join(root, 'node_modules', '.bin', name + (process.platform === 'win32' ? '.cmd' : ''));
  const result = spawnSync(executable, ['--version'], { encoding: 'utf8', timeout: 30000, shell: process.platform === 'win32' });
  const combined = `${result.stdout ?? ''}\n${result.stderr ?? ''}`;
  const observed = combined.match(/\b\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?\b/)?.[0] ?? null;
  results.push({ tool: name, expected: version, observed, installed: result.status === 0 && observed === version });
}
console.log(JSON.stringify({ scope: 'This checkout only; no account authorization, remote change, or user-device installation is implied.', tools: results }, null, 2));
process.exitCode = results.every(result => result.installed) ? 0 : 1;
