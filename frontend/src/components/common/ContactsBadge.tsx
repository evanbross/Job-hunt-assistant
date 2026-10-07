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

// ai: claude-sonnet-5 | 2026-10-07
import { useState } from "react";
import { contactsMatchKey } from "../../utils/contactsUtils.ts";
import ContactsAtCompanyDialog from "./ContactsAtCompanyDialog.tsx";
import type { ContactsByCompany } from "../../types/contacts.ts";

interface ContactsBadgeProps {
  company?: string | null;
  contactsByCompany?: ContactsByCompany;
}

// ai: claude-sonnet-5 | 2026-07-25
export default function ContactsBadge({ company, contactsByCompany = {} }: ContactsBadgeProps) {
  const [open, setOpen] = useState(false);
  const contacts = contactsByCompany[contactsMatchKey(company)] ?? [];

  if (contacts.length === 0) return null;

  return (
    <>
      <button
        type="button"
        className="btn-secondary"
        onClick={() => setOpen(true)}
        style={{ fontSize: "12px", padding: "3px 9px" }}
        title={`You know ${contacts.length} people at this company`}
      >
        🔗 {contacts.length} connection{contacts.length === 1 ? "" : "s"}
      </button>
      {open && (
        <ContactsAtCompanyDialog
          company={company || "Unknown Company"}
          contacts={contacts}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
