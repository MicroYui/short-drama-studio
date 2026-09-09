import { createRequire } from 'node:module'
import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
await build({ entryPoints: ['electron/export/studio-export-cli.ts'], bundle: true, platform: 'node', target: 'node20', format: 'cjs', outfile: 'backend/short_drama/runtime/studio-export.cjs', sourcemap: false })
// The upstream Python bootstrap uses this only to identify its dependency cache.
const dependencies = await Promise.all(['backend/pyproject.toml', 'backend/uv.lock'].map(path => readFile(path)))
await writeFile('python-deps-hash.txt', createHash('sha256').update(Buffer.concat(dependencies)).digest('hex') + '\n')
