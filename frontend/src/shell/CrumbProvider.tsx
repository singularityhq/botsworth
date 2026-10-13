import { useCallback, useMemo, useState } from 'react'
import { Ctx } from './crumbs'

export function CrumbProvider({ children }: { children: React.ReactNode }) {
  const [labels, setLabels] = useState<Record<string, string>>({})
  const setLabel = useCallback((key: string, label: string | null) => {
    setLabels((cur) => {
      if (label === null) {
        if (!(key in cur)) return cur
        const { [key]: _gone, ...rest } = cur
        return rest
      }
      return cur[key] === label ? cur : { ...cur, [key]: label }
    })
  }, [])
  const value = useMemo(() => ({ labels, setLabel }), [labels, setLabel])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
