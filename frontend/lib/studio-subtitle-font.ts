import { useEffect, useState } from 'react'
import type { SubtitleStyle } from '../types/project-model'

const availableFonts = new Map<string, Promise<string | null>>()
const fontScales = new Map<string, number>()

/** Verify the backend's selected face exists in this browser, without installing fonts. */
export function confirmStudioSubtitleFont(family: string | null | undefined): Promise<string | null> {
  if (!family || typeof FontFace === 'undefined') return Promise.resolve(null)
  let pending = availableFonts.get(family)
  if (!pending) {
    // local() matches face names; some collections expose "Family Regular".
    const sources = [family, `${family} Regular`].map(name => `local(${JSON.stringify(name)})`).join(', ')
    pending = new FontFace(family, sources).load().then(() => family, () => null)
    availableFonts.set(family, pending)
  }
  return pending
}

export function useStudioSubtitleFont(family: string | null | undefined): string | null {
  const [confirmed, setConfirmed] = useState<{ requested: string | null | undefined; family: string | null } | null>(null)
  useEffect(() => {
    let active = true
    void confirmStudioSubtitleFont(family).then(value => { if (active) setConfirmed({ requested: family, family: value }) })
    return () => { active = false }
  }, [family])
  return confirmed && confirmed.requested === family ? confirmed.family : null
}

export function studioSubtitlePreviewFont(style: SubtitleStyle, displayScale: number, confirmedDefaultFamily: string | null) {
  const family = style.fontFamily.trim().replace(/^['"]|['"]$/g, '').toLowerCase()
  const usesDefault = !family || family === 'sans-serif' || family === 'system-ui'
  if (!usesDefault || !confirmedDefaultFamily) return { fontFamily: style.fontFamily, fontSize: style.fontSize * displayScale, matchedDefault: false }
  const font = `${style.italic ? 'italic' : 'normal'} ${style.fontWeight} 1000px ${JSON.stringify(confirmedDefaultFamily)}`
  let metricScale = fontScales.get(font)
  if (metricScale === undefined) {
    const context = document.createElement('canvas').getContext('2d')
    if (context) {
      context.font = font
      const metrics = context.measureText('国Ag')
      const height = metrics.fontBoundingBoxAscent + metrics.fontBoundingBoxDescent
      if (Number.isFinite(height) && height > 0) metricScale = 1000 / height
    }
    if (metricScale === undefined) return { fontFamily: style.fontFamily, fontSize: style.fontSize * displayScale, matchedDefault: false }
    fontScales.set(font, metricScale)
  }
  // ASS font size describes the font's ascent+descent; CSS px describes its em.
  return { fontFamily: confirmedDefaultFamily, fontSize: style.fontSize * displayScale * metricScale, matchedDefault: true }
}
