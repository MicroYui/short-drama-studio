import { useState } from 'react'
import { Check, CheckCheck, GitCompareArrows, Loader2, X } from 'lucide-react'
import { StudioApi, type StudioChangeSet } from './studio-api'
import type { ProjectAggregate } from './api'

function changedText(before: string, after: string) {
  let prefix = 0
  let suffix = 0
  while (prefix < Math.min(before.length, after.length) && before[prefix] === after[prefix]) prefix++
  while (suffix < Math.min(before.length, after.length) - prefix && before[before.length - suffix - 1] === after[after.length - suffix - 1]) suffix++
  return { prefix, suffix }
}

function DiffText({ value, prefix, suffix }: { value: string; prefix: number; suffix: number }) {
  return <p>{value.slice(0, prefix)}<mark>{value.slice(prefix, suffix ? -suffix : undefined) || '（删除）'}</mark>{suffix > 0 && value.slice(-suffix)}</p>
}

export function StudioChangeReview({ projectId, changes, onChanged, onClose, onError }: {
  projectId: string; changes: StudioChangeSet[]; onChanged: (project: ProjectAggregate, change: StudioChangeSet) => void;
  onClose: () => void; onError: (error: unknown) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const pending = changes.flatMap(change => change.items).filter(item => item.status === 'pending').length
  const decide = async (change: StudioChangeSet, itemId: string, decision: 'accept' | 'reject') => {
    setBusy(itemId)
    try { const result = await StudioApi.decideChange(projectId, change.id, itemId, decision); onChanged(result.project, result.change) }
    catch (error) { onError(error) }
    finally { setBusy(null) }
  }
  return <div className="sd-modal-backdrop sd-review-backdrop" onClick={onClose}>
    <section className="sd-change-review" role="dialog" aria-modal="true" aria-label="审阅 Agent 修改" onClick={event => event.stopPropagation()}>
      <header><span className="sd-review-symbol"><GitCompareArrows size={22} /></span><div><h2>{pending ? `${pending} 处修改，等你决定。` : '修改已处理。'}</h2><p>{pending ? '逐项对照。接受的内容会立即保存。' : '已接受的内容已保存，Agent 将按原目标继续。'}</p></div><button className="sd-icon" aria-label="关闭修改审阅" onClick={onClose}><X size={20} /></button></header>
      <div className="sd-review-scroll">{changes.map(change => <section className="sd-change-set" key={change.id}>
        <div className="sd-change-set-title"><h3>{change.title}</h3>{change.summary && <p>{change.summary}</p>}</div>
        {change.items.map((item, index) => { const range = changedText(item.before, item.after); return <article className={`sd-change-item sd-change-${item.status}`} key={item.id} aria-label={item.title}>
          <div className="sd-change-item-heading"><span>{String(index + 1).padStart(2, '0')}</span><h4>{item.title}</h4><small>{item.target === 'scene' ? '剧本' : '分镜'}</small></div>
          <div className="sd-diff-columns"><div className="sd-diff-before"><label>修改前</label><DiffText value={item.before} {...range} /></div><div className="sd-diff-after"><label>修改后</label><DiffText value={item.after} {...range} /></div></div>
          <footer><p>{item.explanation}</p><div>{item.status === 'pending' ? <><button className="sd-button" disabled={busy !== null} onClick={() => void decide(change, item.id, 'reject')} aria-label={`保留原文：${item.title}`}>保留原文</button><button className="sd-button sd-primary" disabled={busy !== null} onClick={() => void decide(change, item.id, 'accept')} aria-label={`接受修改：${item.title}`}>{busy === item.id ? <Loader2 size={15} className="sd-spin" /> : <Check size={15} />}接受修改</button></> : <span className="sd-review-decision">{item.status === 'accepted' ? <><CheckCheck size={16} />已接受并保存</> : <><X size={16} />已保留原文</>}</span>}</div></footer>
        </article>})}
      </section>)}</div>
      <footer className="sd-review-footer"><span>{pending ? '未接受的修改不会改变当前作品。' : '你的选择已同步。'}</span><button className="sd-button" onClick={onClose}>{pending ? '稍后继续审阅' : '回到作品'}</button></footer>
    </section>
  </div>
}
