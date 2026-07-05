import { apiClient } from './client'

// Speech-to-speech via Gemini (Google AI Studio). STT needs a funded key
// (free tier is limit:0); TTS works on the free tier. Callers should handle
// a 502 from /speech/stt gracefully (voice input temporarily unavailable).

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onloadend = () => resolve((reader.result as string).split(',')[1] ?? '')
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

/** Transcribe recorded audio → text (Gemini multimodal). */
export async function speechToText(blob: Blob, language: string): Promise<string> {
  const audio_base64 = await blobToBase64(blob)
  const { data } = await apiClient.post<{ text: string }>('/speech/stt', {
    audio_base64,
    mime: blob.type || 'audio/webm',
    language,
  })
  return data.text
}

/** Synthesize text → a playable audio data URL (Gemini TTS). */
export async function textToSpeech(text: string, language: string): Promise<string> {
  const { data } = await apiClient.post<{ audio_base64: string; mime: string }>('/speech/tts', {
    text,
    language,
  })
  return `data:${data.mime};base64,${data.audio_base64}`
}
