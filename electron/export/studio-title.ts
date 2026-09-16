import type { TextOverlayStyle } from '../../frontend/types/project-model'

export function validateStudioTitle(style: TextOverlayStyle | undefined): asserts style is TextOverlayStyle {
  if (!style || typeof style.text !== 'string') throw new Error('标题缺少正文或样式。')
  if (/[{}\\]/.test(style.text)) throw new Error('标题暂不支持花括号或反斜杠，请使用常规标点后导出。')
  const supported = style.fontFamily === 'Arial' && style.fontStyle === 'normal' && style.textAlign === 'center'
    && style.backgroundColor === 'transparent' && style.strokeWidth === 0 && style.shadowBlur === 0
    && style.shadowOffsetX === 0 && style.shadowOffsetY === 0 && style.letterSpacing === 0
    && style.lineHeight === 1.2 && style.maxWidth === 0 && style.padding === 0 && style.borderRadius === 0
  if (!supported) throw new Error('此标题包含尚未接通的样式；请在标题属性中恢复基本样式，再设置正文、字号、位置和颜色。')
  for (const [name, value, min, max] of [['字号', style.fontSize, 1, 500], ['横向位置', style.positionX, 0, 100],
    ['纵向位置', style.positionY, 0, 100], ['不透明度', style.opacity, 0, 100]] as const) {
    if (!Number.isFinite(value) || value < min || value > max) throw new Error(`标题${name}超出可用范围。`)
  }
  if (!/^#[\da-f]{6}$/i.test(style.color)) throw new Error('标题颜色须为六位十六进制颜色。')
}

/** Render one transparent frame through the FFmpeg/libass runtime already shipped. */
export function buildStudioTitleAss(style: TextOverlayStyle, width: number, height: number): string {
  validateStudioTitle(style)
  const scriptWidth = width / height * 1080
  const alpha = Math.round(255 * (1 - style.opacity / 100)).toString(16).padStart(2, '0')
  const color = style.color.slice(1)
  const assColor = `&H${alpha}${color.slice(4, 6)}${color.slice(2, 4)}${color.slice(0, 2)}`
  const lines = style.text.replace(/\r\n?/g, '\n').split('\n')
  return ['[Script Info]', 'ScriptType: v4.00+', `PlayResX: ${scriptWidth}`, 'PlayResY: 1080', 'WrapStyle: 2',
    'ScaledBorderAndShadow: yes', '[V4+ Styles]',
    'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
    `Style: Title,Arial,${style.fontSize},${assColor},${assColor},&HFF000000,&HFF000000,${style.fontWeight === 'bold' ? -1 : 0},0,0,0,100,100,0,0,1,0,0,5,0,0,0,1`,
    '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ...lines.map((text, index) => `Dialogue: 0,0:00:00.00,0:00:01.00,Title,,0,0,0,,{\\an5\\q2\\pos(${scriptWidth * style.positionX / 100},${1080 * style.positionY / 100 + (index - (lines.length - 1) / 2) * style.fontSize * style.lineHeight})}${text}`), ''].join('\n')
}

export function studioAssFilter(path: string): string {
  return `ass=filename='${path.replace(/\\/g, '/').replace(/:/g, '\\:').replace(/'/g, "'\\''")}':alpha=1,format=gbrap,unpremultiply=inplace=1,format=rgba`
}
