import { createContext, useContext } from 'react'
import type { DatabaseInfo } from './types'

export type DatabaseContextValue = {
  databases: DatabaseInfo[]
  selected: string
  setSelected: (path: string) => void
  loading: boolean
}

export const DatabaseContext = createContext<DatabaseContextValue>({ databases: [], selected: '', setSelected: () => {}, loading: true })
export const useDatabase = () => useContext(DatabaseContext)
