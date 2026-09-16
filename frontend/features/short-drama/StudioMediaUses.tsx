import { useEffect, useState } from 'react'
import { studioRequest } from './studio-api'
import { mediaUseLocation, type StudioMediaLocation, type StudioMediaUse } from './studio-media-location'

export function StudioMediaUses({ projectId, assetId, versionId, path, reviewToken, onLocate }: {
  projectId: string; assetId?: string; versionId?: string; path?: string | null; reviewToken?: string
  onLocate?: (location: StudioMediaLocation) => void
}) {
  const [uses, setUses] = useState<StudioMediaUse[] | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let active = true
    setUses(null); setFailed(false)
    const query = new URLSearchParams(versionId ? { asset_version_id: versionId } : path ? { path } : assetId ? { asset_id: assetId } : {})
    if (!query.size) { setUses([]); return }
    void studioRequest<{ uses: StudioMediaUse[] }>(`/api/short-drama/studio/projects/${encodeURIComponent(projectId)}/media/usage?${query}`)
      .then(result => { if (active) setUses(result.uses) }).catch(() => { if (active) setFailed(true) })
    return () => { active = false }
  }, [projectId, assetId, versionId, path, reviewToken])
  return <section className="sd-asset-usage" aria-label="全项目使用位置">
    <h3>全项目使用位置</h3>
    {uses?.map((use, index) => {
      const key = `${use.episode_id}:${use.kind}:${use.target_id}:${index}`
      const episodeLabel = `第 ${use.episode_number} 集`
      const location = mediaUseLocation(use)
      const content = <><span>{use.episode_title === episodeLabel ? episodeLabel : `${episodeLabel} · ${use.episode_title}`}</span><p>{use.label}{use.start_seconds != null && use.end_seconds != null ? ` · ${use.start_seconds.toFixed(2)}–${use.end_seconds.toFixed(2)} 秒` : ''}</p></>
      return !location || !onLocate ? <div key={key}>{content}</div>
        : <button type="button" key={key} onClick={() => onLocate(location)}>{content}<small className="sd-media-use-location">{use.kind === 'editorial' ? '定位到剪辑' : '定位到镜头'} ↗</small></button>
    })}
    {failed ? <p className="sd-hint">暂时无法读取跨集使用位置，请重新打开详情重试。</p> : uses === null ? <p className="sd-hint">正在读取各集引用…</p> : !uses.length ? <p className="sd-hint">当前各集没有引用这份素材。</p> : <p className="sd-hint">共涉及 {new Set(uses.map(use => use.episode_id)).size} 集。重新定位同一文件会同步这些位置。</p>}
  </section>
}
