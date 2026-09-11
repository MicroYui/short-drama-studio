import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ImagePlus, Search, X } from 'lucide-react'
import type { ProjectAggregate, AssetVersion } from './api'
import type { ReferenceCue } from './studio-api'
import { assetGroups, mediaUrl, mediaTitle, resolveCue, type AssetGroup } from './studio-content'

export function ReferencePreview({ project, cue, rect, scope }: { project: ProjectAggregate; cue: ReferenceCue; rect: DOMRect; scope?: string }) {
  const { version, name } = resolveCue(project, cue)
  const left = Math.max(12, Math.min(rect.left - 12, window.innerWidth - 320))
  const below = rect.bottom + 10
  const top = below + 292 < window.innerHeight ? below : Math.max(12, rect.top - 294)
  return createPortal(<div className="sd-reference-preview" role="tooltip" style={{ left, top }}>
    <div className="sd-reference-picture">{version ? <img src={mediaUrl(version.local_path, version.external_url)} alt={`${name}实际绑定的图片`} /> : <span>尚未绑定图片</span>}</div>
    <div className="sd-reference-caption"><strong>{name}</strong><span>{scope || '整镜参考'}{cue.usage === 'EDITORIAL' ? ' · 剪辑中使用' : ' · 视频生成参考'}</span><p>{cue.purpose}</p><small>{version ? mediaTitle(project, version) : '待补充素材'}</small></div>
  </div>, document.body)
}

export function ReferenceChip({ project, cue, scope, onClick }: { project: ProjectAggregate; cue: ReferenceCue; scope?: string; onClick?: () => void }) {
  const [rect, setRect] = useState<DOMRect | null>(null)
  const { name, version } = resolveCue(project, cue)
  return <><button className="sd-reference-chip" onMouseEnter={e => setRect(e.currentTarget.getBoundingClientRect())} onMouseLeave={() => setRect(null)} onFocus={e => setRect(e.currentTarget.getBoundingClientRect())} onBlur={() => setRect(null)} onClick={onClick} aria-label={`${name}，${cue.purpose}，${onClick ? '预览和更换图片' : '预览图片'}`}>
    {version && <img src={mediaUrl(version.local_path, version.external_url)} alt="" />}<span>@{name}</span>
  </button>{rect && <ReferencePreview project={project} cue={cue} rect={rect} scope={scope} />}</>
}

export function AssetPicker({ project, query = '', onSelect, onClose }: { project: ProjectAggregate; query?: string; onSelect: (group: AssetGroup, version: AssetVersion) => void; onClose: () => void }) {
  const [search, setSearch] = useState(query)
  useEffect(() => setSearch(query), [query])
  const groups = assetGroups(project).filter(group => `${group.name} ${group.files.map(file => file.version.descriptor).join(' ')}`.toLowerCase().includes(search.toLowerCase()))
  return <div className="sd-asset-picker" role="dialog" aria-label="选择具体参考图片">
    <div className="sd-picker-search"><Search size={15} /><input aria-label="搜索参考图片" placeholder="搜索角色、场景或图片…" value={search} onChange={e => setSearch(e.target.value)} /><button className="sd-icon" aria-label="关闭图片选择" onClick={onClose}><X size={16} /></button></div>
    <div className="sd-picker-results">{groups.map(group => <div key={group.id} className="sd-picker-group"><h4>{group.name}</h4><div>{group.files.filter(file => file.version.media_type === 'IMAGE').map(({ version }) => <button key={version.id} onMouseDown={e => e.preventDefault()} onClick={() => onSelect(group, version)}>
      <img src={mediaUrl(version.local_path, version.external_url)} alt={mediaTitle(project, version)} loading="lazy" /><span>{mediaTitle(project, version)}</span>
    </button>)}</div></div>)}{groups.every(group => !group.files.some(file => file.version.media_type === 'IMAGE')) && <p className="sd-hint">还没有匹配的图片。先从项目资产导入，或告诉 Agent 生成所需素材。</p>}</div>
  </div>
}

function buildContent(root: HTMLElement, text: string, cues: ReferenceCue[]) {
  const chars = Array.from(text)
  const fragment = document.createDocumentFragment()
  let start = 0
  for (const cue of [...cues].filter(item => item.text_start != null && item.text_end != null).sort((a, b) => (a.text_start ?? 0) - (b.text_start ?? 0))) {
    const from = cue.text_start ?? 0
    const to = cue.text_end ?? 0
    if (from < start || to > chars.length) continue
    fragment.append(document.createTextNode(chars.slice(start, from).join('')))
    const token = document.createElement('span')
    token.contentEditable = 'false'
    token.className = 'sd-inline-reference'
    token.dataset.referenceId = cue.id ?? ''
    token.tabIndex = 0
    token.setAttribute('role', 'button')
    token.setAttribute('aria-label', `图片引用 ${cue.alias}`)
    token.textContent = chars.slice(from, to).join('')
    fragment.append(token)
    start = to
  }
  fragment.append(document.createTextNode(chars.slice(start).join('')))
  root.replaceChildren(fragment)
}

function readContent(root: HTMLElement, lookup: Map<string, ReferenceCue>) {
  let text = ''
  const cues: ReferenceCue[] = []
  const visit = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) { text += node.textContent ?? ''; return }
    if (!(node instanceof HTMLElement)) return
    if (node.tagName === 'BR') { text += '\n'; return }
    const binding = node.dataset.referenceId ? lookup.get(node.dataset.referenceId) : undefined
    if (binding) {
      const start = Array.from(text).length
      text += node.textContent ?? ''
      cues.push({ ...binding, text_start: start, text_end: Array.from(text).length })
      return
    }
    const block = node !== root && ['DIV', 'P'].includes(node.tagName)
    if (block && text && !text.endsWith('\n')) text += '\n'
    node.childNodes.forEach(visit)
  }
  root.childNodes.forEach(visit)
  return { text, cues }
}

export function ReferenceUsagePicker({ value, onChange }: { value: NonNullable<ReferenceCue['usage']>; onChange: (value: NonNullable<ReferenceCue['usage']>) => void }) {
  return <label className="sd-field">使用位置<select aria-label="素材使用位置" value={value} onChange={event => onChange(event.target.value === 'EDITORIAL' ? 'EDITORIAL' : 'GENERATION')}><option value="GENERATION">视频生成参考</option><option value="EDITORIAL">剪辑中使用</option></select></label>
}

export function ShotReferenceEditor({ project, text, cues, field, label, scope, onChange, readOnly = false }: {
  project: ProjectAggregate; text: string; cues: ReferenceCue[]; field: NonNullable<ReferenceCue['field']>; label: string; scope: string;
  readOnly?: boolean;
  onChange: (text: string, cues: ReferenceCue[]) => void;
}) {
  const editor = useRef<HTMLDivElement>(null)
  const lookup = useRef(new Map<string, ReferenceCue>())
  const previous = useRef('')
  const rangeRef = useRef<Range | null>(null)
  const [picker, setPicker] = useState<{ query: string; cue?: ReferenceCue } | null>(null)
  const [hover, setHover] = useState<{ cue: ReferenceCue; rect: DOMRect } | null>(null)
  const [purpose, setPurpose] = useState('')
  const [usage, setUsage] = useState<NonNullable<ReferenceCue['usage']>>('GENERATION')

  useLayoutEffect(() => {
    const normalized = cues.map((cue, index) => ({ ...cue, id: cue.id ?? `legacy-${field}-${index}` }))
    lookup.current = new Map(normalized.map(cue => [cue.id, cue]))
    const signature = JSON.stringify({ text, cues: normalized })
    if (editor.current && previous.current !== signature) buildContent(editor.current, text, normalized)
    previous.current = signature
  }, [text, cues, field])

  const publish = () => {
    if (!editor.current) return
    const value = readContent(editor.current, lookup.current)
    previous.current = JSON.stringify(value)
    onChange(value.text, value.cues)
  }

  const handleInput = () => {
    publish()
    const selection = window.getSelection()
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null
    if (!range || range.startContainer.nodeType !== Node.TEXT_NODE) return
    const before = (range.startContainer.textContent ?? '').slice(0, range.startOffset)
    const match = before.match(/@([^\s@]{0,40})$/u)
    if (!match) { setPicker(null); return }
    const trigger = range.cloneRange()
    trigger.setStart(range.startContainer, before.lastIndexOf('@'))
    rangeRef.current = trigger
    setPicker({ query: match[1] })
    setPurpose('')
    setUsage('GENERATION')
  }

  const editToken = (element: HTMLElement) => {
    const cue = lookup.current.get(element.dataset.referenceId ?? '')
    if (!cue) return
    if (readOnly) { setHover({ cue, rect: element.getBoundingClientRect() }); return }
    const range = document.createRange()
    range.selectNode(element)
    rangeRef.current = range
    setHover(null)
    setPicker({ query: '', cue })
    setPurpose(cue.purpose)
    setUsage(cue.usage ?? 'GENERATION')
  }

  const select = (group: AssetGroup, version: AssetVersion) => {
    const range = rangeRef.current
    if (!range || !editor.current) return
    const cue: ReferenceCue = {
      id: picker?.cue?.id ?? crypto.randomUUID(), subject_asset_id: group.id, asset_version_id: version.id,
      alias: `${group.name.replace(/[^\p{L}\p{N}_-]/gu, '_')}_${version.id.slice(-5)}`,
      purpose: purpose.trim() || ({ CHARACTER: '人物身份与外观', LOCATION: '场景与空间关系', PROP: '道具外观与使用', REFERENCE: '画面参考' }[group.kind]),
      required_tags: [], field, text_start: 0, text_end: 1, usage,
    }
    lookup.current.set(cue.id!, cue)
    const span = document.createElement('span')
    span.contentEditable = 'false'
    span.className = 'sd-inline-reference'
    span.dataset.referenceId = cue.id!
    span.tabIndex = 0
    span.setAttribute('role', 'button')
    span.setAttribute('aria-label', `图片引用 ${group.name}`)
    span.textContent = `@${group.name}`
    range.deleteContents()
    range.insertNode(span)
    const tail = document.createTextNode(' ')
    span.after(tail)
    range.setStartAfter(tail)
    range.collapse(true)
    window.getSelection()?.removeAllRanges()
    window.getSelection()?.addRange(range)
    setPicker(null)
    editor.current.focus()
    publish()
  }

  const remove = () => {
    const range = rangeRef.current
    if (!range) return
    const text = range.toString().replace(/^@/, '')
    range.deleteContents()
    range.insertNode(document.createTextNode(text))
    setPicker(null)
    publish()
  }

  useEffect(() => {
    if (!picker) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setPicker(null) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [picker])

  return <div className={`sd-rich-field ${readOnly ? 'sd-read-field' : ''}`}>
    <label>{label}</label>
    <div ref={editor} className="sd-rich-text" role="textbox" aria-readonly={readOnly} aria-label={label} aria-multiline="true" contentEditable={!readOnly} suppressContentEditableWarning data-placeholder={readOnly ? '尚未填写' : '描述画面；输入 @ 选择参考图片'}
      onInput={readOnly ? undefined : handleInput} onPaste={readOnly ? undefined : event => { event.preventDefault(); document.execCommand('insertText', false, event.clipboardData.getData('text/plain')); }}
      onClick={event => { const element = (event.target as HTMLElement).closest<HTMLElement>('[data-reference-id]'); if (element) editToken(element) }}
      onKeyDown={event => { if (event.key === 'Enter' && (event.target as HTMLElement).dataset.referenceId) { event.preventDefault(); editToken(event.target as HTMLElement) } }}
      onMouseOver={event => { const element = (event.target as HTMLElement).closest<HTMLElement>('[data-reference-id]'); const cue = lookup.current.get(element?.dataset.referenceId ?? ''); if (element && cue) setHover({ cue, rect: element.getBoundingClientRect() }) }}
      onFocus={event => { const element = (event.target as HTMLElement).closest<HTMLElement>('[data-reference-id]'); const cue = lookup.current.get(element?.dataset.referenceId ?? ''); if (element && cue) setHover({ cue, rect: element.getBoundingClientRect() }) }}
      onMouseOut={() => setHover(null)} onBlur={() => setHover(null)} />
    {picker && <div className="sd-reference-editor-popover"><div className="sd-reference-purpose"><ImagePlus size={15} /><input aria-label="图片用途" placeholder="图片用途，例如：保持人物外观" value={purpose} onChange={e => setPurpose(e.target.value)} />{picker.cue && <button className="sd-text-button" onClick={remove}>解除引用</button>}</div>
      <ReferenceUsagePicker value={usage} onChange={setUsage} />
      <AssetPicker project={project} query={picker.query} onSelect={select} onClose={() => setPicker(null)} />
      {picker.cue && <button className="sd-button sd-purpose-save" onClick={() => { const cue = { ...picker.cue!, purpose: purpose.trim() || picker.cue!.purpose, usage }; lookup.current.set(cue.id!, cue); publish(); setPicker(null) }}><Check size={14} />仅更新用途</button>}
    </div>}
    {hover && !picker && <ReferencePreview project={project} cue={hover.cue} rect={hover.rect} scope={scope} />}
  </div>
}
