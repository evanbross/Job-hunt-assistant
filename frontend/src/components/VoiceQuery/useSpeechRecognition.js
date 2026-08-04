/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

import { useCallback, useEffect, useRef, useState } from "react";

// No existing pattern to copy here -- this is the first Web Speech API usage
// in the app. Feature-detected (Chrome/Edge ship it prefixed or unprefixed;
// Safari/Firefox support is absent/partial), so callers must check
// `supported` and disable the mic button rather than assume it exists.
const RecognitionCtor =
  typeof window !== "undefined"
    ? window.SpeechRecognition || window.webkitSpeechRecognition
    : null;

// Click-to-toggle, not push-to-talk: a tap-and-release gesture races the Web
// Speech API's async start()/stop() (stop() can fire before start() has
// actually initialized the recognizer), and toggle is the simpler
// idle -> listening -> idle state machine for a header button that has to
// stay reachable while the results panel is open.
export default function useSpeechRecognition({ onFinalResult } = {}) {
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const recognitionRef = useRef(null);
  const onFinalResultRef = useRef(onFinalResult);

  useEffect(() => {
    onFinalResultRef.current = onFinalResult;
  }, [onFinalResult]);

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
    };
  }, []);

  const start = useCallback(() => {
    if (!RecognitionCtor || listening) return;
    const recognition = new RecognitionCtor();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      const text = event.results[0]?.[0]?.transcript || "";
      setTranscript(text);
      if (text.trim()) onFinalResultRef.current?.(text.trim());
    };
    recognition.onerror = () => setListening(false);
    recognition.onend = () => setListening(false);
    recognitionRef.current = recognition;
    setTranscript("");
    setListening(true);
    recognition.start();
  }, [listening]);

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
  }, []);

  return { listening, transcript, start, stop, supported: !!RecognitionCtor };
}
