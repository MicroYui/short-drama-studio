import { useRef, useState } from 'react'
import { ArrowRight, FileUp, Plus, Save, Sparkles, Pencil } from 'lucide-react'
import type { ProjectAggregate } from './api'
import { currentScript, durationLabel } from './studio-content'
import { StudioApi, type ScriptScene, type StudioScope } from './studio-api'

interface Props { project: ProjectAggregate; onProject: (project: ProjectAggregate) => void; onAsk: (text: string, scope: StudioScope) => void; onScope: (scope: StudioScope) => void; onShots: () => void; onError: (error: unknown) => void }

function SceneEditor({ scene, index, projectId, onProject, onScope, onAsk, onError }: { scene: ScriptScene; index: number; projectId: string } & Omit<Props, 'project' | 'onShots'>) {
  const [draft, setDraft] = useState<{ heading: string; text: string; base: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const scope: StudioScope = { kind: 'scene', id: scene.id, label: `场次 ${index + 1} · ${scene.heading}` }
  const save = async (overwrite = false) => {
    if (!draft) return
    setSaving(true)
    try {
      const result = await StudioApi.saveScene(projectId, { scene_id: scene.id, heading: draft.heading, screenplay_text: draft.text, expected_text: overwrite ? scene.screenplay_text : draft.base })
      onProject(result.project)
      setDraft(null)
    } catch (error) { onError(error) } finally { setSaving(false) }
  }
  const conflict = draft && draft.base !== scene.screenplay_text
  return <section className="sd-scene" id={`script-${scene.id}`} onFocus={() => onScope(scope)}>
    <div className="sd-scene-title"><span className="sd-scene-number">{String(index + 1).padStart(2, '0')}</span><div>
      {draft ? <input aria-label={`场次 ${index + 1} 标题`} value={draft.heading} onChange={event => setDraft({ ...draft, heading: event.target.value })} /> : <h3>{scene.heading}</h3>}
      <span title={scene.narrative_purpose || undefined}>{scene.target_duration_seconds ? `约 ${durationLabel(scene.target_duration_seconds)}` : '完整场次'}{scene.narrative_purpose ? ` · ${scene.narrative_purpose}` : ''}</span>
    </div>{!draft && <button className="sd-text-button" aria-label={`编辑场次 ${index + 1} 正文`} onClick={() => { setDraft({ heading: scene.heading, text: scene.screenplay_text, base: scene.screenplay_text }); onScope(scope) }}><Pencil size={13} />编辑</button>}<button className="sd-icon" aria-label={`请 Agent 修改场次 ${index + 1}`} title="请 Agent 修改这个场次" onClick={() => { onScope(scope); onAsk('', scope) }}><Sparkles size={16} /></button></div>
    {draft ? <><textarea className="sd-screenplay-editor" aria-label={`场次 ${index + 1} 正文`} value={draft.text} onChange={event => setDraft({ ...draft, text: event.target.value })} style={{ minHeight: Math.min(680, Math.max(220, draft.text.split('\n').length * 26 + 60)) }} />
      {conflict && <p className="sd-inline-notice">这个场次刚有新内容。你的编辑已保留，可查看最新正文后合并。<button onClick={() => { if (draft) setDraft({ ...draft, base: scene.screenplay_text }) }}>我已对照最新正文</button><details><summary>查看最新正文</summary><pre>{scene.screenplay_text}</pre></details></p>}
      <div className="sd-editor-actions"><button className="sd-text-button" onClick={() => setDraft(null)}>取消编辑</button><button className="sd-button sd-primary" disabled={saving || !!conflict || !draft.heading.trim()} onClick={() => void save()}><Save size={14} />{saving ? '正在保存…' : '保存场次'}</button></div>
    </> : <div className="sd-screenplay">{scene.screenplay_text || <span className="sd-hint">场次已安排，正文尚未写入。</span>}</div>}
  </section>
}

export function StudioScript(props: Props) {
  const { project, onProject, onAsk, onScope, onShots, onError } = props
  const script = currentScript(project)
  const fileInput = useRef<HTMLInputElement>(null)
  const [importing, setImporting] = useState(false)
  const projectId = project.document.project.id
  const scenes = script?.structured_scenes ?? []
  const scope: StudioScope = { kind: 'episode', label: project.document.project.episode_title }
  const add = async () => {
    try { const result = await StudioApi.saveScene(projectId, { heading: `场次 ${scenes.length + 1}`, screenplay_text: '', lines: [] }); onProject(result.project) } catch (error) { onError(error) }
  }
  return <div className="sd-script-page">
    <header className="sd-page-header"><div><span className="sd-eyebrow">STORY</span><h1>让故事，从这里开始。</h1><p>写下一个想法，或带来已有剧本。每一场都可以直接修改。</p></div><button className="sd-button" onClick={() => fileInput.current?.click()} disabled={importing}><FileUp size={16} />{importing ? '正在导入…' : '导入剧本'}</button></header>
    <input hidden ref={fileInput} type="file" accept=".txt,.md" onChange={event => { const file = event.target.files?.[0]; if (!file) return; setImporting(true); void file.text().then(text => StudioApi.saveScene(projectId, { heading: file.name.replace(/\.[^.]+$/, ''), screenplay_text: text })).then(result => onProject(result.project)).catch(onError).finally(() => { setImporting(false); if (fileInput.current) fileInput.current.value = '' }) }} />
    {scenes.length ? <div className="sd-script-layout"><aside className="sd-scene-outline"><div className="sd-mini-heading">本集场次 <span>{scenes.length}</span></div>{scenes.map((scene, index) => <button key={scene.id} onClick={() => { document.getElementById(`script-${scene.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); onScope({ kind: 'scene', id: scene.id, label: `场次 ${index + 1} · ${scene.heading}` }) }}><span>{String(index + 1).padStart(2, '0')}</span><div>{scene.heading}</div></button>)}<button className="sd-outline-add" onClick={() => void add()}><Plus size={14} />添加场次</button></aside>
      <article className="sd-script-paper"><div className="sd-paper-heading"><span>{project.document.project.episode_title}</span><h2>{project.document.project.name}</h2><p>{scenes.length} 场 · {script?.screenplay_text.length ?? 0} 字</p></div>{scenes.map((scene, index) => <SceneEditor key={scene.id} {...props} scene={scene} index={index} projectId={projectId} />)}<footer className="sd-paper-footer"><span>故事仍由你做主。</span><button className="sd-text-button" onClick={onShots}>查看镜头设计 <ArrowRight size={14} /></button></footer></article></div>
      : <div className="sd-start-story"><div className="sd-start-orb"><Sparkles size={28} /></div><h2>一句话，也可以是一部作品的开始。</h2><p>在右侧告诉 Agent 想做什么。可以只写剧本，也可以把目标交给它，一步步完成视频。</p><div className="sd-example-prompts"><button onClick={() => onAsk('根据我的创意，先生成完整剧本。', scope)}>先写完整剧本 <ArrowRight size={14} /></button><button onClick={() => onAsk('把我的创意制作成完整视频。', scope)}>直接制作成片 <ArrowRight size={14} /></button></div><button className="sd-text-button" onClick={() => void add()}>或从空白场次开始</button></div>}
  </div>
}
