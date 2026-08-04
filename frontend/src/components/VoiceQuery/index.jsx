/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

import { useCallback, useState } from "react";
import { describeFetchError } from "../../utils/fetchErrorUtils.js";
import VoiceAssistantButton from "./VoiceAssistantButton.jsx";
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

  return (
    <>
      <VoiceAssistantButton onClick={() => setOpen(true)} />
      {open && (
        <VoiceQueryPanel
          transcript={transcript}
          querying={querying}
          result={result}
          onSubmit={runQuery}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
