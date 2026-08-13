/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

// ai: claude-sonnet-5 | 2026-08-13
import Modal from "./Modal.jsx";
import PhraseBookManager from "./PhraseBookManager.jsx";

/**
 * Modal wrapper around PhraseBookManager, for opening the phrase book from
 * inside another modal (Cover Letter / Pitch) — either to insert a phrase or
 * to save one just written. See PhraseBookPanel.jsx for the full-page version
 * reachable from the main nav.
 *
 * Props: onClose. Optional onInsert(text) — when provided, each entry gets a
 * "Use this" button that hands the phrase text back to the caller and closes.
 * Optional initialText — prefills the "add a new phrase" text area, so callers
 * can open this straight into "save what I just wrote" mode.
 */
export default function PhraseBookModal({ onClose, onInsert, initialText }) {
  return (
    <Modal title="Phrase Book" subtitle="Reusable 'why this company' material, tagged by theme" onClose={onClose} maxWidth="700px">
      <div style={{ maxHeight: "72vh", overflowY: "auto", paddingRight: "4px" }}>
        <PhraseBookManager onInsert={onInsert} onClose={onClose} initialText={initialText} />
      </div>
    </Modal>
  );
}
