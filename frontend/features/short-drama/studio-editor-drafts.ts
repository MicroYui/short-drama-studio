import { projectSchema, type Project } from '../../types/project-model'

export interface EditorDraftScope {
  projectId: string
  episodeId: string
  shotId?: string
  timelineId?: string
  anchorClipId?: string | null
}

export interface EditorDraft {
  version: 1
  revision: number
  updatedAt: number
  base: Project
  current: Project
}

interface DraftSlot { key: string; owner: string; draft: EditorDraft | null }

export function editorDraftKey(scope: EditorDraftScope): string {
  return JSON.stringify(scope.shotId
    ? [scope.projectId, scope.episodeId, 'SHOT_LOCAL', scope.timelineId, scope.shotId, scope.anchorClipId ?? 'unadopted']
    : [scope.projectId, scope.episodeId, 'FILM'])
}

export function parseEditorDraft(value: unknown): EditorDraft | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'object') throw new Error('本机剪辑草稿格式无法读取；已保留原始记录。')
  const row = value as Partial<EditorDraft>
  if (row.version !== 1 || !Number.isSafeInteger(row.revision) || row.revision! < 0 || !Number.isFinite(row.updatedAt)) {
    throw new Error('本机剪辑草稿格式无法读取；已保留原始记录。')
  }
  return { version: 1, revision: row.revision!, updatedAt: row.updatedAt!, base: projectSchema.parse(row.base), current: projectSchema.parse(row.current) }
}

export function requireExistingDraftTimelines(draft: EditorDraft, latest: Project): void {
  const removed = draft.current.timelines.filter(timeline => {
    const baseline = draft.base.timelines.find(item => item.id === timeline.id)
    return baseline && !latest.timelines.some(item => item.id === timeline.id)
      && (timeline.id === draft.current.activeTimelineId || JSON.stringify(baseline) !== JSON.stringify(timeline))
  })
  if (removed.length) throw new Error(`草稿对应的时间线已被移除（${removed.map(item => item.name).join('、')}）。请保留本地副本；不会自动改到另一条时间线或恢复已删除内容。`)
}

let database: Promise<IDBDatabase> | null = null
function openDatabase(): Promise<IDBDatabase> {
  if (!database) database = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('short-drama-editor-drafts', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('drafts', { keyPath: 'key' })
    request.onsuccess = () => { request.result.onversionchange = () => { request.result.close(); database = null }; resolve(request.result) }
    request.onerror = () => reject(request.error ?? new Error('本机草稿存储未能打开。'))
    request.onblocked = () => reject(new Error('本机草稿存储正在由另一窗口更新，请关闭旧窗口后重试。'))
  }).catch(reason => { database = null; throw reason })
  return database
}

async function transaction<T>(key: string, change: (slot: DraftSlot | undefined, store: IDBObjectStore) => T): Promise<T> {
  const db = await openDatabase()
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction('drafts', 'readwrite')
    const store = tx.objectStore('drafts')
    const request = store.get(key)
    let result: T
    let failure: unknown
    request.onsuccess = () => {
      try { result = change(request.result as DraftSlot | undefined, store) }
      catch (reason) { failure = reason; tx.abort() }
    }
    tx.oncomplete = () => resolve(result)
    tx.onerror = tx.onabort = () => reject(failure ?? tx.error ?? new Error('本机草稿未能写入。'))
  })
}

export interface EditorDraftStorage {
  claim(key: string, owner: string): Promise<EditorDraft | null>
  write(key: string, owner: string, draft: EditorDraft | null): Promise<boolean>
  relocate(key: string, nextKey: string, owner: string): Promise<void>
  release(key: string, owner: string, discard?: boolean): Promise<void>
}

export const editorDraftStorage: EditorDraftStorage = {
  claim: (key, owner) => transaction(key, (slot, store) => {
    const draft = parseEditorDraft(slot?.draft)
    store.put({ key, owner, draft } satisfies DraftSlot)
    return draft
  }),
  write: (key, owner, draft) => transaction(key, (slot, store) => {
    if (slot?.owner !== owner) return false
    store.put({ key, owner, draft } satisfies DraftSlot)
    return true
  }),
  relocate: async (key, nextKey, owner) => {
    if (key === nextKey) return
    const db = await openDatabase()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction('drafts', 'readwrite')
      const store = tx.objectStore('drafts')
      const source = store.get(key), target = store.get(nextKey)
      let failure: unknown
      target.onsuccess = () => {
        const previous = source.result as DraftSlot | undefined
        const existing = target.result as DraftSlot | undefined
        if (previous?.owner !== owner || existing && existing.owner !== owner) {
          failure = new Error('采用位置草稿已由另一窗口接管；原草稿仍保留。')
          tx.abort()
          return
        }
        // Changing recovery identity must not delete the last successful draft
        // if the replacement cannot be committed (for example, storage quota).
        store.delete(key)
        store.put({ ...previous, key: nextKey } satisfies DraftSlot)
      }
      tx.oncomplete = () => resolve()
      tx.onerror = tx.onabort = () => reject(failure ?? tx.error ?? new Error('采用位置草稿未能同步；原草稿仍保留。'))
    })
  },
  release: (key, owner, discard = false) => transaction(key, (slot, store) => {
    if (slot?.owner === owner && (discard || !slot.draft)) store.delete(key)
  }),
}

/** Keep one active write and the most recent pending snapshot, never a keypress history. */
export function createEditorDraftWriter(write: (draft: EditorDraft | null) => Promise<boolean>) {
  let desired: EditorDraft | null | undefined
  let pending: Promise<void> | null = null
  const flush = (): Promise<void> => {
    if (pending) return pending
    if (desired === undefined) return Promise.resolve()
    const run = async () => {
      while (desired !== undefined) {
        const next = desired
        desired = undefined
        if (!await write(next)) throw new Error('剪辑已在另一个窗口打开；当前修改仍在此窗口，请重新打开后合并。')
      }
    }
    pending = run().finally(() => { pending = null })
    return pending
  }
  return { put: (draft: EditorDraft | null) => { desired = draft; return flush() }, flush }
}
