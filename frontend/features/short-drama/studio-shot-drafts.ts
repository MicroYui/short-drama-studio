import type { ShotPlan } from './api'
import { mergeCurrentChanges } from './studio-editor-merge'

export interface ShotDraft { editId: string; shot: ShotPlan; base: ShotPlan; revision: number }
export type ShotDrafts = Record<string, Record<string, ShotDraft>>
export type ShotDraftUpdate = ShotDraft | null | ((current: ShotDraft | null) => ShotDraft | null)
type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
const storageKey = 'short-drama:shot-drafts'

export function updateShotDraft(drafts: ShotDrafts, episodeId: string, shotId: string, update: ShotDraftUpdate): ShotDrafts {
  const current = drafts[episodeId]?.[shotId] ?? null
  const next = typeof update === 'function' ? update(current) : update
  if (next === current) return drafts
  const episode = { ...drafts[episodeId] }
  if (next) episode[shotId] = next
  else delete episode[shotId]
  const result = { ...drafts }
  if (Object.keys(episode).length) result[episodeId] = episode
  else delete result[episodeId]
  return result
}

export function acknowledgeShotSave(current: ShotDraft | null, submitted: ShotDraft, saved: ShotPlan): ShotDraft | null {
  if (!current || current.editId !== submitted.editId) return current
  // Another confirmation or an explicit merge may already have advanced the
  // baseline while this response was in flight. Equal revisions are stale only
  // when that advancement happened after this request's submitted baseline.
  if (saved.revision < current.revision || (saved.revision === current.revision && current.revision > submitted.revision)) return current
  if (JSON.stringify(current.shot) === JSON.stringify(submitted.shot)) return null
  const shot = mergeCurrentChanges(submitted.shot, current.shot, saved)
  return { ...current, shot, base: structuredClone(saved), revision: saved.revision }
}

export function loadShotDrafts(storage?: DraftStorage): ShotDrafts {
  try {
    const raw: unknown = JSON.parse((storage ?? localStorage).getItem(storageKey) ?? '{}')
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
    const entries = Object.entries(raw).flatMap(([episodeId, shots]) => {
      if (!shots || typeof shots !== 'object' || Array.isArray(shots)) return []
      const valid = Object.entries(shots).filter((entry): entry is [string, ShotDraft] => {
        const value = entry[1] as Partial<ShotDraft> | null
        return !!value && typeof value.editId === 'string' && typeof value.revision === 'number'
          && value.shot?.id === entry[0] && value.base?.id === entry[0]
          && typeof value.shot?.duration_seconds === 'number' && typeof value.base?.duration_seconds === 'number'
      })
      return valid.length ? [[episodeId, Object.fromEntries(valid)]] : []
    })
    return Object.fromEntries(entries) as ShotDrafts
  } catch { return {} }
}

export function persistShotDrafts(drafts: ShotDrafts, storage?: DraftStorage): boolean {
  try {
    const target = storage ?? localStorage
    if (Object.keys(drafts).length) target.setItem(storageKey, JSON.stringify(drafts))
    else target.removeItem(storageKey)
    return true
  } catch { return false }
}
