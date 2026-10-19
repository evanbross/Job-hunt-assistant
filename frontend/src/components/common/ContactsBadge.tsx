/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
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
