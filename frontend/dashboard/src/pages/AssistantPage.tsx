import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { apiClient } from '../api/client'
import { speechToText, textToSpeech } from '../api/speech'
import { formatClock } from '../lib/format'
import { useAssistantStore } from '../stores/assistantStore'

const SUPPORTED_LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'Hindi' },
  { code: 'mr', label: 'Marathi' },
  { code: 'gu', label: 'Gujarati' },
  { code: 'pa', label: 'Punjabi' },
  { code: 'ta', label: 'Tamil' },
  { code: 'ml', label: 'Malayalam' },
  { code: 'te', label: 'Telugu' },
  { code: 'kn', label: 'Kannada' },
  { code: 'bn', label: 'Bengali' },
]

const SUGGESTED_QUESTIONS = [
  'assistant.q1',
  'assistant.q2',
  'assistant.q3',
  'assistant.q4',
  'assistant.q5',
]

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

function LoadingDots() {
  return (
    <div className="flex gap-1 items-center px-4 py-2.5">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  )
}

export default function AssistantPage() {
  const { t } = useTranslation()
  // Chat state lives in a store so it survives tab navigation (see assistantStore).
  const { messages, language, addMessage, setLanguage, initGreeting, clear } = useAssistantStore()
  useEffect(() => { initGreeting(t('assistant.greeting')) }, [initGreeting, t])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [recording, setRecording] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false) // transcribing
  const [voiceError, setVoiceError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // Speak an answer via Gemini TTS (Google AI Studio). Silent no-op on failure.
  async function speakAnswer(text: string) {
    try {
      const url = await textToSpeech(text, language)
      if (!audioRef.current) audioRef.current = new Audio()
      audioRef.current.src = url
      await audioRef.current.play()
    } catch {
      /* TTS unavailable — the text answer is already shown */
    }
  }

  async function startRecording() {
    setVoiceError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunksRef.current = []
      mr.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data) }
      mr.onstop = async () => {
        stream.getTracks().forEach((tr) => tr.stop())
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || 'audio/webm' })
        setVoiceBusy(true)
        try {
          const text = await speechToText(blob, language)
          if (text.trim()) await sendMessage(text, true)
          else setVoiceError(t('assistant.voice_unavailable'))
        } catch {
          setVoiceError(t('assistant.voice_unavailable'))
        } finally {
          setVoiceBusy(false)
        }
      }
      mediaRecorderRef.current = mr
      mr.start()
      setRecording(true)
    } catch {
      setVoiceError(t('assistant.voice_unavailable'))
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop()
    setRecording(false)
  }

  async function sendMessage(question: string, speak = false) {
    if (!question.trim() || loading) return

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: question.trim(),
      timestamp: Date.now(),
    }
    addMessage(userMsg)
    setInput('')
    setLoading(true)

    try {
      const { data } = await apiClient.post<{ answer: string }>('/assistant/query', {
        question: question.trim(),
        language,
      })

      addMessage({
        id: crypto.randomUUID(),
        role: 'assistant',
        content: data.answer,
        timestamp: Date.now(),
      })
      if (speak) await speakAnswer(data.answer)
    } catch {
      addMessage({
        id: crypto.randomUUID(),
        role: 'assistant',
        content: t('assistant.error'),
        timestamp: Date.now(),
      })
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage(input)
    }
  }

  const formatTime = (ts: number) => formatClock(new Date(ts))

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">{t('assistant.title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('assistant.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { clear(); initGreeting(t('assistant.greeting')) }}
            className="text-xs text-gray-500 hover:text-teal-700 border border-gray-200 rounded-lg px-2.5 py-1.5"
          >
            {t('assistant.new_chat', 'New chat')}
          </button>
          <label className="text-xs font-medium text-gray-500 mr-1">{t('assistant.response_language')}</label>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent outline-none"
          >
            {SUPPORTED_LANGUAGES.map((lang) => (
              <option key={lang.code} value={lang.code}>{t('lang.' + lang.code)}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto bg-white rounded-xl border border-gray-200 shadow-sm p-4 space-y-4">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            {msg.role === 'assistant' && (
              <div className="w-7 h-7 rounded-full bg-teal-100 flex items-center justify-center shrink-0 mr-2 mt-0.5">
                <svg className="w-4 h-4 text-teal-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17H4a2 2 0 01-2-2V5a2 2 0 012-2h16a2 2 0 012 2v10a2 2 0 01-2 2h-1" />
                </svg>
              </div>
            )}
            <div className={`max-w-[80%] ${msg.role === 'user' ? 'items-end' : 'items-start'} flex flex-col`}>
              <div
                className={`px-4 py-2.5 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
                  msg.role === 'user'
                    ? 'bg-teal-600 text-white rounded-tr-sm'
                    : 'bg-gray-100 text-gray-900 rounded-tl-sm'
                }`}
              >
                {msg.content}
              </div>
              <div className="flex items-center gap-2 mt-1 px-1">
                <span className="text-xs text-gray-400">{formatTime(msg.timestamp)}</span>
                {msg.role === 'assistant' && (
                  <button
                    onClick={() => speakAnswer(msg.content)}
                    title={t('assistant.mic')}
                    className="text-gray-400 hover:text-teal-600 transition-colors"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072M18.364 5.636a9 9 0 010 12.728M5 9v6h4l5 5V4L9 9H5z" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="w-7 h-7 rounded-full bg-teal-100 flex items-center justify-center shrink-0 mr-2 mt-0.5">
              <svg className="w-4 h-4 text-teal-600 animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17H4a2 2 0 01-2-2V5a2 2 0 012-2h16a2 2 0 012 2v10a2 2 0 01-2 2h-1" />
              </svg>
            </div>
            <div className="bg-gray-100 rounded-2xl rounded-tl-sm">
              <LoadingDots />
              <p className="px-4 pb-2 -mt-1 text-xs text-gray-400">{t('assistant.thinking_hint', 'Analyzing live district data…')}</p>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Suggested questions */}
      {messages.length <= 1 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTED_QUESTIONS.map((key) => (
            <button
              key={key}
              onClick={() => sendMessage(t(key))}
              disabled={loading}
              className="text-xs bg-white border border-gray-200 rounded-full px-3 py-1.5 text-gray-600 hover:bg-teal-50 hover:border-teal-300 hover:text-teal-700 transition-colors disabled:opacity-50"
            >
              {t(key)}
            </button>
          ))}
        </div>
      )}

      {voiceError && <p className="mt-2 text-xs text-red-600 text-center">{voiceError}</p>}

      {/* Input area */}
      <div className="mt-3 flex gap-3 bg-white rounded-xl border border-gray-200 shadow-sm p-3">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('assistant.placeholder')}
          rows={2}
          className="flex-1 resize-none text-sm text-gray-900 placeholder-gray-400 outline-none"
        />
        <button
          onClick={() => (recording ? stopRecording() : startRecording())}
          disabled={loading || voiceBusy}
          title={t('assistant.mic')}
          aria-label={t('assistant.mic')}
          className={`self-end rounded-lg px-3 py-2 transition-colors flex items-center disabled:opacity-50 ${
            recording ? 'bg-red-600 text-white animate-pulse' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
          }`}
        >
          {voiceBusy ? (
            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-14 0m7 7v3m0-3a4 4 0 004-4V5a4 4 0 00-8 0v6a4 4 0 004 4z" />
            </svg>
          )}
        </button>
        <button
          onClick={() => sendMessage(input)}
          disabled={loading || !input.trim()}
          className="self-end bg-teal-600 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-teal-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-1.5"
        >
          {loading ? (
            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
          )}
          {t('assistant.send')}
        </button>
      </div>
    </div>
  )
}
