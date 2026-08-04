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

// ai: claude-sonnet-5 | 2026-08-04
// Raw Web Speech API error codes -> human-readable text. "aborted" is the
// user clicking stop themselves, not a failure, so callers should treat a
// null return as "say nothing." Every other code gets a message because
// silently reverting to idle (the original bug here) is indistinguishable
// from the button just not working.
function describeSpeechError(code) {
  switch (code) {
    case "aborted":
      return null;
    case "no-speech":
      return "Didn't catch anything -- try again.";
    case "not-allowed":
    case "permission-denied":
      return "Microphone access is blocked. Check your browser's site permissions and allow microphone access for this page.";
    case "audio-capture":
      return "No microphone was found. Check that one is connected and enabled.";
    case "network":
      return "Couldn't reach the speech recognition service (network error). Check your internet connection and try again.";
    case "service-not-allowed":
      return "Speech recognition isn't available right now -- try again in a moment.";
    default:
      return `Speech recognition error: ${code}`;
  }
}

// Click-to-toggle, not push-to-talk: a tap-and-release gesture races the Web
// Speech API's async start()/stop() (stop() can fire before start() has
// actually initialized the recognizer), and toggle is the simpler
// idle -> listening -> idle state machine for a header button that has to
// stay reachable while the results panel is open.
export default function useSpeechRecognition({ onFinalResult, onError } = {}) {
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const recognitionRef = useRef(null);
  const onFinalResultRef = useRef(onFinalResult);
  const onErrorRef = useRef(onError);

  useEffect(() => {
    onFinalResultRef.current = onFinalResult;
  }, [onFinalResult]);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

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
    // ai: claude-sonnet-5 | 2026-08-04
    // Previously silent -- the button would revert to idle a second or two
    // after starting with zero indication why, which is exactly what a
    // "network" error from Chrome's cloud speech service looks like to a
    // user. Report it instead of only clearing `listening`.
    recognition.onerror = (event) => {
      setListening(false);
      const message = describeSpeechError(event.error);
      if (message) onErrorRef.current?.(message);
    };
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
