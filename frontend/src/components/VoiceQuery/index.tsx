/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-07
import { useCallback, useState } from "react";
import { describeFetchError } from "../../utils/fetchErrorUtils.ts";
import VoiceAssistantButton from "./VoiceAssistantButton.tsx";
import VoiceQueryPanel from "./VoiceQueryPanel.tsx";
import type { VoiceQueryResult } from "../../types/voice.ts";

// Mounted once in App.jsx's sticky header. Modal (used by VoiceQueryPanel)
// renders via `position: fixed; inset: 0`, so it overlays the full page
// regardless of being nested inside the header in the DOM -- no separate
// top-level mount point or portal needed.
export default function VoiceQuery() {
  const [open, setOpen] = useState(false);
  const [querying, setQuerying] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [result, setResult] = useState<VoiceQueryResult | null>(null);

  const runQuery = useCallback(async (text: string) => {
    setTranscript(text);
    setResult(null);
    setQuerying(true);
    try {
      const res = await fetch("/api/voice/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data: VoiceQueryResult = await res.json().catch(() => ({}));
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
