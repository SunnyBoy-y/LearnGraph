import { createContext, useContext, useEffect, useRef } from 'react'

export const SettingsDirtyContext = createContext<(id: string, dirty: boolean) => void>(() => {})

/** Register explicit-save forms without storing drafts or credentials outside React. */
export function useSettingsDirty(id: string, dirty: boolean) {
  const register = useContext(SettingsDirtyContext)
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty
  useEffect(() => { register(id, dirty); return () => register(id, false) }, [id, dirty, register])
  useEffect(() => {
    const preventUnload = (event: BeforeUnloadEvent) => { if (dirtyRef.current) event.preventDefault() }
    window.addEventListener('beforeunload', preventUnload)
    return () => window.removeEventListener('beforeunload', preventUnload)
  }, [])
}
