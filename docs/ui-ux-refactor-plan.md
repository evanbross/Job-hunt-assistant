# UI/UX Refactor Plan - Job Hunt Assistant

## Overview
This document outlines the strategy for improving UI/UX consistency across the Job Hunt Assistant frontend. The goal is to eliminate "component debt," standardize layout patterns, and unify status feedback mechanisms.

## Phase 1: Style & Layout Standardization
- **Global CSS Refactor:** Move redundant inline styles (backdrops, panels, headers) from components into `frontend/src/index.css`.
- **Atomic Components:**
    - `Modal`: A single wrapper for all dialogs and popups.
    - `StatusBadge`: A unified component for rendering job and application statuses.
    - `MetricGrid`: A standardized way to show counts and results (e.g., in scan summaries).

## Phase 2: Component Migration
- **Dialog Consolidation:** Migrate the following to the new `Modal` component:
    - `ProgressPopup` (JobQueue)
    - `ScanSummaryPopup` (JobQueue)
    - `ListenerImportDialog` (App)
    - `FixCompaniesDialog` (JobQueue)
    - `ManualEvalDialog` (JobQueue)
    - `DescriptionModal` (TriagePanel)
- **Search & Filter:** Standardize search input styling across all panels.

## Phase 3: Brand & Status Consistency
- **Color Tokens:** Define a central mapping of sources (LinkedIn, Greenhouse, etc.) and fit scores to specific colors.
- **Typography:** Ensure font sizes and weights are consistent across headers, subheaders, and body text.

## Phase 4: Code Cleanup
- **Extraction:** Move large sub-components (like `JobCard`, `TriageCard`) into their own files in `frontend/src/components/`.
- **Dead Code Removal:** Remove unused inline styles and legacy CSS classes.

## Success Criteria
- No redundant backdrop/panel style definitions in individual components.
- All dialogs respond to `Esc` and "Click Outside" consistently.
- Visual styles for "Applied," "Rejected," etc., are identical across the entire app.
