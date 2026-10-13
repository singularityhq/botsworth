import { useRef, useState } from 'react'
import { useNav } from '../../../state/nav'
import { Toast } from '../../../ui/Toast'
import { Palette } from './Palette'
import type { Undo } from './palette.logic'

/** The shell mounts this and opens it on Cmd+K (EXP-67). The toast lives here, not in the palette, because it outlives the palette (EXP-73). */
export function PaletteHost({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNav()
  const [toast, setToast] = useState<{ id: number; undo: Undo } | null>(null)
  const nextId = useRef(0)
  return (
    <>
      {open ? <Palette onClose={onClose} onToast={(undo) => setToast({ id: nextId.current++, undo })} /> : null}
      {toast ? (
        <Toast
          key={toast.id}
          message={toast.undo.message}
          // EXP-72: Undo restores the search, filters and period; the session stays open.
          onUndo={() => nav.replace(toast.undo.patch)}
          onClose={() => setToast(null)}
        />
      ) : null}
    </>
  )
}
