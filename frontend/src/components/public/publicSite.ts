/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// The facts the public pages (homepage, privacy policy) and the login page's footer
// state about the service, in one place so they can't disagree. Changing the privacy
// policy's substance means updating PRIVACY_POLICY_UPDATED too.

export const APP_NAME = "Job Hunt Assistant";
export const COMPANY_NAME = "ZombieTech LLC";
export const PRIVACY_CONTACT_EMAIL = "privacy@zombietech.biz";
export const PRIVACY_POLICY_UPDATED = "November 10, 2026";

export const HOME_PATH = "/";
export const LOGIN_PATH = "/login";
export const PRIVACY_PATH = "/privacy";

/** Google's policy, which the Limited Use statement cites. */
export const GOOGLE_USER_DATA_POLICY_URL =
  "https://developers.google.com/terms/api-services-user-data-policy#additional_requirements_for_specific_api_scopes";
/** Where a user removes the app's Google access themselves. */
export const GOOGLE_PERMISSIONS_URL = "https://myaccount.google.com/permissions";
