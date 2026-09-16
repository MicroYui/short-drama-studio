import { spawn } from 'child_process'
import path from 'path'
import fs from 'fs'
import { logger } from '../logger'
import { fileMediaStreams } from './audio-probe'
import type { ExportClip } from './timeline'
import { clipAudioGain, type AudioFadeSettings } from '../../frontend/lib/audio-envelope'

const SAMPLE_RATE = 48000
const NUM_CHANNELS = 2
const BYTES_PER_SAMPLE = 2 // 16-bit signed LE
const BYTES_PER_FRAME = NUM_CHANNELS * BYTES_PER_SAMPLE // 4 bytes per stereo frame

/** Extract raw PCM from a file via ffmpeg stdout pipe */
function extractPcmBuffer(
  ffmpegPath: string,
  filePath: string, trimStart: number, trimEnd: number, speed: number, reversed: boolean
): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    // Build audio filter chain: trim -> reset PTS -> speed -> reverse
    // Using atrim (not -ss/-t) for sample-accurate trimming
    const filters: string[] = [
      `atrim=start=${trimStart.toFixed(6)}:end=${trimEnd.toFixed(6)}`,
      'asetpts=PTS-STARTPTS',
    ]
    if (speed !== 1) {
      // atempo only supports 0.5-100, chain multiple for extreme values
      let remaining = speed
      while (remaining > 2.0) { filters.push('atempo=2.0'); remaining /= 2.0 }
      while (remaining < 0.5) { filters.push('atempo=0.5'); remaining /= 0.5 }
      filters.push(`atempo=${remaining.toFixed(6)}`)
    }
    if (reversed) filters.push('areverse')

    const args = [
      '-i', filePath,
      '-af', filters.join(','),
      '-f', 's16le', '-ac', String(NUM_CHANNELS), '-ar', String(SAMPLE_RATE),
      'pipe:1',
    ]
    const proc = spawn(ffmpegPath, args, { stdio: ['pipe', 'pipe', 'pipe'] })
    const chunks: Buffer[] = []
    proc.stdout?.on('data', (chunk: Buffer) => chunks.push(chunk))
    proc.stderr?.on('data', () => {}) // drain stderr to prevent blocking
    proc.on('close', (code) => {
      if (code === 0) resolve(Buffer.concat(chunks))
      else reject(new Error(`PCM extraction failed (code ${code}) for ${filePath}`))
    })
    proc.on('error', reject)
  })
}

interface AudioSource extends AudioFadeSettings {
  filePath: string; trimStart: number; trimEnd: number;
  timelineStart: number; duration: number; speed: number; reversed: boolean; volume: number;
}

/**
 * Mix all audio from clips into a single PCM buffer.
 * Returns raw Int16LE PCM data with the sample rate and channel count.
 */
export async function mixAudioToPcm(
  clips: ExportClip[],
  totalDuration: number,
  ffmpegPath: string,
  strict = false,
): Promise<{ pcmBuffer: Buffer; sampleRate: number; channels: number; peakBeforeGain: number; outputGain: number }> {
  // Collect audio sources from ORIGINAL clips
  const audioProbeCache = new Map<string, { audio: boolean; video: boolean }>()
  const audioSources: AudioSource[] = []

  for (const c of clips) {
    if (c.muted || c.volume <= 0 || c.audioDelegated) continue
    const fp = c.path
    if (!fp || !fs.existsSync(fp)) {
      if (strict && (c.type === 'audio' || c.type === 'video')) throw new Error(`声音素材不可读取：${fp}`)
      continue
    }

    if (c.type === 'video' || c.sourceType === 'video') {
      if (!audioProbeCache.has(fp)) audioProbeCache.set(fp, fileMediaStreams(ffmpegPath, fp))
      const streams = audioProbeCache.get(fp)!
      // Only a confirmed video container with no audio is silent. A broken
      // audio source must still fail strict export instead of disappearing.
      if (!streams.audio && streams.video) continue
    }
    if (c.type === 'audio' || c.type === 'video') {
      audioSources.push({
        ...c,
        filePath: fp,
        trimStart: c.trimStart,
        trimEnd: c.trimStart + c.duration * c.speed,
        timelineStart: c.startTime,
        speed: c.speed,
        reversed: c.reversed,
        volume: c.volume,
      })
    }
  }

  logger.info( `[Export] Audio: ${audioSources.length} source(s) from ${clips.length} clip(s)`)

  // Create master mix buffer (Float64 to accumulate without clipping)
  const totalFrames = Math.ceil(totalDuration * SAMPLE_RATE)
  const totalSamples = totalFrames * NUM_CHANNELS
  const mixBuffer = new Float64Array(totalSamples) // initialized to 0 (silence)

  // Extract each source and mix into the master buffer
  for (let i = 0; i < audioSources.length; i++) {
    const src = audioSources[i]
    logger.info( `[Export] Audio ${i + 1}/${audioSources.length}: ${path.basename(src.filePath)} trim=${src.trimStart.toFixed(2)}-${src.trimEnd.toFixed(2)} @${src.timelineStart.toFixed(2)}s vol=${src.volume}`)
    try {
      const pcm = await extractPcmBuffer(ffmpegPath, src.filePath, src.trimStart, src.trimEnd, src.speed, src.reversed)
      const startFrame = Math.round(src.timelineStart * SAMPLE_RATE)
      const startSample = startFrame * NUM_CHANNELS
      const numPcmSamples = Math.floor(pcm.length / BYTES_PER_SAMPLE)

      for (let s = 0; s < numPcmSamples; s++) {
        const destIdx = startSample + s
        if (destIdx < 0 || destIdx >= totalSamples) continue
        const value = pcm.readInt16LE(s * BYTES_PER_SAMPLE)
        const localTime = Math.floor(s / NUM_CHANNELS) / SAMPLE_RATE
        mixBuffer[destIdx] += value * clipAudioGain(src, localTime)
      }
      logger.info( `[Export] Audio ${i + 1}: mixed ${numPcmSamples} samples (${(numPcmSamples / SAMPLE_RATE / NUM_CHANNELS).toFixed(2)}s) at offset frame ${startFrame}`)
    } catch (err: any) {
      if (strict) throw err
      logger.warn( `[Export] Failed to extract audio from ${src.filePath}: ${err.message}`)
    }
  }

  // Keep normal mixes unchanged. If overlapping tracks exceed full scale,
  // lower the complete stereo mix uniformly rather than flattening its peaks.
  let peakBeforeGain = 0
  for (const sample of mixBuffer) {
    // Int16 has one extra negative value; an untouched -32768 is valid audio.
    peakBeforeGain = Math.max(peakBeforeGain, sample >= 0 ? sample / 32767 : -sample / 32768)
  }
  // Leave a little room for AAC reconstruction after the PCM mix (about 0.45 dB).
  const outputGain = peakBeforeGain > 1 ? 0.95 / peakBeforeGain : 1
  logger.info(`[Export] Audio peak=${peakBeforeGain.toFixed(6)} outputGain=${outputGain.toFixed(6)}`)
  // The clamp below only guards numerical rounding after uniform attenuation.
  const outputPcm = Buffer.alloc(totalFrames * BYTES_PER_FRAME)
  for (let s = 0; s < totalSamples; s++) {
    const clamped = Math.max(-32768, Math.min(32767, Math.round(mixBuffer[s] * outputGain)))
    outputPcm.writeInt16LE(clamped, s * BYTES_PER_SAMPLE)
  }

  return { pcmBuffer: outputPcm, sampleRate: SAMPLE_RATE, channels: NUM_CHANNELS, peakBeforeGain, outputGain }
}
