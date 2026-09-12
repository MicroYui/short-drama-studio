import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { BookOpen, Loader2, X } from 'lucide-react'
import { StudioApi, type StudioMethod } from './studio-api'

const roles: Record<string, string> = { CONCEPT: '故事策划', WRITER: '编剧', PLANNER: '制作规划', DIRECTOR: '导演', ASSET_PLANNER: '美术设计', ASSET_BUILDER: '资产制作', ASSET_REVIEWER: '美术', ASSET_VERIFIER: '素材检查', REVISION_VERIFIER: '修订检查', VIDEO_PROMPT_COMPILER: '影像制作', EPISODE_REVIEWER: '成片检查', ROUTE_PLANNER: '生成调度' }
const stages: Record<string, string> = { STORY: '剧本', STORYBOARD: '分镜', ASSETS: '资产', VISUAL_GEN: '视频制作', POST: '剪辑与交付', ORCHESTRATION: '整体安排' }
export const methodCategoryLabels: Record<string, string> = { creative: '创作', analysis: '视频拆解', image: '图片', audio: '声音', software: '软件工作流', maintenance: '方法维护' }

function Knowledge({ text }: { text: string }) {
  const inline = (value: string) => value.split(/(\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|`[^`]+`)/).map((part, index) => {
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (link) return /^https?:\/\//.test(link[2]) ? <a key={index} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a> : <span key={index} title={`来源资料：${link[2]}`}>{link[1]}</span>
    return part.startsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part.startsWith('`') ? <code key={index}>{part.slice(1, -1)}</code> : part
  })
  const lines = text.split('\n'), blocks: ReactNode[] = []
  const cells = (line: string) => line.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim())
  for (let index = 0; index < lines.length;) {
    const line = lines[index].trim(), key = index
    if (!line || /^[-*_]{3,}$/.test(line)) { index++; continue }
    if (/^#{1,6}\s/.test(line)) { blocks.push(<h4 key={key}>{inline(line.replace(/^#{1,6}\s+/, ''))}</h4>); index++; continue }
    if (line.startsWith('|') && /^\|?\s*:?-{3,}/.test(lines[index + 1]?.trim() || '')) {
      const headings = cells(line), rows: string[][] = []
      index += 2
      while (index < lines.length && lines[index].trim().startsWith('|')) rows.push(cells(lines[index++]))
      blocks.push(<div className="sd-method-table" key={key}><table><thead><tr>{headings.map((cell, col) => <th key={col}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, number) => <tr key={number}>{headings.map((_, col) => <td key={col}>{inline(row[col] || '')}</td>)}</tr>)}</tbody></table></div>); continue
    }
    const listPattern = /^[-*]\s+/.test(line) ? /^[-*]\s+/ : /^\d+[.)]\s+/.test(line) ? /^\d+[.)]\s+/ : null
    if (listPattern) {
      const items: ReactNode[] = [], ordered = /^\d/.test(line)
      while (index < lines.length && listPattern.test(lines[index].trim())) items.push(<li key={index}>{inline(lines[index++].trim().replace(listPattern, ''))}</li>)
      blocks.push(ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>); continue
    }
    const paragraph = [lines[index++]]
    while (index < lines.length && lines[index].trim() && !/^(?:#{1,6}\s|[-*]\s|\d+[.)]\s|\|)/.test(lines[index].trim())) paragraph.push(lines[index++])
    blocks.push(<p key={key}>{inline(paragraph.join('\n'))}</p>)
  }
  return <div className="sd-method-knowledge">{blocks}</div>
}

export function StudioMethodDetail({ id, selected, role, stage, onClose }: { id: string; selected: boolean; role?: string | null; stage?: string | null; onClose: () => void }) {
  const panel = useRef<HTMLElement>(null)
  const [method, setMethod] = useState<StudioMethod | null>(null)
  const [section, setSection] = useState(0)
  const [error, setError] = useState('')
  useEffect(() => {
    let current = true
    setMethod(null); setError('')
    void StudioApi.method(id).then(value => {
      if (!current) return
      setMethod(value)
      const specific = value.sections.findIndex(item => item.role === role && item.stage === stage)
      const general = value.sections.findIndex(item => item.role === role && !item.stage)
      setSection(role ? Math.max(0, specific >= 0 ? specific : general) : -1)
    }).catch(reason => { if (current) setError(reason instanceof Error ? reason.message : '方法暂未读取成功') })
    return () => { current = false }
  }, [id, role, stage])
  useEffect(() => { if (method) panel.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [method])
  return <section ref={panel} className="sd-method-detail" aria-label="创作方法详情"><header><BookOpen size={16} /><strong>{method?.label ?? '读取方法'}</strong><button className="sd-icon" aria-label="关闭方法详情" onClick={onClose}><X size={15} /></button></header>
    {error ? <p role="alert">{error}</p> : !method ? <Loader2 size={16} className="sd-spin" /> : <>
      <span className="sd-method-selection">{methodCategoryLabels[method.category ?? 'creative']} · {selected ? '当前已选用' : '可按作品目标选用'}</span><p>{method.description}</p>
      {Boolean(method.requirements?.length) && <aside className="sd-method-requirements"><strong>执行条件</strong><ul>{method.requirements?.map(requirement => <li key={requirement}>{requirement}</li>)}</ul></aside>}
      <p className="sd-hint">按当前职责与制作阶段加载以下知识。你的具体创作要求可以覆盖方法建议。</p>
      <label className="sd-field">查看专业方法<select aria-label="查看专业方法" value={section} onChange={event => setSection(Number(event.target.value))}><option value={-1}>方法概览</option>{method.sections.map((item, index) => <option key={`${item.stage}:${item.role}`} value={index}>{roles[item.role] || item.role}{item.stage ? ` · ${stages[item.stage] || item.stage}` : ' · 通用方法'}</option>)}</select></label>
      {section === -1 ? <Knowledge text={method.principles} /> : method.sections[section] && <Knowledge text={method.sections[section].text} />}
    </>}
  </section>
}
