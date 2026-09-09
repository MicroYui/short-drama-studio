import { useEffect, useRef, useState } from 'react'
import { Check, FileJson, Loader2, Play, Upload } from 'lucide-react'
import type { ProjectAggregate } from './api'
import { StudioApi, studioRequest } from './studio-api'
import { assetGroups } from './studio-content'

type Graph = Record<string, { class_type: string; inputs: Record<string, unknown>; _meta?: { title?: string } }>
type Run = { id: string; name: string; status: string; error?: string | null; output_asset_ids: string[] }
const runningStatuses = new Set(['queued', 'submitting', 'running'])

export function StudioWorkflows({ project, connected, onProject, onError }: { project: ProjectAggregate; connected: boolean; onProject: (value: ProjectAggregate) => void; onError: (error: unknown) => void }) {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [name, setName] = useState('')
  const [bindings, setBindings] = useState<Record<string, string>>({})
  const [run, setRun] = useState<Run | null>(null)
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const files = assetGroups(project).flatMap(group => group.files.map(file => ({ ...file, name: group.name })))
  const inputs = Object.entries(graph ?? {}).flatMap(([id, node]) => Object.entries(node.inputs).filter(([, value]) => typeof value === 'string').map(([field, value]) => ({ id, field, value, key: `${id}:${field}`, label: node._meta?.title || node.class_type })))
  useEffect(() => {
    if (!run || !runningStatuses.has(run.status)) return
    let active = true
    const timer = setInterval(() => { void studioRequest<Run>(`/api/short-drama/studio/projects/${project.document.project.id}/workflows/${run.id}`).then(async next => { if (!active) return; setRun(next); if (!runningStatuses.has(next.status)) onProject(await StudioApi.getProject(project.document.project.id)) }).catch(onError) }, 1700)
    return () => { active = false; clearInterval(timer) }
  }, [run?.id, run?.status, project.document.project.id, onProject, onError])
  const start = async () => {
    if (!graph) return
    setBusy(true)
    try {
      const next = await studioRequest<Run>(`/api/short-drama/studio/projects/${project.document.project.id}/workflows`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() || '高级处理', workflow: graph, bindings: inputs.filter(item => bindings[item.key]).map(item => ({ node_id: item.id, input_name: item.field, asset_version_id: bindings[item.key] })) }) })
      setRun(next)
    } catch (error) { onError(error) } finally { setBusy(false) }
  }
  const previous = (project.document.comfy_workflows ?? []) as Run[]
  return <section className="sd-workflow-panel"><div className="sd-section-label"><h3>项目工作流</h3><button className="sd-button" onClick={() => input.current?.click()}><Upload size={14} />导入工作流</button></div><p className="sd-hint">从 ComfyUI 导出 API 格式 JSON。为需要处理的输入选择项目素材，输出会自动加入资产库。</p><input hidden ref={input} type="file" accept=".json,application/json" onChange={event => { const file = event.target.files?.[0]; if (!file) return; void file.text().then(text => { const value = JSON.parse(text) as Graph; if (!Object.keys(value).length || Object.values(value).some(node => !node.class_type || !node.inputs)) throw new Error('请在 ComfyUI 导出 API 格式工作流，再导入。'); setGraph(value); setName(file.name.replace(/\.json$/i, '')); setBindings({}); setRun(null) }).catch(onError); event.target.value = '' }} />
    {graph && <div className="sd-workflow-draft"><label className="sd-field">工作流名称<input value={name} onChange={event => setName(event.target.value)} /></label><p className="sd-hint"><FileJson size={14} /> {Object.keys(graph).length} 个处理节点</p>{inputs.map(item => <label className="sd-field" key={item.key}>{item.label} · {item.field}<select aria-label={`${item.label} ${item.field} 素材`} value={bindings[item.key] ?? ''} onChange={event => setBindings(previous => ({ ...previous, [item.key]: event.target.value }))}><option value="">保留工作流输入：{String(item.value).slice(0, 60)}</option>{files.map(file => <option key={file.version.id} value={file.version.id}>{file.name} · {file.version.descriptor || file.version.media_type}</option>)}</select></label>)}<button className="sd-button sd-primary" disabled={!connected || busy || Boolean(run && runningStatuses.has(run.status))} onClick={() => void start()}>{busy ? <Loader2 size={14} className="sd-spin" /> : <Play size={14} />}执行工作流</button></div>}
    {run && <p className="sd-workflow-status" role="status">{run.status === 'completed' ? <Check size={14} /> : runningStatuses.has(run.status) ? <Loader2 size={14} className="sd-spin" /> : null}{run.status === 'completed' ? `已完成，${run.output_asset_ids.length} 个输出已加入项目资产` : run.error || '正在执行，离开设置后任务会继续。'}</p>}
    {!graph && !previous.length && <p className="sd-hint">还没有工作流。日常剧本、镜头和剪辑可以直接在工作台完成。</p>}
    {previous.filter(item => item.id !== run?.id).slice(-4).reverse().map(item => <div className="sd-workflow-history" key={item.id}><strong>{item.name}</strong><span>{item.status === 'completed' ? `已完成 · ${item.output_asset_ids.length} 个输出` : item.error || '制作中'}</span></div>)}
  </section>
}
