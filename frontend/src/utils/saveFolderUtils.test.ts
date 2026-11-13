/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-06
// saveDocument(): the desktop app saves into a folder (and asks for another when that
// folder is gone); a hosted server sends the file back, which becomes a browser
// download -- never a folder prompt (docs/cloud-deployment-readiness-plan.md 2.2).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { attachmentFilename, saveDocument, saveStatusFor } from "./saveFolderUtils.ts";
import type { SaveDocumentRequest } from "../types/coverLetter.ts";

const BODY: SaveDocumentRequest = {
  text: "Dear team,",
  label: "Cover Letter",
  format: "docx",
  job_title: "Engineer",
  company: "Acme",
};

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    headers: new Headers({ "Content-Type": "application/json" }),
    json: () => Promise.resolve(body),
    blob: () => Promise.reject(new Error("not a file")),
  };
}

function fileResponse(disposition: string) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "Content-Disposition": disposition }),
    json: () => Promise.reject(new Error("not JSON")),
    blob: () => Promise.resolve(new Blob(["file bytes"])),
  };
}

describe("saveDocument", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let clicked: string[];

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    clicked = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    });
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reports where the desktop app saved the file", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ path: "C:/Letters/Acme.docx", filename: "Acme.docx", folder: "C:/Letters" }),
    );

    const result = await saveDocument(BODY);

    expect(result).toEqual({ downloaded: false, path: "C:/Letters/Acme.docx", folder: "C:/Letters" });
    expect(clicked).toEqual([]);
  });

  it("downloads the file a hosted server sends back", async () => {
    fetchMock.mockResolvedValue(fileResponse('attachment; filename="Acme - Engineer Cover Letter.docx"'));
    const promptSpy = vi.spyOn(window, "prompt");

    const result = await saveDocument(BODY);

    expect(result).toEqual({ downloaded: true, filename: "Acme - Engineer Cover Letter.docx" });
    expect(clicked).toEqual(["Acme - Engineer Cover Letter.docx"]);
    expect(promptSpy).not.toHaveBeenCalled();
  });

  it("asks for another folder when the desktop's is unavailable, then saves there", async () => {
    vi.spyOn(window, "alert").mockImplementation(() => {});
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ error: "gone", folder_error: true, folder: "E:/Old" }, 500))
      .mockResolvedValueOnce(jsonResponse({ error: "no tkinter" }, 500)) // the folder picker
      .mockResolvedValueOnce(jsonResponse({ path: "D:/New/Acme.docx", filename: "Acme.docx", folder: "D:/New" }));
    vi.spyOn(window, "prompt").mockReturnValue("D:/New");

    const result = await saveDocument(BODY);

    expect(result).toEqual({ downloaded: false, path: "D:/New/Acme.docx", folder: "D:/New" });
    expect(JSON.parse(fetchMock.mock.calls[2][1].body).folder).toBe("D:/New");
  });

  it("throws the server's error for any other failure", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: "Failed to create the document" }, 500));

    await expect(saveDocument(BODY)).rejects.toThrow("Failed to create the document");
  });
});

describe("attachmentFilename", () => {
  it("prefers the RFC 6266 encoded name", () => {
    expect(attachmentFilename("attachment; filename=Caf.docx; filename*=UTF-8''Caf%C3%A9.docx")).toBe("Café.docx");
  });

  it("is null for anything that isn't an attachment", () => {
    expect(attachmentFilename(null)).toBeNull();
    expect(attachmentFilename('inline; filename="x.pdf"')).toBeNull();
  });
});

describe("saveStatusFor", () => {
  it("keeps no folder for a download", () => {
    expect(saveStatusFor({ downloaded: true, filename: "a.pdf" }, "pdf")).toEqual({
      path: "a.pdf",
      folder: "",
      format: "pdf",
      downloaded: true,
    });
  });
});
