import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AuthState {
  token: string | null
  refreshToken: string | null
  userId: string | null
  role: string | null
  name: string | null
  facilityId: string | null
  facilityName: string | null
  districtId: number | null
  districtName: string | null
  stateId: number | null
  stateName: string | null
  // Shown once after login for DISTRICT_OFFICER+ (the dashboard nav tour
  // doesn't apply to PHC_ADMIN's single-facility view); re-openable anytime
  // via startNavTour.
  showNavTour: boolean
  setAuth: (auth: {
    token: string
    refreshToken: string
    userId: string
    role: string
    name: string
    facilityId?: string | null
    facilityName?: string | null
    districtId?: number | null
    districtName?: string | null
    stateId?: number | null
    stateName?: string | null
  }) => void
  dismissNavTour: () => void
  startNavTour: () => void
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
      facilityId: null,
      facilityName: null,
      districtId: null,
      districtName: null,
      stateId: null,
      stateName: null,
      showNavTour: false,
      setAuth: (auth) =>
        set({
          facilityId: null,
          facilityName: null,
          districtId: null,
          districtName: null,
          stateId: null,
          stateName: null,
          ...auth,
          showNavTour: auth.role !== 'PHC_ADMIN',
        }),
      dismissNavTour: () => set({ showNavTour: false }),
      startNavTour: () => set({ showNavTour: true }),
      logout: () =>
        set({
          token: null,
          refreshToken: null,
          userId: null,
          role: null,
          name: null,
          facilityId: null,
          facilityName: null,
          districtId: null,
          districtName: null,
          stateId: null,
          stateName: null,
          showNavTour: false,
        }),
    }),
    { name: 'smarthealth-auth' },
  ),
)
