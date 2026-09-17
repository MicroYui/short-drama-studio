import type { StudioWorker } from './studio-api'

/** A historical poll deadline alone does not mean this worker still owns a media wait. */
export function isWorkerWaitingForGeneration(
  worker: Pick<StudioWorker, 'status' | 'next_owner' | 'waiting_until' | 'waiting_review'>,
): boolean {
  return worker.status === 'NEEDS_ATTENTION'
    && worker.next_owner === 'WORKER_TOOL'
    && Boolean(worker.waiting_until)
    && !worker.waiting_review
}
