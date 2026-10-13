/** A one-shot timer that can pause while the pointer is over the toast (EXP-73) and resume with the time left. */
export class PauseTimer {
  private left: number
  private startedAt: number | null = null
  private handle: ReturnType<typeof setTimeout> | null = null
  private readonly onDone: () => void
  private readonly now: () => number

  constructor(ms: number, onDone: () => void, now: () => number = Date.now) {
    this.left = ms
    this.onDone = onDone
    this.now = now
    this.resume()
  }

  resume(): void {
    if (this.handle !== null || this.left <= 0) return
    this.startedAt = this.now()
    this.handle = setTimeout(this.onDone, this.left)
  }

  pause(): void {
    if (this.handle === null || this.startedAt === null) return
    clearTimeout(this.handle)
    this.handle = null
    this.left = Math.max(0, this.left - (this.now() - this.startedAt))
  }

  cancel(): void {
    if (this.handle !== null) clearTimeout(this.handle)
    this.handle = null
    this.left = 0
  }
}
