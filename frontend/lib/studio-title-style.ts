import type { TextOverlayStyle } from '../types/project-model'

/** Titles use output-height units, like Studio subtitles (1080 units high). */
export const STUDIO_TITLE_STYLE: TextOverlayStyle = {
  text: '新的标题', fontFamily: 'Arial', fontSize: 64, fontWeight: 'bold', fontStyle: 'normal',
  color: '#FFFFFF', backgroundColor: 'transparent', textAlign: 'center', positionX: 50, positionY: 50,
  strokeColor: 'transparent', strokeWidth: 0, shadowColor: 'transparent', shadowBlur: 0,
  shadowOffsetX: 0, shadowOffsetY: 0, letterSpacing: 0, lineHeight: 1.2, maxWidth: 0,
  padding: 0, borderRadius: 0, opacity: 100,
}
