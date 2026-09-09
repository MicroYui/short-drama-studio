import { spawnSync } from 'node:child_process'
import { mkdtemp, writeFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
process.chdir(root)
if (process.platform !== 'darwin') throw new Error('This local delivery command currently packages the verified macOS arm64 target.')
const cached = spawnSync(process.execPath, ['scripts/find-cached-pnpm.mjs', 'pnpm@10.30.3'], { encoding: 'utf8' })
if (cached.status !== 0 || !cached.stdout.trim()) throw new Error('Pinned pnpm is not cached. No package manager or dependency download was attempted.')
await mkdir('output', { recursive: true })
const bridge = await mkdtemp(path.join(root, 'output', 'studio-package-bin-'))
const quote = value => "'" + value.replaceAll("'", "'\\''") + "'"
await writeFile(path.join(bridge, 'pnpm'), `#!/bin/sh\nexec ${quote(process.execPath)} ${quote(cached.stdout.trim())} "$@"\n`, { mode: 0o755 })
const result = spawnSync(process.execPath, ['node_modules/electron-builder/cli.js', '--dir', '--mac', '--arm64', '--config.mac.identity=null', '--config.mac.notarize=false', '--config.npmRebuild=false', '--config.electronDist=node_modules/electron/dist', '--config.directories.output=output/product-delivery/package'], { stdio: 'inherit', env: { ...process.env, PATH: `${bridge}${path.delimiter}${process.env.PATH ?? ''}`, COREPACK_ENABLE_NETWORK: '0' } })
process.exitCode = result.status ?? 1
