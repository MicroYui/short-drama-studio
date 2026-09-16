import type { ColorCorrection } from '../../frontend/types/project-model'

/** Match the ordered sRGB CSS filters used by getClipEffectStyles.
 * Each stage clamps independently, as CSS does; merging the matrices would
 * change bright or saturated pixels when a later operation darkens them.
 * https://www.w3.org/TR/filter-effects-1/#FilterProperty
 */
export function buildStudioColorFilters(correction?: ColorCorrection): string {
  if (!correction) return ''
  const keys = ['brightness', 'contrast', 'saturation', 'exposure', 'temperature', 'tint', 'highlights', 'shadows'] as const
  for (const key of keys) {
    if (!Number.isFinite(correction[key]) || correction[key] < -100 || correction[key] > 100) {
      throw new Error(`调色参数 ${key} 须在 -100 到 100 之间。`)
    }
  }
  if (keys.every(key => correction[key] === 0)) return ''
  const filters = ['format=rgba']
  const fixed = (value: number) => value.toFixed(9)
  const transfer = (slope: number, intercept = 0) => {
    const expression = `'clip(val*${fixed(slope)}+${fixed(intercept * 255)},0,255)'`
    filters.push(`lutrgb=r=${expression}:g=${expression}:b=${expression}`)
  }
  const matrix = (values: number[]) => {
    const channels = ['rr', 'rg', 'rb', 'gr', 'gg', 'gb', 'br', 'bg', 'bb']
    filters.push(`colorchannelmixer=${values.map((value, index) => `${channels[index]}=${fixed(value)}`).join(':')}`)
  }
  const hue = (degrees: number) => {
    const cosine = Math.cos(degrees * Math.PI / 180), sine = Math.sin(degrees * Math.PI / 180)
    matrix([
      .213 + cosine * .787 - sine * .213, .715 - cosine * .715 - sine * .715, .072 - cosine * .072 + sine * .928,
      .213 - cosine * .213 + sine * .143, .715 + cosine * .285 + sine * .140, .072 - cosine * .072 - sine * .283,
      .213 - cosine * .213 - sine * .787, .715 - cosine * .715 + sine * .715, .072 + cosine * .928 + sine * .072,
    ])
  }
  const cc = correction
  if (cc.brightness !== 0) transfer(1 + cc.brightness / 100)
  if (cc.contrast !== 0) transfer(1 + cc.contrast / 100, -cc.contrast / 200)
  if (cc.saturation !== 0) {
    const saturation = 1 + cc.saturation / 100
    matrix([
      .213 + .787 * saturation, .715 - .715 * saturation, .072 - .072 * saturation,
      .213 - .213 * saturation, .715 + .285 * saturation, .072 - .072 * saturation,
      .213 - .213 * saturation, .715 - .715 * saturation, .072 + .928 * saturation,
    ])
  }
  if (cc.exposure !== 0) transfer(1 + cc.exposure / 200)
  if (cc.temperature > 0) {
    const amount = cc.temperature / 200
    matrix([1 - .607 * amount, .769 * amount, .189 * amount,
      .349 * amount, 1 - .314 * amount, .168 * amount,
      .272 * amount, .534 * amount, 1 - .869 * amount])
    hue(-cc.temperature * .1)
  } else if (cc.temperature < 0) hue(Math.abs(cc.temperature) * .4)
  if (cc.tint !== 0) hue(cc.tint * 1.2)
  if (cc.highlights !== 0) transfer(1 + cc.highlights / 300)
  if (cc.shadows !== 0) transfer(1 + cc.shadows / 300, -cc.shadows / 600)
  return `,${filters.join(',')}`
}
