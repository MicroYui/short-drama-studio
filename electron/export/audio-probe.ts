import { spawnSync } from 'child_process'

// Shared by desktop export and the Agent's local export process.
export function fileHasAudio(ffmpegPath: string, filePath: string): boolean {
  return fileMediaStreams(ffmpegPath, filePath).audio
}

export function fileMediaStreams(ffmpegPath: string, filePath: string): { audio: boolean; video: boolean } {
  const result = spawnSync(ffmpegPath, ['-i', filePath, '-hide_banner'], { encoding: 'utf8', timeout: 5000 })
  const output = `${result.stdout || ''}${result.stderr || ''}`
  return { audio: output.includes('Audio:'), video: output.includes('Video:') }
}
