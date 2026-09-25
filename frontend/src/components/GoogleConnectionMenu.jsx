/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 * You may read, study, and run this code for personal purposes.
 * You may NOT:
 *   - Use this code, in whole or in part, in any commercial product or service
 *   - Sell, sublicense, or otherwise monetize this code or derivatives of it
 *   - Redistribute this code without explicit written permission from ZombieTech LLC
 *
 * No warranty is provided, express or implied. Use at your own risk.
 *
 * Licensing inquiries: license@zombietech.biz
 */

import { useEffect, useRef, useState } from "react";

// ai: claude-sonnet-5 | 2026-09-25
/**
 * Header "Connected" pill that opens a small menu with the two Google-account
 * actions (reconnect / disconnect), replacing the separate Reconnect button.
 */
export default function GoogleConnectionMenu({ onReconnect, onDisconnect }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsideClick = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const choose = (action) => {
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

const triggerStyle = {
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

const dotStyle = {
  width: "7px",
  height: "7px",
  borderRadius: "50%",
  background: "#4ade80",
  display: "inline-block",
  boxShadow: "0 0 6px #4ade80",
};

const menuStyle = {
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

const itemStyle = {
  textAlign: "left",
  fontSize: "13px",
  padding: "7px 10px",
  background: "transparent",
  color: "var(--text)",
  border: "none",
  borderRadius: "6px",
  cursor: "pointer",
};
