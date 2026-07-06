import axios from 'axios'
import { useAuthStore } from '../stores/authStore'
import { addDebug } from '../lib/debugLog'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export const apiBaseUrl = API_URL

export const apiClient = axios.create({ baseURL: `${API_URL}/api/v1` })

apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) config.headers.Authorization = `Bearer ${token}`
  // Stamp start time so the response/error interceptor can compute latency.
  ;(config as { metadata?: { start: number } }).metadata = { start: Date.now() }
  return config
})

function durationOf(config: unknown): number | undefined {
  const start = (config as { metadata?: { start: number } })?.metadata?.start
  return start ? Date.now() - start : undefined
}

apiClient.interceptors.response.use(
  (res) => {
    addDebug({
      kind: 'response',
      method: res.config.method?.toUpperCase(),
      url: res.config.url,
      status: res.status,
      durationMs: durationOf(res.config),
    })
    return res
  },
  (err) => {
    if (err.response?.status === 401) useAuthStore.getState().logout()
    addDebug({
      kind: 'error',
      method: err.config?.method?.toUpperCase(),
      url: err.config?.url,
      status: err.response?.status,
      durationMs: durationOf(err.config),
      message:
        err.response?.data?.detail ||
        err.message ||
        'Request failed',
    })
    return Promise.reject(err)
  },
)
