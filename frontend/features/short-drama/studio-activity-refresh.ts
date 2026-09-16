export interface StudioActivityChannel {
  id: string | null
  requested: boolean
  pending: Promise<void> | null
}

export const createStudioActivityChannel = (id: string | null): StudioActivityChannel => ({ id, requested: false, pending: null })

/** Coalesce within one opened episode; an older episode never blocks or writes into a new one. */
export function refreshStudioActivity<T>(channel: StudioActivityChannel, options: {
  isCurrent: () => boolean
  read: (id: string) => Promise<T>
  apply: (value: T) => void
  connection: (connected: boolean) => void
}): Promise<void> {
  if (!channel.id) return Promise.resolve()
  channel.requested = true
  if (channel.pending) return channel.pending
  const run = async () => {
    while (channel.requested && options.isCurrent()) {
      channel.requested = false
      try {
        const value = await options.read(channel.id!)
        if (!options.isCurrent()) return
        options.apply(value)
        options.connection(true)
      } catch (error) {
        if (!options.isCurrent()) return
        options.connection(false)
        throw error
      }
    }
  }
  channel.pending = run().finally(() => { channel.pending = null })
  return channel.pending
}

const PROJECT_EVENTS = new Set(['content.updated', 'assets.updated', 'workflow.updated', 'change.proposed', 'change.decided'])
export const studioEventsRefreshProject = (events: readonly { kind: string }[]) => events.some(event => PROJECT_EVENTS.has(event.kind))
