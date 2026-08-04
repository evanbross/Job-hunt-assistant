/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

import { useCallback, useState } from "react";
import { describeFetchError } from "../../utils/fetchErrorUtils.js";
import useSpeechRecognition from "./useSpeechRecognition.js";
import VoiceMicButton from "./VoiceMicButton.jsx";
import VoiceQueryPanel from "./VoiceQueryPanel.jsx";

// Mounted once in App.jsx's sticky header. Modal (used by VoiceQueryPanel)
// renders via `position: fixed; inset: 0`, so it overlays the full page
// regardless of being nested inside the header in the DOM -- no separate
// top-level mount point or portal needed.
export default function VoiceQuery() {
  const [open, setOpen] = useState(false);
  const [querying, setQuerying] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [result, setResult] = useState(null);

  const runQuery = useCallback(async (text) => {
    setOpen(true);
    setTranscript(text);
    setResult(null);
    setQuerying(true);
    try {
      const res = await fetch("/api/voice/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok && !data.intent) {
        setResult({ error: data.error || `Voice query failed (${res.status})` });
        return;
      }
      setResult(data);
      if (data.answer_text && window.speechSynthesis) {
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(data.answer_text));
      }
    } catch (e) {
      setResult({ error: describeFetchError(e) });
    } finally {
      setQuerying(false);
    }
  }, []);

  const { listening, start, stop, supported } = useSpeechRecognition({
    onFinalResult: runQuery,
  });

  return (
    <>
      <VoiceMicButton
        listening={listening}
        supported={supported}
        onToggle={() => (listening ? stop() : start())}
      />
      {open && (
        <VoiceQueryPanel
          transcript={transcript}
          querying={querying}
          result={result}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
