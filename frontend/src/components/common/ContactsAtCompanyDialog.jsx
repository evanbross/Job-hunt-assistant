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

import Modal from "./Modal.jsx";

// ai: claude-sonnet-5 | 2026-07-25
export default function ContactsAtCompanyDialog({ company, contacts, onClose }) {
  return (
    <Modal
      title={company}
      subtitle={`${contacts.length} connection${contacts.length === 1 ? "" : "s"} from your LinkedIn import`}
      onClose={onClose}
      maxWidth="560px"
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {contacts.map((contact) => (
          <div
            key={contact.id}
            style={{
              background: "var(--surface2)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: "10px 12px",
            }}
          >
            <div style={{ fontWeight: 700, fontSize: "13px", color: "var(--text)" }}>
              {contact.full_name}
            </div>
            {contact.position && (
              <div style={{ color: "var(--text-muted)", fontSize: "12px", marginTop: "2px" }}>
                {contact.position}
              </div>
            )}
            {contact.profile_url && (
              <a
                href={contact.profile_url}
                target="_blank"
                rel="noreferrer"
                style={{ color: "var(--accent)", fontSize: "12px", marginTop: "6px", display: "inline-block" }}
              >
                View LinkedIn profile
              </a>
            )}
          </div>
        ))}
      </div>
    </Modal>
  );
}
