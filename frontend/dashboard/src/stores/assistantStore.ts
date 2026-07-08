import { create } from 'zustand'

// Chat lives in a module-level store (not component state) so navigating away
// from the Assistant tab and back keeps the conversation instead of resetting it.
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

interface AssistantState {
  messages: ChatMessage[]
  language: string
  initialized: boolean
  setLanguage: (l: string) => void
  addMessage: (m: ChatMessage) => void
  initGreeting: (text: string) => void
  clear: () => void
}

export const useAssistantStore = create<AssistantState>((set, get) => ({
  messages: [],
  language: 'en',
  initialized: false,
  setLanguage: (language) => set({ language }),
  addMessage: (m) => set((s) => ({ messages: [...s.messages, m] })),
  initGreeting: (text) => {
    if (get().initialized) return
    set({
      initialized: true,
      messages: [{ id: '0', role: 'assistant', content: text, timestamp: Date.now() }],
    })
  },
  clear: () => set({ initialized: false, messages: [] }),
}))
