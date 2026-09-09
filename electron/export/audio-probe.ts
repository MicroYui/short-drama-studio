import { spawnSync } from 'child_process'

// Shared by desktop export and the Agent's local export process.
export function fileHasAudio(ffmpegPath: string, filePath: string): boolean {
  const result = spawnSync(ffmpegPath, ['-i', filePath, '-hide_banner'], { encoding: 'utf8', timeout: 5000 })
  return `${result.stdout || ''}${result.stderr || ''}`.includes('Audio:')
}
