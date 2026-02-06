/**
 * Request Priority System
 *
 * Manages API request priorities to ensure timeline thumbnails load first,
 * followed by other lower-priority requests like waveforms and asset library thumbnails.
 *
 * Priority levels:
 * - HIGH (0): Timeline clip thumbnails - should load immediately
 * - MEDIUM (1): Timeline waveforms - can be slightly delayed
 * - LOW (2): Asset library thumbnails/waveforms - can be delayed more
 */

export enum RequestPriority {
  HIGH = 0,    // Timeline thumbnails
  MEDIUM = 1,  // Timeline waveforms
  LOW = 2,     // Asset library content
}

// Delays in milliseconds for each priority level
const PRIORITY_DELAYS: Record<RequestPriority, number> = {
  [RequestPriority.HIGH]: 0,      // No delay for timeline thumbnails
  [RequestPriority.MEDIUM]: 300,  // 300ms delay for timeline waveforms
  [RequestPriority.LOW]: 500,     // 500ms delay for asset library content
}

// Track if high priority requests are in progress
let highPriorityRequestsInProgress = 0
let highPriorityCompleteCallbacks: (() => void)[] = []

/**
 * Mark that a high priority request has started
 */
export function startHighPriorityRequest(): void {
  highPriorityRequestsInProgress++
}

/**
 * Mark that a high priority request has completed
 */
export function endHighPriorityRequest(): void {
  highPriorityRequestsInProgress = Math.max(0, highPriorityRequestsInProgress - 1)

  if (highPriorityRequestsInProgress === 0) {
    // Notify all waiting callbacks
    const callbacks = highPriorityCompleteCallbacks
    highPriorityCompleteCallbacks = []
    callbacks.forEach(cb => cb())
  }
}

/**
 * Wait for high priority requests to complete (or timeout)
 */
function waitForHighPriorityRequests(timeoutMs: number = 2000): Promise<void> {
  if (highPriorityRequestsInProgress === 0) {
    return Promise.resolve()
  }

  return new Promise(resolve => {
    const timeoutId = setTimeout(() => {
      // Remove callback if timeout
      const index = highPriorityCompleteCallbacks.indexOf(resolve)
      if (index > -1) {
        highPriorityCompleteCallbacks.splice(index, 1)
      }
      resolve()
    }, timeoutMs)

    highPriorityCompleteCallbacks.push(() => {
      clearTimeout(timeoutId)
      resolve()
    })
  })
}

/**
 * Get the delay for a given priority level
 */
export function getPriorityDelay(priority: RequestPriority): number {
  return PRIORITY_DELAYS[priority]
}

/**
 * Execute a function with appropriate delay based on priority
 * Higher priority requests execute immediately, lower priority requests wait
 */
export async function withPriority<T>(
  priority: RequestPriority,
  fn: () => Promise<T>
): Promise<T> {
  const delay = getPriorityDelay(priority)

  // For high priority, execute immediately and track
  if (priority === RequestPriority.HIGH) {
    startHighPriorityRequest()
    try {
      return await fn()
    } finally {
      endHighPriorityRequest()
    }
  }

  // For lower priorities, wait for delay and optionally for high priority to complete
  if (delay > 0) {
    await new Promise(resolve => setTimeout(resolve, delay))
  }

  // Additionally wait for high priority requests to complete (with timeout)
  if (priority > RequestPriority.HIGH) {
    await waitForHighPriorityRequests()
  }

  return fn()
}

/**
 * Create a debounced function with priority-aware delays
 * For HIGH priority: no debounce (immediate execution)
 * For other priorities: base delay + priority delay
 */
export function createPriorityDebounce<T extends (...args: unknown[]) => void>(
  fn: T,
  priority: RequestPriority,
  baseDelay: number = 0
): T {
  let timeoutId: ReturnType<typeof setTimeout> | null = null
  const totalDelay = baseDelay + getPriorityDelay(priority)

  return ((...args: Parameters<T>) => {
    if (timeoutId) {
      clearTimeout(timeoutId)
    }

    if (totalDelay === 0) {
      fn(...args)
    } else {
      timeoutId = setTimeout(() => {
        fn(...args)
      }, totalDelay)
    }
  }) as T
}

/**
 * Reset the priority system (useful for testing or cleanup)
 */
export function resetPrioritySystem(): void {
  highPriorityRequestsInProgress = 0
  highPriorityCompleteCallbacks = []
}
