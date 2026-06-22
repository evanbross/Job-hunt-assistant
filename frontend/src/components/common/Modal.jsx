/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

import { useEffect, useRef } from "react";

/**
 * Standard Modal component for consistent dialogs across the app.
 *
 * @param {Object} props
 * @param {string} props.title - Main title of the modal.
 * @param {string} [props.subtitle] - Optional subtitle.
 * @param {React.ReactNode} props.children - Modal body content.
 * @param {React.ReactNode} [props.footer] - Optional footer content (buttons, etc).
 * @param {() => void} props.onClose - Callback when modal should close.
 * @param {string} [props.maxWidth] - Optional override for max-width (e.g. "800px").
 * @param {boolean} [props.showCloseButton=true] - Whether to show the top-right X.
 */
export default function Modal({
  title,
  subtitle,
  children,
  footer,
  onClose,
  maxWidth,
  showCloseButton = true,
}) {
  const panelRef = useRef(null);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const handleBackdropClick = (e) => {
    if (panelRef.current && !panelRef.current.contains(e.target)) {
      onClose();
    }
  };

  return (
    <div className="modal-backdrop" onClick={handleBackdropClick}>
      <div
        ref={panelRef}
        className="modal-panel"
        style={maxWidth ? { maxWidth } : undefined}
        onClick={(e) => e.stopPropagation()}
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
    </div>
  );
}
