import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(Boolean(path))
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!path) return
    setLoading(true)
    setError('')
    try {
      setData(await api<T>(path))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось загрузить данные')
    } finally {
      setLoading(false)
    }
  }, [path])

  useEffect(() => {
    void load()
  }, [load])

  return { data, loading, error, reload: load }
}
