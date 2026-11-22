/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-07
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import CompaniesReport from "./CompaniesReport.tsx";
import ContactDetail from "./ContactDetail.tsx";
import ContactsImport from "./ContactsImport.tsx";
import AddContactModal from "./leads/AddContactModal.tsx";
import OutreachQueue from "./OutreachQueue.tsx";
import OutreachTemplateManager from "./OutreachTemplateManager.tsx";
import TitlesReport from "./TitlesReport.tsx";
import { DISCARD_TEMPLATE_CHANGES_PROMPT, contactsMatchKey, safeHttpUrl, sendJson } from "../utils/contactsUtils.ts";
import { formatLocalDate } from "../utils/dateTimeUtils.ts";
import { readStoredPref, writeStoredPref } from "../utils/storedPreference.ts";
import type { Contact, ContactsListResponse, OutreachChannel } from "../types/contacts.ts";
import type { RecruiterType } from "../types/contacts.ts";
import type { ApplicationProfile } from "../types/applicationProfile.ts";

const PAGE_SIZE = 100;

// ai: claude-opus-5-5 | 2026-10-14 -- added "outreach" (the LinkedIn outreach queue)
// ai: claude-opus-5-5 | 2026-10-26 -- added "templates" (the outreach message templates)
type View = "contacts" | "outreach" | "templates" | "companies" | "titles";
// ai: claude-opus-5-5 | 2026-10-14 -- was EmailFilter ("emailed"/"not_emailed"):
// outreach now covers LinkedIn messages as well as emails.
type OutreachFilter = "all" | "contacted" | "not_contacted" | "awaiting" | "replied";
type RecruiterFilter = "all" | "recruiter" | "not_recruiter";
// ai: claude-opus-5-5 | 2026-10-26 -- was InterestedFilter ("all" or one of
// these); the filter is now any combination of them.
type InterestedChoice = "yes" | "no" | "tbd";
type OutreachState = "none" | "awaiting" | "replied";
type SortDir = "asc" | "desc";

const OUTREACH_FILTERS: { value: OutreachFilter; label: string }[] = [
  { value: "all", label: "All contacts" },
  { value: "contacted", label: "Contacted" },
  { value: "not_contacted", label: "Not contacted" },
  { value: "awaiting", label: "Contacted, awaiting reply" },
  { value: "replied", label: "Replied" },
];

// ai: claude-opus-5-5 | 2026-10-26 -- was a single-choice dropdown; now
// multi-choice toggles so e.g. Yes + Undecided can be shown together.
// Mirrors the tri-state INTERESTED_OPTIONS below (yes/no/undecided).
const INTERESTED_CHOICES: { value: InterestedChoice; label: string }[] = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
  { value: "tbd", label: "Undecided" },
];

function interestedChoice(contact: Contact): InterestedChoice {
  if (contact.interested === true) return "yes";
  if (contact.interested === false) return "no";
  return "tbd";
}

const RECRUITER_FILTERS: { value: RecruiterFilter; label: string }[] = [
  { value: "all", label: "Recruiters and others" },
  { value: "recruiter", label: "Recruiters only" },
  { value: "not_recruiter", label: "Not recruiters" },
];

// ai: claude-sonnet-5 | 2026-09-30
// Matches backend/db/repositories/contacts.py's recruiter_type CHECK
// constraint. NULL ("") means "not a recruiter".
const RECRUITER_TYPES: { value: RecruiterType | ""; label: string }[] = [
  { value: "", label: "Not a recruiter" },
  { value: "unrated", label: "Recruiter (unrated)" },
  { value: "motion", label: "Motion (high-pressure, low-success)" },
  { value: "low_reliability", label: "Low Reliability" },
  { value: "high_reliability", label: "High Reliability" },
  { value: "offshore_spam", label: "Offshore spam" },
];

// ai: claude-sonnet-5 | 2026-10-13
// Tri-state "Want to Contact": true/false/null = yes/no/undecided (the DB
// default for a never-reviewed contact -- see backend/db/schema.py's
// contacts.interested migration). Rendered as three small dots so all three
// states are always visible and one click away, rather than hidden behind a
// cycle-through toggle.
const INTERESTED_OPTIONS: { value: boolean | null; label: string; letter: string; color: string }[] = [
  { value: true, label: "Yes", letter: "Y", color: "var(--green)" },
  { value: false, label: "No", letter: "N", color: "var(--red)" },
  { value: null, label: "Undecided", letter: "?", color: "var(--text-muted)" },
];

function InterestedToggle({
  value,
  onChange,
  contactName,
}: {
  value: boolean | null | undefined;
  onChange: (next: boolean | null) => void;
  contactName: string;
}) {
  return (
    <div role="radiogroup" aria-label={`Want to contact ${contactName}`} style={{ display: "flex", gap: "4px" }}>
      {INTERESTED_OPTIONS.map((option) => {
        const selected = (value ?? null) === option.value;
        return (
          <button
            key={option.label}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={`${option.label} -- want to contact ${contactName}`}
            title={option.label}
            onClick={() => onChange(option.value)}
            style={{
              ...interestedDotStyle,
              borderColor: option.color,
              background: selected ? option.color : "transparent",
              color: selected ? "#fff" : option.color,
            }}
          >
            {option.letter}
          </button>
        );
      })}
    </div>
  );
}

// ai: claude-sonnet-5 | 2026-09-25
// ai: claude-opus-5-5 | 2026-10-14 -- was emailState over contact.emails
function outreachState(contact: Contact): OutreachState {
  if (contact.outreach.length === 0) return "none";
  return contact.outreach.some((entry) => entry.response_received_at) ? "replied" : "awaiting";
}

// ai: claude-sonnet-5 | 2026-10-02
const OUTREACH_STATE_RANK: Record<OutreachState, number> = { none: 0, awaiting: 1, replied: 2 };

// Recruiter column sorts by RECRUITER_TYPES' declared order (not-a-recruiter,
// then unrated/motion/low/high/offshore), not raw alphabetical enum text --
// alphabetical would scatter related tiers apart (e.g. "high_reliability"
// and "low_reliability" don't sort next to "motion").
const RECRUITER_RANK: Record<string, number> = Object.fromEntries(
  RECRUITER_TYPES.map((t, i) => [t.value, i]),
);

interface SortColumn {
  key: string;
  label: string;
  getValue: (c: Contact) => number | string;
}

// ai: claude-sonnet-5 | 2026-10-02
// Column sort-key config for the contacts table header. Each getValue()
// return must be directly comparable with < / > (numbers or lowercased
// strings) -- see toggleSort/sortedContacts below.
const SORT_COLUMNS: SortColumn[] = [
  // ai: gpt-5 | 2026-10-03
  { key: "outreach_score", label: "Priority", getValue: (c) => c.outreach_score ?? 0 },
  // ai: claude-sonnet-5 | 2026-10-13 -- Yes > Undecided > No
  { key: "interested", label: "Want to Contact", getValue: (c) => (c.interested === true ? 2 : c.interested === false ? 0 : 1) },
  { key: "name", label: "Contact", getValue: (c) => (c.full_name || "").toLowerCase() },
  { key: "recruiter", label: "Recruiter", getValue: (c) => RECRUITER_RANK[c.recruiter_type || ""] ?? 0 },
  // ai: claude-opus-5-5 | 2026-10-14 -- were "sent_email"/"Sent Email" and "email_status"/"Email status"
  { key: "contacted", label: "Contacted", getValue: (c) => (c.outreach.length > 0 ? 1 : 0) },
  { key: "outreach_status", label: "Outreach status", getValue: (c) => OUTREACH_STATE_RANK[outreachState(c)] },
];

// ai: claude-opus-5-5 | 2026-10-14
const CHANNEL_LABELS: Record<OutreachChannel, string> = { email: "Emailed", linkedin: "Messaged" };

// ai: claude-sonnet-5 | 2026-10-15
// Sentinel companyFilter value meaning "any of my previous employers," not a
// single company -- distinct from null (no company filter at all, every
// contact) and from a specific employer name (exact one picked).
const ANY_FORMER_EMPLOYER = "__any_former_employer__";

// ai: claude-sonnet-5 | 2026-10-15
// Bidirectional substring check on two already-normalized (contactsMatchKey)
// company strings. Neither side is reliably the "longer" one: a contact's
// stored company can be a plainer/shorter form of a longer name on the
// user's own employer list ("Aerva, Inc." vs. "Aerva by Watchfire"), or the
// reverse ("Google" vs. a contact's "Google Cloud"). Guards against an empty
// key matching everything ("".includes("") is true).
function companyKeysOverlap(a: string, b: string): boolean {
  if (!a || !b) return false;
  return a.includes(b) || b.includes(a);
}

interface FilterState {
  search: string;
  outreachFilter: OutreachFilter;
  recruiterFilter: RecruiterFilter;
  interestedFilter: InterestedChoice[]; // empty = no filter
  titleFilter: string | null;
  companyFilter: string | null;
  previousEmployers: string[];
}

// ai: claude-opus-5-5 | 2026-10-26
// The contacts list's filters, kept across visits and reloads. Each value is
// checked against its option list on the way back in, so a stale or
// hand-edited entry falls back to "all" instead of an option that no longer exists.
const FILTERS_STORAGE_KEY = "contactsFilters";

type StoredFilters = Omit<FilterState, "previousEmployers">;

const DEFAULT_FILTERS: StoredFilters = {
  search: "",
  outreachFilter: "all",
  recruiterFilter: "all",
  interestedFilter: [],
  titleFilter: null,
  companyFilter: null,
};

function knownOption<T extends string>(options: { value: T }[], value: unknown, fallback: T): T {
  return options.find((option) => option.value === value)?.value ?? fallback;
}

// Known choices in INTERESTED_CHOICES' order, deduplicated. A bare string is
// accepted too, so a value saved before this became multi-choice still loads.
function knownInterestedChoices(value: unknown): InterestedChoice[] {
  const saved: unknown[] = Array.isArray(value) ? value : [value];
  return INTERESTED_CHOICES.map((option) => option.value).filter((choice) => saved.includes(choice));
}

// ai: claude-opus-5-5 | 2026-10-26
// The table's sort, kept across visits like the filters but under its own key
// so Clear filters leaves it alone.
const SORT_STORAGE_KEY = "contactsSort";

interface StoredSort {
  sortKey: string;
  sortDir: SortDir;
}

const DEFAULT_SORT: StoredSort = { sortKey: "outreach_score", sortDir: "desc" };

function loadStoredSort(): StoredSort {
  try {
    const saved: unknown = JSON.parse(readStoredPref(SORT_STORAGE_KEY, "{}"));
    if (!saved || typeof saved !== "object") return DEFAULT_SORT;
    const { sortKey, sortDir } = saved as Record<string, unknown>;
    if (!SORT_COLUMNS.some((column) => column.key === sortKey)) return DEFAULT_SORT;
    if (sortDir !== "asc" && sortDir !== "desc") return DEFAULT_SORT;
    return { sortKey: sortKey as string, sortDir };
  } catch {
    return DEFAULT_SORT;
  }
}

function loadStoredFilters(): StoredFilters {
  let saved: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(readStoredPref(FILTERS_STORAGE_KEY, "{}"));
    if (parsed && typeof parsed === "object") saved = parsed as Record<string, unknown>;
  } catch {
    /* corrupt value: use the defaults */
  }
  const stringOrNull = (value: unknown) => (typeof value === "string" ? value : null);
  return {
    search: typeof saved.search === "string" ? saved.search : DEFAULT_FILTERS.search,
    outreachFilter: knownOption(OUTREACH_FILTERS, saved.outreachFilter, DEFAULT_FILTERS.outreachFilter),
    recruiterFilter: knownOption(RECRUITER_FILTERS, saved.recruiterFilter, DEFAULT_FILTERS.recruiterFilter),
    interestedFilter: knownInterestedChoices(saved.interestedFilter),
    // "" is a real title filter ("(no title)"), so only a non-string is dropped.
    titleFilter: stringOrNull(saved.titleFilter),
    // Checked against the Previous Employers list once it loads (see activeCompanyFilter).
    companyFilter: stringOrNull(saved.companyFilter),
  };
}

// ai: claude-sonnet-5 | 2026-09-25
function matchesFilters(
  contact: Contact,
  { search, outreachFilter, recruiterFilter, interestedFilter, titleFilter, companyFilter, previousEmployers }: FilterState,
): boolean {
  // ai: claude-opus-5-5 | 2026-10-26
  if (interestedFilter.length > 0 && !interestedFilter.includes(interestedChoice(contact))) return false;
  if (titleFilter !== null && contact.normalized_title !== titleFilter) return false;
  // ai: claude-sonnet-5 | 2026-10-14 -- "former coworkers" filter, sourced
  // from the user's own Previous Employers list (Application Profile tab).
  // Substring match (not exact) on the same corporate-suffix-stripping
  // normalizer used elsewhere, so "Google" matches "Google LLC" (suffix
  // stripped) as well as "Google Cloud" or "Alphabet/Google" (substring) --
  // deliberately looser than the exact-match normalizer's other call sites.
  // ai: claude-sonnet-5 | 2026-10-15 -- must check both directions: a
  // contact's stored company can be the shorter/plainer form of a longer
  // employer name on the user's own list (e.g. previous_employers has
  // "Aerva by Watchfire" but a contact's company_raw is just "Aerva, Inc."),
  // and a one-directional "does the contact's name contain the filter" check
  // never matches that (confirmed live: contact Jeremy Denham / "Aerva,
  // Inc." didn't match "Aerva by Watchfire" until this fix).
  if (companyFilter === ANY_FORMER_EMPLOYER) {
    // ai: claude-sonnet-5 | 2026-10-15 -- matches ANY employer on the list,
    // not just one picked from the dropdown.
    const contactKey = contactsMatchKey(contact.company_raw);
    if (!previousEmployers.some((name) => companyKeysOverlap(contactKey, contactsMatchKey(name)))) {
      return false;
    }
  } else if (
    companyFilter !== null &&
    !companyKeysOverlap(contactsMatchKey(contact.company_raw), contactsMatchKey(companyFilter))
  ) {
    return false;
  }

  const state = outreachState(contact);
  if (outreachFilter === "contacted" && state === "none") return false;
  if (outreachFilter === "not_contacted" && state !== "none") return false;
  if (outreachFilter === "awaiting" && state !== "awaiting") return false;
  if (outreachFilter === "replied" && state !== "replied") return false;

  if (recruiterFilter === "recruiter" && !contact.recruiter_type) return false;
  if (recruiterFilter === "not_recruiter" && contact.recruiter_type) return false;

  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  return [contact.full_name, contact.company_raw, contact.position, contact.notes].some((field) =>
    (field || "").toLowerCase().includes(needle),
  );
}

// ai: claude-opus-5-5 | 2026-11-11 -- phone and keep_informed added
type ContactPatchFields = Partial<
  Pick<
    Contact,
    "email_address" | "personal_email" | "notes" | "interested" | "recruiter_type" | "phone" | "keep_informed"
  > // ai: claude-opus-5-5 | 2026-11-22 -- personal_email added
>;

// ai: claude-opus-5-5 | 2026-10-26
interface ContactsPanelProps {
  /** Told whenever unsaved edits appear or go away (and false on unmount), so leaving the section can ask first. */
  onUnsavedChangesChange: (unsaved: boolean) => void;
}

// ai: claude-sonnet-5 | 2026-09-25
export default function ContactsPanel({ onUnsavedChangesChange }: ContactsPanelProps) {
  const [view, setView] = useState<View>("contacts");
  // ai: claude-opus-5-5 | 2026-10-26 -- filters start from the last visit's
  const [initialFilters] = useState(loadStoredFilters);
  const [titleFilter, setTitleFilter] = useState<string | null>(initialFilters.titleFilter); // normalized_title, set by TitlesReport's count link
  const [companyFilter, setCompanyFilter] = useState<string | null>(initialFilters.companyFilter);
  const [previousEmployers, setPreviousEmployers] = useState<string[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [addingContact, setAddingContact] = useState(false); // ai: claude-opus-5-5 | 2026-11-11
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const [search, setSearch] = useState(initialFilters.search);
  const [outreachFilter, setOutreachFilter] = useState<OutreachFilter>(initialFilters.outreachFilter);
  const [recruiterFilter, setRecruiterFilter] = useState<RecruiterFilter>(initialFilters.recruiterFilter);
  const [interestedFilter, setInterestedFilter] = useState<InterestedChoice[]>(initialFilters.interestedFilter);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set());
  const [bulkPending, setBulkPending] = useState(false);
  const [bulkRecruiterType, setBulkRecruiterType] = useState<RecruiterType>("unrated");
  // ai: claude-opus-5-5 | 2026-10-26 -- starts from the last visit's sort (default: Priority, high first)
  const [initialSort] = useState(loadStoredSort);
  const [sortKey, setSortKey] = useState(initialSort.sortKey);
  const [sortDir, setSortDir] = useState<SortDir>(initialSort.sortDir);
  // ai: claude-opus-5-5 | 2026-10-21
  // Contacts picked via a row's or the selection's "Message" button (null =
  // the default outreach queue). outreachRunId remounts OutreachQueue on each
  // new pick so its skipped/sent/draft state starts fresh.
  const [outreachTargetIds, setOutreachTargetIds] = useState<number[] | null>(null);
  const [outreachRunId, setOutreachRunId] = useState(0);
  // ai: claude-opus-5-5 | 2026-10-26
  // Unsaved edits in the Templates tab, so leaving it asks first.
  const [templatesDirty, setTemplatesDirty] = useState(false);
  // ai: claude-opus-5-5 | 2026-10-26
  useEffect(() => {
    onUnsavedChangesChange(templatesDirty);
  }, [templatesDirty, onUnsavedChangesChange]);
  useEffect(() => () => onUnsavedChangesChange(false), [onUnsavedChangesChange]);

  const loadContacts = async () => {
    try {
      const res = await fetch("/api/contacts");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: ContactsListResponse = await res.json();
      setContacts(data.contacts);
      setLoadError("");
    } catch (e) {
      setLoadError(`Could not load contacts: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadContacts();
  }, []);

  // ai: claude-sonnet-5 | 2026-10-14 -- source list for the company filter
  // below; user-maintained in the Application Profile tab.
  useEffect(() => {
    fetch("/api/application-profile")
      .then((res) => (res.ok ? res.json() : null))
      .then((data: ApplicationProfile | null) => setPreviousEmployers(data?.previous_employers ?? []))
      .catch(() => setPreviousEmployers([]));
  }, []);

  // ai: claude-opus-5-5 | 2026-10-26
  // A remembered employer only applies while it's still on the Previous
  // Employers list -- otherwise its dropdown would show a different option
  // (or be hidden entirely) while the stale value silently kept filtering.
  // Derived rather than reset, so a failed profile load doesn't erase it.
  const activeCompanyFilter =
    companyFilter === ANY_FORMER_EMPLOYER
      ? previousEmployers.length > 0
        ? companyFilter
        : null
      : companyFilter !== null && previousEmployers.includes(companyFilter)
        ? companyFilter
        : null;

  useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [search, outreachFilter, recruiterFilter, interestedFilter, titleFilter, activeCompanyFilter]);

  // ai: claude-opus-5-5 | 2026-10-26
  useEffect(() => {
    const stored: StoredFilters = { search, outreachFilter, recruiterFilter, interestedFilter, titleFilter, companyFilter };
    writeStoredPref(FILTERS_STORAGE_KEY, JSON.stringify(stored));
  }, [search, outreachFilter, recruiterFilter, interestedFilter, titleFilter, companyFilter]);

  // ai: claude-opus-5-5 | 2026-10-26
  useEffect(() => {
    const stored: StoredSort = { sortKey, sortDir };
    writeStoredPref(SORT_STORAGE_KEY, JSON.stringify(stored));
  }, [sortKey, sortDir]);

  // ai: claude-opus-5-5 | 2026-10-26
  const hasActiveFilters =
    search.trim() !== "" ||
    outreachFilter !== DEFAULT_FILTERS.outreachFilter ||
    recruiterFilter !== DEFAULT_FILTERS.recruiterFilter ||
    interestedFilter.length > 0 ||
    titleFilter !== DEFAULT_FILTERS.titleFilter ||
    activeCompanyFilter !== DEFAULT_FILTERS.companyFilter;

  const clearFilters = () => {
    setSearch(DEFAULT_FILTERS.search);
    setOutreachFilter(DEFAULT_FILTERS.outreachFilter);
    setRecruiterFilter(DEFAULT_FILTERS.recruiterFilter);
    setInterestedFilter(DEFAULT_FILTERS.interestedFilter);
    setTitleFilter(DEFAULT_FILTERS.titleFilter);
    setCompanyFilter(DEFAULT_FILTERS.companyFilter);
  };

  // ai: claude-opus-5-5 | 2026-10-26
  const toggleInterestedChoice = (choice: InterestedChoice) =>
    setInterestedFilter((prev) =>
      knownInterestedChoices(prev.includes(choice) ? prev.filter((c) => c !== choice) : [...prev, choice]),
    );

  const viewContactsForTitle = (normalizedTitle: string) => {
    setTitleFilter(normalizedTitle);
    setView("contacts");
  };

  const filtered = useMemo(
    () =>
      contacts.filter((contact) =>
        matchesFilters(contact, {
          search,
          outreachFilter,
          recruiterFilter,
          interestedFilter,
          titleFilter,
          companyFilter: activeCompanyFilter,
          previousEmployers,
        }),
      ),
    [contacts, search, outreachFilter, recruiterFilter, interestedFilter, titleFilter, activeCompanyFilter, previousEmployers],
  );

  // ai: claude-opus-5-5 | 2026-10-14 -- same criteria as OutreachQueue's list, for the tab badge
  const outreachQueueCount = useMemo(
    () => contacts.filter((c) => c.interested === true && c.outreach.length === 0 && safeHttpUrl(c.profile_url)).length,
    [contacts],
  );

  // ai: claude-sonnet-5 | 2026-10-02
  // Clicking a sortable column header sets it as the sort key; clicking the
  // same header again flips direction instead of re-selecting it.
  const toggleSort = (key: string) => {
    setSortKey((prevKey) => {
      setSortDir((prevDir) => (prevKey === key ? (prevDir === "asc" ? "desc" : "asc") : "asc"));
      return key;
    });
  };

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    const column = SORT_COLUMNS.find((c) => c.key === sortKey);
    if (!column) return filtered;
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = column.getValue(a);
      const bv = column.getValue(b);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return a.full_name.localeCompare(b.full_name) || a.id - b.id;
    });
  }, [filtered, sortKey, sortDir]);

  // ai: claude-sonnet-5 | 2026-09-25
  // A bulk action must only ever touch rows the user can currently see in the
  // filtered list, so selections that a filter change hides are dropped.
  useEffect(() => {
    setSelectedIds((prev) => {
      const filteredIds = new Set(filtered.map((c) => c.id));
      const kept = new Set([...prev].filter((id) => filteredIds.has(id)));
      return kept.size === prev.size ? prev : kept;
    });
  }, [filtered]);

  const allFilteredSelected = filtered.length > 0 && selectedIds.size === filtered.length;

  const toggleSelected = (contactId: number, checked: boolean) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(contactId);
      else next.delete(contactId);
      return next;
    });

  const toggleSelectAllFiltered = (checked: boolean) =>
    setSelectedIds(checked ? new Set(filtered.map((c) => c.id)) : new Set());

  // ai: claude-opus-5-5 | 2026-10-21
  const messageContacts = (contactIds: number[]) => {
    setOutreachTargetIds(contactIds);
    setOutreachRunId((id) => id + 1);
    setView("outreach");
  };

  // ai: claude-opus-5-5 | 2026-10-26
  // The tab bar is the only way out of the Templates tab (the other setView
  // callers live in the Contacts, Outreach and Titles tabs).
  // ai: claude-opus-5-5 | 2026-10-26 -- returns whether the switch happened
  const switchView = (next: View): boolean => {
    if (
      view === "templates" &&
      next !== "templates" &&
      templatesDirty &&
      !window.confirm(DISCARD_TEMPLATE_CHANGES_PROMPT)
    ) {
      return false;
    }
    setView(next);
    return true;
  };

  // Selection in the table's current sort order, so the queue walks them in
  // the order the user is looking at.
  const messageSelected = () => {
    messageContacts(sorted.filter((c) => selectedIds.has(c.id)).map((c) => c.id));
    setSelectedIds(new Set());
  };

  const clearOutreachTarget = () => {
    setOutreachTargetIds(null);
    setOutreachRunId((id) => id + 1);
  };

  // ai: claude-opus-5-5 | 2026-10-26
  // The tab's badge counts the full queue, so the tab always opens it; only
  // the Message buttons open a picked-contacts run. Already on the full queue,
  // it doesn't remount (which would forget this session's skips).
  const openFullOutreachQueue = () => {
    if (!switchView("outreach")) return;
    if (outreachTargetIds !== null) clearOutreachTarget();
  };

  const bulkSetRecruiter = async (recruiterType: RecruiterType | null) => {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    setBulkPending(true);
    try {
      const res = await sendJson("/api/contacts/bulk-recruiter", "POST", {
        contact_ids: ids,
        recruiter_type: recruiterType,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const idSet = new Set(ids);
      setContacts((prev) =>
        prev.map((c) => (idSet.has(c.id) ? { ...c, recruiter_type: recruiterType } : c)),
      );
      setSelectedIds(new Set());
      // ai: gpt-5 | 2026-10-03
      await loadContacts();
    } catch (e) {
      window.alert(`Bulk update failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBulkPending(false);
    }
  };

  const replaceContact = (contactId: number, changes: Partial<Contact>) =>
    setContacts((prev) => prev.map((c) => (c.id === contactId ? { ...c, ...changes } : c)));

  // Optimistic: flip the UI immediately, roll back only the touched fields if
  // the server rejects the write.
  const patchContact = async (contactId: number, fields: ContactPatchFields): Promise<boolean> => {
    const previous = contacts.find((c) => c.id === contactId);
    if (!previous) return false;
    const rollback: ContactPatchFields = Object.fromEntries(
      Object.keys(fields).map((key) => [key, previous[key as keyof ContactPatchFields]]),
    );
    replaceContact(contactId, fields);
    try {
      const res = await sendJson(`/api/contacts/${contactId}`, "PATCH", fields);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // ai: gpt-5 | 2026-10-03
      await loadContacts();
      return true;
    } catch (e) {
      replaceContact(contactId, rollback);
      window.alert(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  };

  const saveEmailAddress = (contactId: number, value: string) =>
    patchContact(contactId, { email_address: value === "" ? null : value });

  const saveNotes = (contactId: number, value: string) =>
    patchContact(contactId, { notes: value === "" ? null : value });

  // ai: claude-opus-5-5 | 2026-11-11
  // ai: claude-opus-5-5 | 2026-11-22
  const savePersonalEmail = (contactId: number, value: string) =>
    patchContact(contactId, { personal_email: value === "" ? null : value });

  const savePhone = (contactId: number, value: string) =>
    patchContact(contactId, { phone: value === "" ? null : value });

  const setKeepInformed = (contactId: number, keepInformed: boolean) =>
    patchContact(contactId, { keep_informed: keepInformed });

  // ai: claude-opus-5-5 | 2026-10-14 -- was logEmail; records the channel too
  const logOutreach = async (contactId: number, sentAt: string, channel: OutreachChannel) => {
    try {
      const res = await sendJson(`/api/contacts/${contactId}/outreach`, "POST", { sent_at: sentAt, channel });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const created = await res.json();
      setContacts((prev) =>
        prev.map((c) =>
          c.id === contactId
            ? {
                ...c,
                outreach_draft: null,
                outreach: [...c.outreach, created].sort(
                  (a, b) => a.sent_at.localeCompare(b.sent_at) || a.id - b.id,
                ),
              }
            : c,
        ),
      );
      // ai: gpt-5 | 2026-10-03
      await loadContacts();
    } catch (e) {
      window.alert(`Could not log the message: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const setResponse = async (contactId: number, outreachId: number, receivedAt: string | null) => {
    try {
      // ai: claude-opus-5-5 | 2026-11-11 -- logged outreach lives in interactions now
      const res = await sendJson(`/api/interactions/${outreachId}`, "PATCH", {
        response_received_at: receivedAt,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setContacts((prev) =>
        prev.map((c) =>
          c.id === contactId
            ? {
                ...c,
                outreach: c.outreach.map((entry) =>
                  entry.id === outreachId ? { ...entry, response_received_at: receivedAt } : entry,
                ),
              }
            : c,
        ),
      );
      // ai: gpt-5 | 2026-10-03
      await loadContacts();
    } catch (e) {
      window.alert(`Could not update the reply: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const deleteOutreach = async (contactId: number, outreachId: number) => {
    try {
      const res = await sendJson(`/api/interactions/${outreachId}`, "DELETE"); // ai: claude-opus-5-5 | 2026-11-11
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setContacts((prev) =>
        prev.map((c) =>
          c.id === contactId ? { ...c, outreach: c.outreach.filter((entry) => entry.id !== outreachId) } : c,
        ),
      );
      // ai: gpt-5 | 2026-10-03
      await loadContacts();
    } catch (e) {
      window.alert(`Could not remove the entry: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const importVisible = showImport || (!loading && !loadError && contacts.length === 0);
  const visible = sorted.slice(0, visibleCount);

  return (
    <div>
      <div style={{ marginBottom: "16px", display: "flex", alignItems: "flex-end", gap: "16px" }}>
        <div style={{ flex: 1 }}>
          <h2 style={titleStyle}>Contacts</h2>
          <p style={summaryStyle}>
            Your LinkedIn connections. Mark the ones you want to reach, mark the recruiters (or
            select several and mark them at once), and log the messages you send and any replies.
            The Outreach queue walks you through messaging each "Yes" contact on LinkedIn, or use
            Message on any contact (or a selection) to message just those.
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button
            className={view === "contacts" ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            onClick={() => switchView("contacts")}
          >
            Contacts
          </button>
          <button
            className={view === "outreach" ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            onClick={openFullOutreachQueue}
          >
            Outreach queue{outreachQueueCount > 0 ? ` (${outreachQueueCount})` : ""}
          </button>
          {/* ai: claude-opus-5-5 | 2026-10-26 */}
          <button
            className={view === "templates" ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            onClick={() => switchView("templates")}
          >
            Templates
          </button>
          <button
            className={view === "companies" ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            onClick={() => switchView("companies")}
          >
            Companies
          </button>
          <button
            className={view === "titles" ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            onClick={() => switchView("titles")}
          >
            Titles
          </button>
        </div>
        {view === "contacts" && contacts.length > 0 && (
          <button className="btn-secondary btn-sm" onClick={() => setShowImport((open) => !open)}>
            {importVisible ? "Hide import" : "Import CSV"}
          </button>
        )}
        {/* ai: claude-opus-5-5 | 2026-11-11 */}
        {view === "contacts" && (
          <button className="btn-secondary btn-sm" onClick={() => setAddingContact(true)}>
            Add contact
          </button>
        )}
      </div>

      {addingContact && (
        <AddContactModal
          onClose={() => setAddingContact(false)}
          onAdded={() => {
            setAddingContact(false);
            void loadContacts();
          }}
        />
      )}

      {view === "outreach" && (
        <OutreachQueue
          key={outreachRunId}
          contacts={contacts}
          targetIds={outreachTargetIds}
          onClearTarget={clearOutreachTarget}
          onOutreachLogged={loadContacts}
          onManageTemplates={() => setView("templates")}
        />
      )}
      {view === "templates" && <OutreachTemplateManager onDirtyChange={setTemplatesDirty} />}
      {view === "companies" && <CompaniesReport />}
      {view === "titles" && <TitlesReport onViewContactsForTitle={viewContactsForTitle} />}
      {view === "contacts" && (
        <>
          {titleFilter !== null && (
            <div style={titleFilterBar}>
              Filtered to title: <strong>{titleFilter || "(no title)"}</strong>
              <button className="btn-secondary btn-xs" onClick={() => setTitleFilter(null)}>
                Clear
              </button>
            </div>
          )}
          {importVisible && (
            <div className="card" style={{ marginBottom: "16px" }}>
              <ContactsImport onImported={loadContacts} />
            </div>
          )}

          {loadError && <div style={{ color: "var(--red)", marginBottom: "12px" }}>{loadError}</div>}

          {contacts.length > 0 && (
            <>
              <div style={toolbar}>
                <input
                  type="search"
                  placeholder="Search name, company, title, or notes"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  style={{ flex: "1 1 220px", minWidth: "180px" }}
                  aria-label="Search contacts"
                />
                <select
                  value={outreachFilter}
                  onChange={(e) => setOutreachFilter(e.target.value as OutreachFilter)}
                  aria-label="Filter by outreach status"
                  style={outreachFilter !== "all" ? filterPillActive : filterPill}
                >
                  {OUTREACH_FILTERS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <select
                  value={recruiterFilter}
                  onChange={(e) => setRecruiterFilter(e.target.value as RecruiterFilter)}
                  aria-label="Filter by recruiter"
                  style={recruiterFilter !== "all" ? filterPillActive : filterPill}
                >
                  {RECRUITER_FILTERS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                {/* ai: claude-opus-5-5 | 2026-10-26 -- multi-choice toggles (was a single-choice select) */}
                <div
                  role="group"
                  aria-label="Filter by want to contact"
                  title="Pick any combination; none picked shows everyone"
                  style={choiceGroup}
                >
                  <span>Want to contact:</span>
                  {INTERESTED_CHOICES.map((option) => {
                    const picked = interestedFilter.includes(option.value);
                    return (
                      <button
                        key={option.value}
                        type="button"
                        aria-pressed={picked}
                        onClick={() => toggleInterestedChoice(option.value)}
                        style={picked ? choicePillActive : choicePill}
                      >
                        {option.label}
                      </button>
                    );
                  })}
                </div>
                {previousEmployers.length > 0 && (
                  <select
                    value={activeCompanyFilter ?? ""}
                    onChange={(e) => setCompanyFilter(e.target.value || null)}
                    aria-label="Filter by former employer"
                    title="Show only contacts who worked at one of your previous employers (set in the Application Profile tab)"
                    style={activeCompanyFilter !== null ? filterPillActive : filterPill}
                  >
                    {/* ai: claude-sonnet-5 | 2026-10-15 -- was "Former coworkers: any
                    company", which reads as an active filter but value="" actually
                    means no company filter at all (every contact, not just former
                    coworkers) -- confirmed live, caused a non-coworker to show up
                    while the user believed this was filtering. */}
                    <option value="">Anyone from Any Company</option>
                    <option value={ANY_FORMER_EMPLOYER}>Former coworkers (any of my employers)</option>
                    {previousEmployers.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                )}
                {/* ai: claude-opus-5-5 | 2026-10-26 -- also clears the title filter bar above */}
                <button className="btn-secondary btn-sm" disabled={!hasActiveFilters} onClick={clearFilters}>
                  Clear filters
                </button>
              </div>

              <div style={{ color: "var(--text-muted)", fontSize: "12px", margin: "0 2px 8px" }}>
                {filtered.length.toLocaleString()} of {contacts.length.toLocaleString()} contacts
              </div>

              {selectedIds.size > 0 && (
                <div style={bulkBar}>
                  <strong>{selectedIds.size.toLocaleString()} selected</strong>
                  {/* ai: claude-opus-5-5 | 2026-10-21 */}
                  <button className="btn-primary btn-sm" disabled={bulkPending} onClick={messageSelected}>
                    Message selected
                  </button>
                  <select
                    value={bulkRecruiterType}
                    onChange={(e) => setBulkRecruiterType(e.target.value as RecruiterType)}
                    aria-label="Recruiter type to apply to selection"
                  >
                    {RECRUITER_TYPES.filter((option) => option.value !== "").map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <button
                    className="btn-primary btn-sm"
                    disabled={bulkPending}
                    onClick={() => bulkSetRecruiter(bulkRecruiterType)}
                  >
                    Set recruiter type
                  </button>
                  <button
                    className="btn-secondary btn-sm"
                    disabled={bulkPending}
                    onClick={() => bulkSetRecruiter(null)}
                  >
                    Not a recruiter
                  </button>
                  <button
                    className="btn-secondary btn-sm"
                    disabled={bulkPending}
                    onClick={() => setSelectedIds(new Set())}
                  >
                    Clear selection
                  </button>
                </div>
              )}

              <div className="card" style={{ padding: 0, overflow: "hidden" }}>
                <div style={scrollArea}>
                  {visible.length > 0 && (
                    <div style={{ ...rowGrid, ...headerRow, ...stickyHeader }}>
                      <span style={headerCell}>#</span>
                      <label style={headerCell}>
                        <input
                          type="checkbox"
                          checked={allFilteredSelected}
                          onChange={(e) => toggleSelectAllFiltered(e.target.checked)}
                          aria-label={`Select all ${filtered.length} filtered contacts`}
                        />
                        Select
                      </label>
                      {SORT_COLUMNS.map((column) => (
                        <button
                          key={column.key}
                          type="button"
                          onClick={() => toggleSort(column.key)}
                          style={sortHeaderCell}
                          aria-label={`Sort by ${column.label}`}
                        >
                          {column.label}
                          {sortKey === column.key && (sortDir === "asc" ? " ▲" : " ▼")}
                        </button>
                      ))}
                      <span />
                    </div>
                  )}
                  {visible.length === 0 ? (
                    <div style={{ padding: "18px", color: "var(--text-muted)" }}>
                      No contacts match these filters.
                    </div>
                  ) : (
                    visible.map((contact, index) => {
                    const state = outreachState(contact);
                    const profileUrl = safeHttpUrl(contact.profile_url);
                    const expanded = expandedId === contact.id;
                    const lastOutreach = contact.outreach.length
                      ? contact.outreach[contact.outreach.length - 1]
                      : null;
                    const lastSent = lastOutreach?.sent_at ?? null;
                    return (
                      <div key={contact.id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <div style={rowGrid}>
                          <div style={{ textAlign: "center", color: "var(--text-muted)", fontSize: "12px" }}>
                            {index + 1}
                          </div>
                          <input
                            type="checkbox"
                            checked={selectedIds.has(contact.id)}
                            onChange={(e) => toggleSelected(contact.id, e.target.checked)}
                            aria-label={`Select ${contact.full_name}`}
                          />
                          {/* ai: gpt-5 | 2026-10-03 */}
                          <div
                            style={{ fontWeight: 700, textAlign: "center" }}
                            title="Weighted outreach priority (company, role, relationship, and engagement)"
                          >
                            {contact.outreach_score?.toFixed(1) ?? "—"}
                          </div>
                          <InterestedToggle
                            value={contact.interested}
                            onChange={(next) => patchContact(contact.id, { interested: next })}
                            contactName={contact.full_name}
                          />
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 600 }}>
                              {profileUrl ? (
                                <a href={profileUrl} target="_blank" rel="noopener noreferrer">
                                  {contact.full_name}
                                </a>
                              ) : (
                                contact.full_name
                              )}
                            </div>
                            <div
                              style={subtleText}
                              title={[contact.position, contact.company_raw].filter(Boolean).join(" at ") || undefined}
                            >
                              {[contact.position, contact.company_raw].filter(Boolean).join(" at ") ||
                                "No title or company"}
                            </div>
                            {contact.notes && (
                              <div style={{ ...subtleText, fontStyle: "italic" }} title={contact.notes}>
                                {contact.notes}
                              </div>
                            )}
                          </div>
                          <select
                            value={contact.recruiter_type || ""}
                            onChange={(e) =>
                              patchContact(contact.id, {
                                recruiter_type: e.target.value === "" ? null : (e.target.value as RecruiterType),
                              })
                            }
                            aria-label={`Recruiter type for ${contact.full_name}`}
                            style={{ fontSize: "12px" }}
                          >
                            {RECRUITER_TYPES.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                          <div
                            style={{ textAlign: "center" }}
                            title={
                              lastSent
                                ? `Last sent ${formatLocalDate(lastSent)}`
                                : "No outreach logged yet"
                            }
                          >
                            {contact.outreach.length > 0 ? (
                              <span aria-label={`Contacted ${contact.full_name}`} style={{ color: "var(--green)" }}>
                                ✓
                              </span>
                            ) : (
                              <span aria-label={`Not contacted ${contact.full_name}`} style={{ color: "var(--text-muted)" }}>
                                —
                              </span>
                            )}
                          </div>
                          <div>
                            {state === "none" && <span className="badge badge-gray">Not contacted</span>}
                            {state === "awaiting" && lastOutreach && (
                              <span className="badge badge-orange">
                                {CHANNEL_LABELS[lastOutreach.channel]} {formatLocalDate(lastSent)}
                                {contact.outreach.length > 1 ? ` (${contact.outreach.length}x)` : ""} · no reply
                              </span>
                            )}
                            {state === "replied" && (
                              <span className="badge badge-green">
                                Replied
                                {contact.outreach.length > 1 ? ` (${contact.outreach.length} sent)` : ""}
                              </span>
                            )}
                          </div>
                          {/* ai: claude-opus-5-5 | 2026-10-21 -- added Message */}
                          <div style={{ display: "flex", gap: "6px" }}>
                            <button
                              className="btn-secondary btn-xs"
                              disabled={!profileUrl}
                              onClick={() => messageContacts([contact.id])}
                              title={profileUrl ? "Write a LinkedIn message to this contact" : "No LinkedIn profile URL"}
                              aria-label={`Message ${contact.full_name}`}
                            >
                              Message
                            </button>
                            <button
                              className="btn-secondary btn-xs"
                              onClick={() => setExpandedId(expanded ? null : contact.id)}
                              aria-expanded={expanded}
                            >
                              {expanded ? "Close" : "Details"}
                            </button>
                          </div>
                        </div>
                        {expanded && (
                          <ContactDetail
                            contact={contact}
                            onSaveEmailAddress={saveEmailAddress}
                            onSaveNotes={saveNotes}
                            onLogOutreach={logOutreach}
                            onSetResponse={setResponse}
                            onDeleteOutreach={deleteOutreach}
                            onSavePhone={savePhone}
                            onSavePersonalEmail={savePersonalEmail}
                            onSetKeepInformed={setKeepInformed}
                            allContacts={contacts}
                          />
                        )}
                      </div>
                    );
                  })
                )}
                </div>
              </div>

              {filtered.length > visibleCount && (
                <div style={{ textAlign: "center", marginTop: "12px" }}>
                  <button
                    className="btn-secondary btn-sm"
                    onClick={() => setVisibleCount((count) => count + PAGE_SIZE)}
                  >
                    Show {Math.min(PAGE_SIZE, filtered.length - visibleCount)} more
                  </button>
                </div>
              )}
            </>
          )}

          {loading && <div style={{ color: "var(--text-muted)" }}>Loading contacts…</div>}
        </>
      )}
    </div>
  );
}

const titleStyle: CSSProperties = {
  fontSize: "18px",
  fontWeight: 700,
  lineHeight: 1.2,
};

const summaryStyle: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
  marginTop: "2px",
  maxWidth: "640px",
};

const toolbar: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "10px",
  marginBottom: "10px",
};

// ai: claude-sonnet-5 | 2026-10-14
// index.css's global `select { width: 100% }` rule (meant for form fields)
// was stretching these filter dropdowns to the full toolbar width even
// though their option text is short. Pill-shaped and width:auto (sized to
// the selected option's text, like a job board's filter chips) rather than
// the page-wide default. filterPillActive highlights a filter that's
// narrowed the list away from "all", the same way job boards tint an
// active filter chip.
const filterPill: CSSProperties = {
  width: "auto",
  flex: "0 0 auto",
  padding: "6px 28px 6px 14px",
  fontSize: "12.5px",
  fontWeight: 500,
  borderRadius: "999px",
  // ai: claude-opus-5-5 | 2026-10-26 -- longhand, not `border`: filterPillActive
  // overrides borderColor, and React warns (and can drop the color) when a
  // rerender mixes the shorthand with its longhand.
  borderWidth: "1.5px",
  borderStyle: "solid",
  borderColor: "var(--border)",
  background: "var(--surface2)",
  color: "var(--text-muted)",
  cursor: "pointer",
};

const filterPillActive: CSSProperties = {
  ...filterPill,
  borderColor: "var(--accent)",
  background: "var(--accent-light)",
  color: "var(--accent)",
  fontWeight: 600,
};

// ai: claude-opus-5-5 | 2026-10-26
// Toggle buttons for a multi-choice filter: filterPill without the right
// padding a select's dropdown arrow needs.
const choicePill: CSSProperties = { ...filterPill, padding: "6px 14px", fontFamily: "inherit" };
const choicePillActive: CSSProperties = { ...filterPillActive, padding: "6px 14px", fontFamily: "inherit" };

const choiceGroup: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "4px",
  fontSize: "12.5px",
  color: "var(--text-muted)",
};

const rowGrid: CSSProperties = {
  display: "grid",
  // ai: claude-opus-5-5 | 2026-10-21 -- last column widened for the Message button
  gridTemplateColumns: "36px 64px 72px 90px minmax(0, 1fr) 150px 90px 230px 140px",
  alignItems: "center",
  gap: "12px",
  padding: "10px 14px",
};

// ai: claude-sonnet-5 | 2026-10-02
// Keeps the header row fixed at the top of its own scroll container while
// the contact rows scroll underneath it. Needs a solid background (inherited
// from headerRow) so scrolled-under rows don't show through.
const scrollArea: CSSProperties = {
  maxHeight: "65vh",
  overflowY: "auto",
  overflowX: "hidden",
};

const stickyHeader: CSSProperties = {
  position: "sticky",
  top: 0,
  zIndex: 1,
};

const bulkBar: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
  padding: "8px 14px",
  marginBottom: "10px",
  background: "var(--accent-light)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  fontSize: "13px",
};

const titleFilterBar: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
  padding: "8px 14px",
  marginBottom: "10px",
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  fontSize: "13px",
};

const headerRow: CSSProperties = {
  background: "var(--surface2)",
  borderBottom: "1px solid var(--border)",
  padding: "6px 14px",
};

const headerCell: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "6px",
  fontSize: "11px",
  fontWeight: 700,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};

// ai: claude-sonnet-5 | 2026-10-02 — sortable column header, styled to match
// headerCell but as a real <button> so it's keyboard/focus accessible.
const sortHeaderCell: CSSProperties = {
  ...headerCell,
  background: "none",
  border: "none",
  padding: 0,
  margin: 0,
  cursor: "pointer",
  textAlign: "left",
  fontFamily: "inherit",
};

const subtleText: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

// ai: claude-sonnet-5 | 2026-10-13
const interestedDotStyle: CSSProperties = {
  width: "20px",
  height: "20px",
  borderRadius: "50%",
  border: "1.5px solid",
  fontSize: "10px",
  fontWeight: 700,
  lineHeight: 1,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  cursor: "pointer",
  padding: 0,
  flexShrink: 0,
};
