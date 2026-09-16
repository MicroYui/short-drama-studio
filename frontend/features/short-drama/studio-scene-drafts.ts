export interface SceneDraft {
  editId: string
  heading: string
  text: string
  baseHeading: string
  base: string
}

export type SceneDrafts = Record<string, Record<string, SceneDraft>>
export type SceneDraftUpdate = SceneDraft | null | ((current: SceneDraft | null) => SceneDraft | null)

const storageKey = 'short-drama:scene-drafts'
type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

export function sceneDraftChanged(draft: SceneDraft): boolean {
  return draft.heading !== draft.baseHeading || draft.text !== draft.base
}

export function updateSceneDraft(drafts: SceneDrafts, episodeId: string, sceneId: string, update: SceneDraftUpdate): SceneDrafts {
  const current = drafts[episodeId]?.[sceneId] ?? null
  const next = typeof update === 'function' ? update(current) : update
  if (next === current) return drafts
  const episode = { ...drafts[episodeId] }
  if (next) episode[sceneId] = next
  else delete episode[sceneId]
  const result = { ...drafts }
  if (Object.keys(episode).length) result[episodeId] = episode
  else delete result[episodeId]
  return result
}

export function acknowledgeSceneSave(current: SceneDraft | null, submitted: SceneDraft, saved: { heading: string; screenplay_text: string }): SceneDraft | null {
  // A cancelled editor, or an editor opened after it, does not belong to this request.
  if (!current || current.editId !== submitted.editId) return current
  // The user can explicitly review newer remote content during this request.
  // Keep that new comparison baseline instead of replacing it with an old ack.
  if (current.base !== submitted.base || current.baseHeading !== submitted.baseHeading) return current
  const next = {
    ...current,
    heading: current.heading === submitted.heading ? saved.heading : current.heading,
    text: current.text === submitted.text ? saved.screenplay_text : current.text,
    baseHeading: saved.heading,
    base: saved.screenplay_text,
  }
  return sceneDraftChanged(next) ? next : null
}

export function loadSceneDrafts(storage?: DraftStorage): SceneDrafts {
  try {
    // A tab/session cache cannot recover after a normal app exit. Read it only
    // as a migration fallback; all new snapshots live across app restarts.
    const saved = storage ? storage.getItem(storageKey) : localStorage.getItem(storageKey) ?? sessionStorage.getItem(storageKey)
    const raw: unknown = JSON.parse(saved ?? '{}')
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
    const result: SceneDrafts = {}
    for (const [episodeId, scenes] of Object.entries(raw)) {
      if (!scenes || typeof scenes !== 'object' || Array.isArray(scenes)) continue
      const entries = Object.entries(scenes).filter((entry): entry is [string, SceneDraft] => {
        const value: unknown = entry[1]
        if (!value || typeof value !== 'object') return false
        const draft = value as Partial<SceneDraft>
        return ['editId', 'heading', 'text', 'baseHeading', 'base'].every(key => typeof draft[key as keyof SceneDraft] === 'string')
      })
      if (entries.length) Object.defineProperty(result, episodeId, { value: Object.fromEntries(entries), enumerable: true, configurable: true, writable: true })
    }
    return result
  } catch {
    return {}
  }
}

export function persistSceneDrafts(drafts: SceneDrafts, storage?: DraftStorage): boolean {
  try {
    const target = storage ?? localStorage
    if (Object.keys(drafts).length) target.setItem(storageKey, JSON.stringify(drafts))
    else target.removeItem(storageKey)
    if (!storage) sessionStorage.removeItem(storageKey)
    return true
  } catch {
    // Storage can be unavailable or full; the live editor still owns its draft.
    return false
  }
}
