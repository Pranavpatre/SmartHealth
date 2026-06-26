import { useState, useEffect } from 'react'
import { syncPendingData } from '../sync/syncService'

export default function OfflineBanner() {
  const [online, setOnline] = useState(navigator.onLine)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState<{ synced: number; errors: number } | null>(null)

  useEffect(() => {
    const handleOnline = async () => {
      setOnline(true)
      setSyncing(true)
      const result = await syncPendingData()
      setSyncResult(result)
      setSyncing(false)
      setTimeout(() => setSyncResult(null), 4000)
    }
    const handleOffline = () => {
      setOnline(false)
      setSyncResult(null)
    }
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)
    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [])

  if (online && !syncing && !syncResult) return null

  return (
    <div
      role="status"
      aria-live="polite"
      className={`fixed top-0 left-0 right-0 z-50 text-sm font-medium text-center py-2 px-4 transition-colors ${
        !online
          ? 'bg-orange-500 text-white'
          : syncing
            ? 'bg-blue-500 text-white'
            : 'bg-green-600 text-white'
      }`}
    >
      {!online && 'Offline — data saved locally, will sync when connected'}
      {online && syncing && 'Syncing data to server…'}
      {online && !syncing && syncResult && (
        <>
          Synced {syncResult.synced} record(s)
          {syncResult.errors > 0 && `, ${syncResult.errors} error(s)`}
        </>
      )}
    </div>
  )
}
