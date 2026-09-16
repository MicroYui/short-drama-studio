/** Clip-local audio gain shared by live playback and sample-accurate export. */
export interface AudioFadeSettings {
  audioFadeInSeconds?: number
  audioFadeOutSeconds?: number
  // A split retains the original envelope's coordinates instead of fading
  // again at the cut. Explicitly editing either fade clears these two values.
  audioEnvelopeOffsetSeconds?: number | null
  audioEnvelopeDurationSeconds?: number | null
}

interface AudioEnvelopeClip extends AudioFadeSettings {
  duration: number
  volume: number
}

const unit = (value: number) => Math.max(0, Math.min(1, value))

export function clipAudioGain(clip: AudioEnvelopeClip, localTimeSeconds: number): number {
  if (localTimeSeconds < 0 || localTimeSeconds >= clip.duration) return 0
  const duration = clip.audioEnvelopeDurationSeconds ?? clip.duration
  const time = localTimeSeconds + (clip.audioEnvelopeOffsetSeconds ?? 0)
  const fadeIn = Math.min(duration, Math.max(0, clip.audioFadeInSeconds ?? 0))
  const fadeOut = Math.min(duration, Math.max(0, clip.audioFadeOutSeconds ?? 0))
  return unit(clip.volume) * Math.min(fadeIn > 0 ? unit(time / fadeIn) : 1,
    fadeOut > 0 ? unit((duration - time) / fadeOut) : 1)
}

export function sliceClipAudioEnvelope(clip: AudioEnvelopeClip, offsetSeconds: number): AudioFadeSettings {
  if (!(clip.audioFadeInSeconds || clip.audioFadeOutSeconds || clip.audioEnvelopeDurationSeconds)) return {}
  return {
    audioEnvelopeOffsetSeconds: (clip.audioEnvelopeOffsetSeconds ?? 0) + offsetSeconds,
    audioEnvelopeDurationSeconds: clip.audioEnvelopeDurationSeconds ?? clip.duration,
  }
}
