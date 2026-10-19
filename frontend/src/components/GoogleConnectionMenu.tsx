/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-07
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

interface GoogleConnectionMenuProps {
  onReconnect: () => void;
  onDisconnect: () => void;
}

// ai: claude-sonnet-5 | 2026-09-25
/**
 * Header "Connected" pill that opens a small menu with the two Google-account
 * actions (reconnect / disconnect), replacing the separate Reconnect button.
 */
export default function GoogleConnectionMenu({ onReconnect, onDisconnect }: GoogleConnectionMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const choose = (action: () => void) => {
    setOpen(false);
    action();
  };

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((isOpen) => !isOpen)}
        aria-haspopup="menu"
        aria-expanded={open}
        style={triggerStyle}
        title="Google account connection"
      >
        <span style={dotStyle} />
        Connected
        <span aria-hidden="true" style={{ fontSize: "9px", opacity: 0.7 }}>▾</span>
      </button>
      {open && (
        <div role="menu" style={menuStyle}>
          <button
            role="menuitem"
            style={itemStyle}
            onClick={() => choose(onReconnect)}
            title="Re-authenticate to refresh Google permissions"
          >
            Reconnect
          </button>
          <button
            role="menuitem"
            style={{ ...itemStyle, color: "var(--red)" }}
            onClick={() => choose(onDisconnect)}
            title="Remove the stored Google authorization"
          >
            Disconnect
          </button>
        </div>
      )}
    </div>
  );
}

const triggerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "6px",
  fontSize: "12px",
  fontWeight: 500,
  padding: "5px 12px",
  background: "rgba(255,255,255,0.1)",
  color: "rgba(255,255,255,0.85)",
  border: "1px solid rgba(255,255,255,0.2)",
  borderRadius: "var(--radius)",
  cursor: "pointer",
};

const dotStyle: CSSProperties = {
  width: "7px",
  height: "7px",
  borderRadius: "50%",
  background: "#4ade80",
  display: "inline-block",
  boxShadow: "0 0 6px #4ade80",
};

const menuStyle: CSSProperties = {
  position: "absolute",
  top: "calc(100% + 6px)",
  right: 0,
  minWidth: "150px",
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  boxShadow: "var(--shadow)",
  padding: "4px",
  display: "flex",
  flexDirection: "column",
  zIndex: 200,
};

const itemStyle: CSSProperties = {
  textAlign: "left",
  fontSize: "13px",
  padding: "7px 10px",
  background: "transparent",
  color: "var(--text)",
  border: "none",
  borderRadius: "6px",
  cursor: "pointer",
};
