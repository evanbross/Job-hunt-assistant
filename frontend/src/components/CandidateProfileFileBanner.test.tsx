/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-28
// The "your profile file changed -- review and load?" banner: shown only while
// the server says a file version is waiting, and never loads anything without
// the review dialog's Load click carrying the reviewed version.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CandidateProfileFileBanner from "./CandidateProfileFileBanner.tsx";
import type { CandidateProfileFileStatus } from "../types/settings.ts";

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

const HASH_ONE = "1".repeat(64);
const HASH_TWO = "2".repeat(64);

function status(state: CandidateProfileFileStatus["state"], fileSha256 = HASH_ONE): CandidateProfileFileStatus {
  return {
    state,
    path: "F:/Resume/candidate_profile.md",
    file_sha256: fileSha256,
    file_modified_at: "2026-10-28T20:00:00+00:00",
    stored_updated_at: "2026-10-28T19:00:00+00:00",
  };
}

describe("CandidateProfileFileBanner", () => {
  let current: CandidateProfileFileStatus;
  let loadResponse: { body: unknown; status: number };
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    current = status("reload_available");
    loadResponse = { body: { backup_path: "", rescore_summary: { rescored: 3, failed: 0 } }, status: 200 };
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === "/api/candidate-profile/file-sync") return Promise.resolve(jsonResponse(current));
      if (url === "/api/candidate-profile/file-sync/review") {
        return Promise.resolve(jsonResponse({ ...current, diff: ["--- Stored profile", "+++ File", "-old rule", "+new rule"] }));
      }
      if (url === "/api/candidate-profile/file-sync/load" && method === "POST") {
        if (loadResponse.status === 200) current = status("up_to_date");
        return Promise.resolve(jsonResponse(loadResponse.body, loadResponse.status));
      }
      if (url === "/api/candidate-profile/file-sync/dismiss" && method === "POST") {
        current = status("dismissed");
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      return Promise.reject(new Error(`unexpected fetch ${method} ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function postedBodies(action: string): unknown[] {
    return fetchMock.mock.calls
      .filter(([url]) => url === `/api/candidate-profile/file-sync/${action}`)
      .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
  }

  it("shows nothing when no file version is waiting", async () => {
    current = status("up_to_date");
    const { container } = render(<CandidateProfileFileBanner />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("loads only after review, sending the reviewed version, then hides itself", async () => {
    render(<CandidateProfileFileBanner />);
    fireEvent.click(await screen.findByRole("button", { name: "Review changes" }));

    expect(await screen.findByText("-old rule")).toBeInTheDocument();
    expect(screen.getByText("+new rule")).toBeInTheDocument();
    expect(postedBodies("load")).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Load file" }));

    expect(await screen.findByText(/Re-scored 3 queued jobs/)).toBeInTheDocument();
    expect(postedBodies("load")).toEqual([{ file_sha256: HASH_ONE }]);
    expect(screen.queryByRole("button", { name: "Review changes" })).not.toBeInTheDocument();
  });

  it("shows the new diff instead of loading when the file changed after review", async () => {
    render(<CandidateProfileFileBanner />);
    fireEvent.click(await screen.findByRole("button", { name: "Review changes" }));
    await screen.findByText("+new rule");
    loadResponse = { body: { error: "The profile file changed since you reviewed it. Review it again." }, status: 409 };
    current = status("reload_available", HASH_TWO);

    fireEvent.click(screen.getByRole("button", { name: "Load file" }));

    expect(await screen.findByText(/changed since you reviewed it/)).toBeInTheDocument();
    loadResponse = { body: { backup_path: "", rescore_summary: { rescored: 0, failed: 0 } }, status: 200 };
    fireEvent.click(screen.getByRole("button", { name: "Load file" }));
    await waitFor(() => expect(postedBodies("load")).toEqual([{ file_sha256: HASH_ONE }, { file_sha256: HASH_TWO }]));
  });

  it("dismisses the offered version from the banner", async () => {
    render(<CandidateProfileFileBanner />);
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "Review changes" })).not.toBeInTheDocument());
    expect(postedBodies("dismiss")).toEqual([{ file_sha256: HASH_ONE }]);
  });

  it("re-checks when the tab regains focus", async () => {
    current = status("up_to_date");
    render(<CandidateProfileFileBanner />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    current = status("reload_available");
    fireEvent.focus(window);

    expect(await screen.findByRole("button", { name: "Review changes" })).toBeInTheDocument();
  });
});
