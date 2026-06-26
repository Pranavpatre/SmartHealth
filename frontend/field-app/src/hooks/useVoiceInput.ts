import { useState, useCallback, useRef } from 'react'

export interface VoiceResult {
  transcript: string
  confidence: number
}

export function useVoiceInput(language: string = 'hi-IN') {
  const [isListening, setIsListening] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [error, setError] = useState<string | null>(null)
  const recognitionRef = useRef<SpeechRecognition | null>(null)

  const getSpeechRecognition = (): typeof SpeechRecognition | null => {
    if ('SpeechRecognition' in window) return window.SpeechRecognition
    if ('webkitSpeechRecognition' in window)
      return (window as Window & { webkitSpeechRecognition: typeof SpeechRecognition })
        .webkitSpeechRecognition
    return null
  }

  const startListening = useCallback(() => {
    const SpeechRecognitionCtor = getSpeechRecognition()
    if (!SpeechRecognitionCtor) {
      setError('Voice input not supported in this browser')
      return
    }
    setError(null)
    setTranscript('')

    const recognition = new SpeechRecognitionCtor()
    recognition.lang = language
    recognition.interimResults = false
    recognition.maxAlternatives = 1

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      const result = event.results[0][0]
      setTranscript(result.transcript)
      setIsListening(false)
    }

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      setError(`Voice error: ${event.error}`)
      setIsListening(false)
    }

    recognition.onend = () => setIsListening(false)

    recognitionRef.current = recognition
    recognition.start()
    setIsListening(true)
  }, [language])

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop()
    setIsListening(false)
  }, [])

  const reset = useCallback(() => {
    setTranscript('')
    setError(null)
  }, [])

  return { isListening, transcript, error, startListening, stopListening, reset }
}

/**
 * Parse a spoken number string (English or Hindi) into an integer.
 * Examples:
 *   "180 patients"        → 180
 *   "एक सौ अस्सी"         → 180
 *   "forty two"           → 42
 *   "ORS sachets forty"   → { medicine: "ORS sachets", quantity: 40 }
 */
const HINDI_ONES: Record<string, number> = {
  शून्य: 0, एक: 1, दो: 2, तीन: 3, चार: 4, पांच: 5, छह: 6, सात: 7, आठ: 8, नौ: 9,
  दस: 10, ग्यारह: 11, बारह: 12, तेरह: 13, चौदह: 14, पंद्रह: 15, सोलह: 16,
  सत्रह: 17, अठारह: 18, उन्नीस: 19, बीस: 20, तीस: 30, चालीस: 40, पचास: 50,
  साठ: 60, सत्तर: 70, अस्सी: 80, नब्बे: 90,
}
const HINDI_MULTIPLIERS: Record<string, number> = { सौ: 100, हज़ार: 1000, लाख: 100000 }

const ENGLISH_ONES: Record<string, number> = {
  zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
  eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14,
  fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19,
  twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70,
  eighty: 80, ninety: 90,
}
const ENGLISH_MULTIPLIERS: Record<string, number> = { hundred: 100, thousand: 1000, lakh: 100000 }

export function parseSpokenNumber(text: string): number | null {
  // First try direct digit extraction
  const digitMatch = text.match(/\d+/)
  if (digitMatch) return parseInt(digitMatch[0], 10)

  const words = text.toLowerCase().trim().split(/\s+/)
  let total = 0
  let current = 0
  let found = false

  for (const word of words) {
    if (HINDI_ONES[word] !== undefined) {
      current += HINDI_ONES[word]
      found = true
    } else if (HINDI_MULTIPLIERS[word] !== undefined) {
      current = (current || 1) * HINDI_MULTIPLIERS[word]
      total += current
      current = 0
      found = true
    } else if (ENGLISH_ONES[word] !== undefined) {
      current += ENGLISH_ONES[word]
      found = true
    } else if (ENGLISH_MULTIPLIERS[word] !== undefined) {
      current = (current || 1) * ENGLISH_MULTIPLIERS[word]
      total += current
      current = 0
      found = true
    }
  }

  total += current
  return found ? total : null
}
