import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'

interface StatusBarState {
  left: ReactNode
  right: ReactNode
}

interface StatusBarContextValue {
  state: StatusBarState
  setLeft: (content: ReactNode) => void
  setRight: (content: ReactNode) => void
}

const StatusBarContext = createContext<StatusBarContextValue>({
  state: { left: null, right: null },
  setLeft: () => {},
  setRight: () => {},
})

export function StatusBarProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<StatusBarState>({ left: null, right: null })

  const setLeft = (content: ReactNode) => setState(s => ({ ...s, left: content }))
  const setRight = (content: ReactNode) => setState(s => ({ ...s, right: content }))

  return (
    <StatusBarContext.Provider value={{ state, setLeft, setRight }}>
      {children}
    </StatusBarContext.Provider>
  )
}

export function useStatusBar() {
  return useContext(StatusBarContext)
}

/** Sets status bar content while the component is mounted; clears on unmount. */
export function useStatusBarEffect(left: ReactNode, right: ReactNode) {
  const { setLeft, setRight } = useStatusBar()
  useEffect(() => {
    setLeft(left)
    setRight(right)
    return () => { setLeft(null); setRight(null) }
  }, [left, right])
}
