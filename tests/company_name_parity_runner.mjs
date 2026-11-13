/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-20
// Invoked by tests/test_company_name_normalization_parity.py (node
// --experimental-strip-types, no build step) so that test can assert the
// REAL contactsMatchKey() source agrees with backend/scoring/
// company_matching.py's normalize_company_name() -- not a hand-copied
// re-implementation of either, which would just be a third place for the
// two to drift from.
import { contactsMatchKey } from "../frontend/src/utils/contactsUtils.ts";

const raw = await new Promise((resolve, reject) => {
  let data = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => { data += chunk; });
  process.stdin.on("end", () => resolve(data));
  process.stdin.on("error", reject);
});

const inputs = JSON.parse(raw);
process.stdout.write(JSON.stringify(inputs.map((value) => contactsMatchKey(value))));
