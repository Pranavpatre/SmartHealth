import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getNearestFacilities, type NearestFacility } from '../api/facilities'

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
      setError('Could not fetch nearest facilities.')
    } finally {
      setLoading(false)
    }
  }

  const useMyLocation = () => {
    setError(null)
    if (!navigator.geolocation) { setError('Geolocation is not available in this browser.'); return }
    setLoading(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => search(pos.coords.latitude, pos.coords.longitude, 'your current location'),
      (e) => {
        // POSITION_UNAVAILABLE (2) is common on desktops without Location Services.
        const msg =
          e.code === e.POSITION_UNAVAILABLE
            ? 'Could not get a location fix (enable macOS Location Services, or pick a city / enter coordinates below).'
            : e.message || 'Location permission denied.'
        setError(msg)
        setLoading(false)
      },
      { enableHighAccuracy: true, timeout: 10000 },
    )
  }

  const submitManual = () => {
    const m = manual.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/)
    if (!m) { setError('Enter coordinates as "lat, lng" — e.g. 18.52, 73.85'); return }
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
          if (loc) search(loc.lat(), loc.lng(), place.formatted_address || 'selected location')
        })
      })
      .catch(() => setError('Google Maps failed to load — check the API key.'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
      <h2 className="font-semibold text-gray-800 mb-1">Find Nearest PHCs / CHCs</h2>
      <p className="text-xs text-gray-400 mb-3">
        Enter a location or use your device GPS to find the closest facilities.
      </p>

      <div className="flex flex-col sm:flex-row gap-2 mb-3">
        {GMAPS_KEY ? (
          <input
            ref={inputRef}
            placeholder="Search a place or address (Google Maps)…"
            className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-500"
          />
        ) : (
          <p className="flex-1 text-xs text-gray-400 self-center">
            Set <code>VITE_GOOGLE_MAPS_API_KEY</code> to enable address search. Using GPS for now.
          </p>
        )}
        <button
          onClick={useMyLocation}
          className="bg-teal-600 text-white text-sm font-semibold py-2 px-4 rounded-lg hover:bg-teal-700 transition-colors whitespace-nowrap"
        >
          📍 Use my location
        </button>
      </div>

      {/* Keyless fallbacks — no GPS or Maps key needed */}
      <div className="flex flex-wrap items-center gap-1.5 mb-3">
        <span className="text-xs text-gray-400 mr-1">Quick pick:</span>
        {PRESETS.map((p) => (
          <button
            key={p.name}
            onClick={() => search(p.lat, p.lng, p.name)}
            className="text-xs border border-gray-200 rounded-full px-2.5 py-1 text-gray-600 hover:bg-teal-50 hover:border-teal-300 transition-colors"
          >
            {p.name}
          </button>
        ))}
      </div>

      <div className="flex gap-2 mb-3">
        <input
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submitManual()}
          placeholder="Or enter coordinates: lat, lng"
          className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-500"
        />
        <button
          onClick={submitManual}
          className="border border-gray-200 text-gray-600 text-sm py-2 px-4 rounded-lg hover:bg-gray-50 transition-colors"
        >
          Go
        </button>
      </div>

      {loading && <p className="text-gray-400 text-sm">Finding nearest facilities…</p>}
      {error && <p className="text-red-500 text-sm">{error}</p>}
      {origin && !loading && !error && (
        <p className="text-xs text-gray-500 mb-2">Nearest to <span className="font-medium">{origin}</span>:</p>
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
                  {f.health_score != null && ` · score ${f.health_score}`}
                </p>
              </div>
              <span className="text-sm font-bold text-teal-700 whitespace-nowrap ml-3">
                {f.distance_km} km
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
