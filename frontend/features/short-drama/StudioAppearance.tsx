import { useCallback, useLayoutEffect, useState } from 'react'
import { Check } from 'lucide-react'

const presets = [
  { id: 'mist', name: '雾白蓝', description: '清爽浅色，经典蓝色重点。', tone: '浅色' },
  { id: 'paper', name: '暖纸米', description: '温润纸色，柔和但清晰。', tone: '浅色' },
  { id: 'forest', name: '松林绿', description: '灰绿底色，沉静自然。', tone: '浅色' },
  { id: 'midnight', name: '深空灰', description: '深灰底色，明亮的文字。', tone: '深色' },
] as const

export type StudioTheme = typeof presets[number]['id']
const storageKey = 'short-drama:appearance'

function readTheme(): StudioTheme {
  try {
    const saved = localStorage.getItem(storageKey)
    return presets.find(preset => preset.id === saved)?.id ?? 'mist'
  } catch {
    return 'mist'
  }
}

export function useStudioAppearance(onError: (error: unknown) => void) {
  const [theme, setTheme] = useState<StudioTheme>(readTheme)
  useLayoutEffect(() => {
    // Root variables also reach the reference-image preview rendered in a portal.
    document.documentElement.dataset.studioTheme = theme
  }, [theme])

  const changeTheme = useCallback((next: StudioTheme) => {
    try {
      localStorage.setItem(storageKey, next)
      setTheme(next)
    } catch {
      onError(new Error('配色暂时无法保存，请检查当前应用的本地存储。'))
    }
  }, [onError])

  return { theme, changeTheme }
}

export function StudioAppearance({ theme, onTheme }: { theme: StudioTheme; onTheme: (theme: StudioTheme) => void }) {
  return <section className="sd-appearance" aria-label="工作台外观">
    <h3>选一个看得舒服的工作台。</h3>
    <p className="sd-hint" id="sd-theme-help">点选即可应用并自动保存，下次打开沿用。各套配色都保留清晰的正文、说明和状态文字。</p>
    <fieldset className="sd-theme-grid" aria-describedby="sd-theme-help">
      <legend className="sr-only">配色预设</legend>
      {presets.map(preset => <label className={`sd-theme-card ${theme === preset.id ? 'is-selected' : ''}`} key={preset.id}>
        <input type="radio" name="studio-theme" value={preset.id} checked={theme === preset.id} onChange={() => onTheme(preset.id)} aria-label={preset.name} />
        <div className="sd-theme-preview" data-studio-theme={preset.id} aria-hidden="true">
          <div className="sd-theme-preview-sidebar"><i /><i /><i /><i /></div>
          <div className="sd-theme-preview-main"><div><span>Aa</span><i /></div><strong>让想法，成为作品。</strong><span>剧本、镜头与制作进度</span><div className="sd-theme-preview-line" /><div className="sd-theme-preview-note"><i />正在整理镜头</div></div>
          <div className="sd-theme-preview-agent"><i /><i /><i /></div>
        </div>
        <div className="sd-theme-card-label"><strong>{preset.name}</strong><span>{preset.tone}</span></div>
        <p>{preset.description}</p>
      </label>)}
    </fieldset>
    <p className="sd-appearance-saved" role="status"><Check size={14} aria-hidden="true" />当前配色：{presets.find(preset => preset.id === theme)?.name} · 已保存</p>
  </section>
}
