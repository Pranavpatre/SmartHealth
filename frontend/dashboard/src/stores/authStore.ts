import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AuthState {
  token: string | null
  refreshToken: string | null
  userId: string | null
  role: string | null
  name: string | null
  setAuth: (auth: { token: string; refreshToken: string; userId: string; role: string; name: string }) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      refreshToken: null,
      userId: null,
      role: null,
      name: null,
      setAuth: (auth) => set(auth),
      logout: () => set({ token: null, refreshToken: null, userId: null, role: null, name: null }),
    }),
    { name: 'smarthealth-auth' },
  ),
)
