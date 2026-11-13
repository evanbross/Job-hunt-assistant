/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

import type { AuthGoogleStartResponse } from "../types/auth.ts";

// ai: claude-opus-5-5 | 2026-11-02
// Shared by every "Connect Google" button. Returns Google's consent URL, or null
// once it has already told the user why it can't start. When the app is open on
// a hostname Google can't redirect back to (e.g. jha.localhost), the backend
// answers 409 with connect_url, and this offers to go there instead.
export async function requestGoogleAuthUrl(): Promise<string | null> {
  const res = await fetch("/api/auth/google");
  const data: AuthGoogleStartResponse = await res.json();
  if (data.connect_url) {
    if (window.confirm(`${data.error}\n\nOpen ${data.connect_url} now?`)) {
      window.location.href = data.connect_url;
    }
    return null;
  }
  if (!res.ok || data.error || !data.auth_url) {
    window.alert(data.error || "Failed to start Google sign-in");
    return null;
  }
  return data.auth_url;
}
