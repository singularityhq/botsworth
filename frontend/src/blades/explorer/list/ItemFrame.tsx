import type { BodyPos } from './virtual.logic'

/** One virtual item at its y. A group's body items carry the side rules; the last one closes the body with a bottom rule and an 8 px radius (SYS-37). */
export function ItemFrame({ top, height, body, z, children }: { top: number; height: number; body: BodyPos; z?: number; children: React.ReactNode }) {
  const edge = body === 'none' ? '' : body === 'mid' ? 'border-x border-rule' : 'overflow-hidden rounded-b-[8px] border-x border-b border-rule'
  return (
    <div className={`absolute inset-x-0 ${edge}`} style={{ top, height, zIndex: z }}>
      {children}
    </div>
  )
}
