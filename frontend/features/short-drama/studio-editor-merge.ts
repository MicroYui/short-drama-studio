import type { Project } from '../../types/project-model'

/** Reapply the user's changed fields on the latest document after a save conflict. */
export function mergeCurrentChanges<T>(base: T, local: T, current: T): T {
  const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right)
  const merge = (before: unknown, mine: unknown, latest: unknown): unknown => {
    if (same(before, mine)) return latest
    if (Array.isArray(before) && Array.isArray(mine) && Array.isArray(latest)) {
      const identified = [...before, ...mine, ...latest].every(item => item && typeof item === 'object' && typeof item.id === 'string')
      if (identified) {
        const baseMap = new Map(before.map(item => [item.id, item]))
        const localMap = new Map(mine.map(item => [item.id, item]))
        const remoteMap = new Map(latest.map(item => [item.id, item]))
        const order = same(before.map(item => item.id), mine.map(item => item.id)) ? [...latest, ...mine] : [...mine, ...latest]
        return [...new Set(order.map(item => item.id))].flatMap(id => {
          if (baseMap.has(id) && !localMap.has(id)) return []
          if (!localMap.has(id)) return [remoteMap.get(id)]
          if (!remoteMap.has(id) && same(baseMap.get(id), localMap.get(id))) return []
          return [merge(baseMap.get(id), localMap.get(id), remoteMap.get(id))]
        })
      }
      if (before.length === mine.length && latest.length === mine.length) return mine.map((item, index) => merge(before[index], item, latest[index]))
      return mine
    }
    if (before && mine && latest && typeof before === 'object' && typeof mine === 'object' && typeof latest === 'object') {
      const a = before as Record<string, unknown>, b = mine as Record<string, unknown>, c = latest as Record<string, unknown>
      return Object.fromEntries([...new Set([...Object.keys(b), ...Object.keys(c)])].filter(key => !(key in a) || key in b).map(key => [key, key in b ? merge(a[key], b[key], c[key]) : c[key]]))
    }
    return mine
  }
  return merge(base, local, current) as T
}

export const mergeEditorChanges = (base: Project, local: Project, current: Project) => mergeCurrentChanges(base, local, current)
