/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

// ai: claude-sonnet-5 | 2026-08-11
import { createPortal } from "react-dom";
import type { ReactNode } from "react";

interface ModalProps {
  /** Main title of the modal. */
  title: string;
  /** Optional subtitle. */
  subtitle?: string | null;
  /** Modal body content. */
  children: ReactNode;
  /** Optional footer content (buttons, etc). */
  footer?: ReactNode;
  /** Callback when modal should close. Omitted by callers that render the
   * close X but intentionally give it no behavior (e.g. a progress popup
   * with nothing to cancel back out of). */
  onClose?: () => void;
  /** Optional override for max-width (e.g. "800px"). */
  maxWidth?: string;
  /** Whether to show the top-right X. */
  showCloseButton?: boolean;
}

/**
 * Standard Modal component for consistent dialogs across the app.
 *
 * Modals are dismissed only via the top-right X or an explicit Close button —
 * never by clicking the backdrop or pressing ESC.
 */
// ai: claude-fable-5 | 2026-07-14
export default function Modal({
  title,
  subtitle,
  children,
  footer,
  onClose,
  maxWidth,
  showCloseButton = true,
}: ModalProps) {
  // Portal to document.body so a modal opened from inside another modal isn't
  // clipped by the parent's `overflow: hidden` panel or mispositioned by its
  // `backdrop-filter` (which creates a containing block for fixed descendants).
  return createPortal(
    <div className="modal-backdrop">
      <div
        className="modal-panel"
        style={maxWidth ? { maxWidth } : undefined}
      >
        <div className="modal-header">
          <div className="modal-title-group">
            <h3 className="modal-title">{title}</h3>
            {subtitle && <p className="modal-subtitle">{subtitle}</p>}
          </div>
          {showCloseButton && (
            <button
              type="button"
              className="modal-close-btn"
              onClick={onClose}
              aria-label="Close"
            >
              ✕
            </button>
          )}
        </div>

        <div className="modal-body">
          {children}
        </div>

        {footer && (
          <div className="modal-footer">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
