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

import { normalizeCompany } from "./sheetUtils.ts";

// ai: claude-sonnet-5 | 2026-07-25
// Mirrors normalize_company_name() in backend/company_matching.py — keep the
// suffix list and operation order identical in both places.
export function contactsMatchKey(company?: string | null): string {
  return normalizeCompany(company).replace(/\s+/g, "");
}
