/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// The facts the public pages (homepage, privacy policy, terms) and the login page's
// footer state about the service, in one place so they can't disagree. Changing the
// privacy policy's substance means updating PRIVACY_POLICY_UPDATED too.

export const APP_NAME = "Job Hunt Assistant";
export const COMPANY_NAME = "ZombieTech LLC";
export const PRIVACY_CONTACT_EMAIL = "privacy@zombietech.biz";
export const PRIVACY_POLICY_UPDATED = "November 10, 2026";

// ai: claude-opus-5-5 | 2026-11-10
/**
 * The Terms of Service version the page shows, sent back when a user accepts it. Must
 * equal TERMS_VERSION in backend/security/terms_of_service.py, which refuses any other
 * (tests/test_terms_of_service.py checks they match). Changing the terms' substance
 * means bumping both, and every user then accepts the new version.
 */
export const TERMS_VERSION = "2026-11-10";
/** The law the terms are governed by, and whose courts hear disputes. */
export const GOVERNING_LAW_STATE = "Delaware";

export const HOME_PATH = "/";
export const LOGIN_PATH = "/login";
export const PRIVACY_PATH = "/privacy";
export const TERMS_PATH = "/terms"; // ai: claude-opus-5-5 | 2026-11-10

/** Google's policy, which the Limited Use statement cites. */
export const GOOGLE_USER_DATA_POLICY_URL =
  "https://developers.google.com/terms/api-services-user-data-policy#additional_requirements_for_specific_api_scopes";
/** Where a user removes the app's Google access themselves. */
export const GOOGLE_PERMISSIONS_URL = "https://myaccount.google.com/permissions";

// ai: claude-opus-5-5 | 2026-11-10
/** TERMS_VERSION ("YYYY-MM-DD") as the date the terms page shows. */
export function formatTermsDate(version: string = TERMS_VERSION): string {
  const [year, month, day] = version.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}
