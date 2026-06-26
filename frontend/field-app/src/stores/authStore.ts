import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AuthState {
  token: string | null
  facilityId: string | null
  userId: string | null
  name: string | null
  facilityName: string | null
  setAuth: (a: {
    token: string
    facilityId: string
    userId: string
    name: string
    facilityName?: string
  }) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      facilityId: null,
      userId: null,
      name: null,
      facilityName: null,
      setAuth: (a) => set(a),
      logout: () =>
        set({ token: null, facilityId: null, userId: null, name: null, facilityName: null }),
    }),
    { name: 'smarthealth-field-auth' },
  ),
)
