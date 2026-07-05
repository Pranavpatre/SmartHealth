import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { getNearestFacilities, type NearestFacility } from '../api/facilities'
import { formatNumber, formatDecimal } from '../lib/format'

// Sentinel origin tokens — stored in state, translated at render time.
const ORIGIN_MY_LOCATION = '__my_location__'
const ORIGIN_SELECTED = '__selected_location__'

const GMAPS_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined

const DOT: Record<string, string> = { GREEN: '🟢', YELLOW: '🟡', RED: '🔴' }

// Keyless quick-pick locations — work without device GPS or a Maps key.
const PRESETS: { name: string; lat: number; lng: number }[] = [
  { name: 'Pune', lat: 18.5204, lng: 73.8567 },
  { name: 'Mumbai', lat: 19.076, lng: 72.8777 },
  { name: 'Delhi', lat: 28.6139, lng: 77.209 },
  { name: 'Bengaluru', lat: 12.9716, lng: 77.5946 },
  { name: 'Kolkata', lat: 22.5726, lng: 88.3639 },
  { name: 'Chennai', lat: 13.0827, lng: 80.2707 },
]

// Load the Google Maps JS SDK (Places) once.
function loadGoogleMaps(key: string): Promise<void> {
  return new Promise((resolve, reject) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if ((window as any).google?.maps?.places) return resolve()
    const existing = document.getElementById('gmaps-sdk') as HTMLScriptElement | null
    if (existing) {
      existing.addEventListener('load', () => resolve())
      existing.addEventListener('error', () => reject(new Error('gmaps')))
      return
    }
    const s = document.createElement('script')
    s.id = 'gmaps-sdk'
    s.src = `https://maps.googleapis.com/maps/api/js?key=${key}&libraries=places`
    s.async = true
    s.onload = () => resolve()
    s.onerror = () => reject(new Error('Failed to load Google Maps'))
    document.head.appendChild(s)
  })
}

export default function NearestFacilities() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [results, setResults] = useState<NearestFacility[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [origin, setOrigin] = useState<string | null>(null)
  const [manual, setManual] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const search = async (lat: number, lng: number, label: string) => {
    setLoading(true); setError(null); setOrigin(label)
    try {
      setResults(await getNearestFacilities(lat, lng, 10))
    } catch {
      setError(t('nearest.err_fetch'))
    } finally {
      setLoading(false)
    }
  }

  const useMyLocation = () => {
    setError(null)
    if (!navigator.geolocation) { setError(t('nearest.err_no_geo')); return }
    setLoading(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => search(pos.coords.latitude, pos.coords.longitude, ORIGIN_MY_LOCATION),
      (e) => {
        // POSITION_UNAVAILABLE (2) on desktops usually means the high-accuracy
        // fix failed — fall back to a coarse, cached lookup before giving up.
        const msg =
          e.code === e.POSITION_UNAVAILABLE
            ? t('nearest.err_no_fix')
            : e.message || t('nearest.err_denied')
        setError(msg)
        setLoading(false)
      },
      // Coarse + cached: works on desktops without GPS (mirrors how most sites
      // request location). High-accuracy here caused POSITION_UNAVAILABLE.
      { enableHighAccuracy: false, timeout: 15000, maximumAge: 300000 },
    )
  }

  const submitManual = () => {
    const m = manual.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/)
    if (!m) { setError(t('nearest.err_coords')); return }
    search(parseFloat(m[1]), parseFloat(m[2]), `${m[1]}, ${m[2]}`)
  }

  // Google Places Autocomplete on the address input (only when a key is configured).
  useEffect(() => {
    if (!GMAPS_KEY || !inputRef.current) return
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let ac: any
    loadGoogleMaps(GMAPS_KEY)
      .then(() => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const g = (window as any).google
        if (!g?.maps?.places || !inputRef.current) return
        ac = new g.maps.places.Autocomplete(inputRef.current, {
          componentRestrictions: { country: 'in' },
          fields: ['geometry', 'formatted_address'],
        })
        ac.addListener('place_changed', () => {
          const place = ac.getPlace()
          const loc = place?.geometry?.location
          if (loc) search(loc.lat(), loc.lng(), place.formatted_address || ORIGIN_SELECTED)
        })
      })
      .catch(() => setError(t('nearest.err_maps_key')))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Resolve a stored origin token to its display string at render time.
  const displayOrigin = (o: string): string => {
    if (o === ORIGIN_MY_LOCATION) return t('nearest.your_location')
    if (o === ORIGIN_SELECTED) return t('nearest.selected_location')
    const preset = PRESETS.find((p) => p.name === o)
    if (preset) return t('nearest.city_' + o.toLowerCase())
    return o
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
      <h2 className="font-semibold text-gray-800 mb-1">{t('nearest.title')}</h2>
      <p className="text-xs text-gray-400 mb-3">
        {t('nearest.desc')}
      </p>

      <div className="flex flex-col sm:flex-row gap-2 mb-3">
        {GMAPS_KEY ? (
          <input
            ref={inputRef}
            placeholder={t('nearest.search_placeholder')}
            className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-500"
          />
        ) : (
          <p className="flex-1 text-xs text-gray-400 self-center">
            {t('nearest.key_help')}
          </p>
        )}
        <button
          onClick={useMyLocation}
          className="bg-teal-600 text-white text-sm font-semibold py-2 px-4 rounded-lg hover:bg-teal-700 transition-colors whitespace-nowrap"
        >
          {t('nearest.use_location')}
        </button>
      </div>

      {/* Keyless fallbacks — no GPS or Maps key needed */}
      <div className="flex flex-wrap items-center gap-1.5 mb-3">
        <span className="text-xs text-gray-400 mr-1">{t('nearest.quick_pick')}</span>
        {PRESETS.map((p) => (
          <button
            key={p.name}
            onClick={() => search(p.lat, p.lng, p.name)}
            className="text-xs border border-gray-200 rounded-full px-2.5 py-1 text-gray-600 hover:bg-teal-50 hover:border-teal-300 transition-colors"
          >
            {t('nearest.city_' + p.name.toLowerCase())}
          </button>
        ))}
      </div>

      <div className="flex gap-2 mb-3">
        <input
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submitManual()}
          placeholder={t('nearest.coords_placeholder')}
          className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-500"
        />
        <button
          onClick={submitManual}
          className="border border-gray-200 text-gray-600 text-sm py-2 px-4 rounded-lg hover:bg-gray-50 transition-colors"
        >
          {t('nearest.go')}
        </button>
      </div>

      {loading && <p className="text-gray-400 text-sm">{t('nearest.finding')}</p>}
      {error && <p className="text-red-500 text-sm">{error}</p>}
      {origin && !loading && !error && (
        <p className="text-xs text-gray-500 mb-2">
          {t('nearest.nearest_to', { origin: displayOrigin(origin) })}
        </p>
      )}

      {results.length > 0 && (
        <ul className="divide-y divide-gray-100">
          {results.map((f) => (
            <li
              key={f.id}
              onClick={() => navigate(`/facilities/${f.id}`)}
              className="flex items-center justify-between py-2.5 cursor-pointer hover:bg-gray-50 rounded px-1"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900 truncate">
                  {DOT[f.traffic_light ?? ''] ?? '⚪'} {f.name}
                </p>
                <p className="text-xs text-gray-500">
                  {f.facility_type} · {f.district_name}
                  {f.health_score != null && ` · ${t('nearest.score')} ${formatNumber(f.health_score)}`}
                </p>
              </div>
              <span className="text-sm font-bold text-teal-700 whitespace-nowrap ml-3">
                {formatDecimal(f.distance_km, 1)} {t('common.km')}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
