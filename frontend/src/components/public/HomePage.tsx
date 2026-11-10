/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// The hosted service's public homepage: what the app does and why it asks for Gmail
// access, with a link to the privacy policy. Google's OAuth verification requires
// such a page, reachable without signing in. App.tsx shows it to logged-out visitors
// at "/" when hosted; the desktop app goes straight to sign-in.
import type { CSSProperties } from "react";
import PublicPageShell from "./PublicPageShell.tsx";
import { APP_NAME, LOGIN_PATH, PRIVACY_PATH } from "./publicSite.ts";

const styles: Record<string, CSSProperties> = {
  hero: { textAlign: "center", padding: "24px 0 8px" },
  title: { fontSize: "30px", fontWeight: 800, margin: "0 0 10px" },
  tagline: { fontSize: "16px", color: "var(--text-muted)", lineHeight: 1.55, margin: "0 auto 22px", maxWidth: "560px" },
  beta: {
    background: "var(--accent-light)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    padding: "12px 16px",
    fontSize: "13.5px",
    lineHeight: 1.5,
    margin: "28px 0",
  },
  h2: { fontSize: "18px", fontWeight: 700, margin: "32px 0 12px" },
  features: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "14px" },
  card: {
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    padding: "14px 16px",
  },
  cardTitle: { fontWeight: 700, fontSize: "14px", marginBottom: "6px" },
  cardText: { fontSize: "13.5px", lineHeight: 1.5, color: "var(--text-muted)", margin: 0 },
  p: { fontSize: "14px", lineHeight: 1.6, margin: "0 0 10px" },
  list: { fontSize: "14px", lineHeight: 1.6, margin: "0 0 10px", paddingLeft: "22px" },
};

const FEATURES: { title: string; text: string }[] = [
  {
    title: "Job leads from your inbox",
    text:
      "Connect Gmail and choose which senders count as job alerts (LinkedIn, Indeed, a recruiter). " +
      "New alerts become jobs in one queue, with duplicates merged.",
  },
  {
    title: "Capture while you browse",
    text: "The browser extension saves the posting you're looking at, from job boards and company career sites.",
  },
  {
    title: "Fit scoring",
    text: "Each job is scored against your own candidate profile, so the best matches rise to the top.",
  },
  {
    title: "Cover letters you control",
    text: "Draft a cover letter for a job from your profile and résumé, edit it, and download it as Word or PDF.",
  },
  {
    title: "Application tracking",
    text: "Follow every application through its stages, from applied to offer, with dates and notes.",
  },
  {
    title: "Your AI provider, your key",
    text:
      "AI features run on the provider you choose (Anthropic, OpenAI, or your own Ollama server) " +
      "under your own API key and account.",
  },
];

export default function HomePage() {
  return (
    <PublicPageShell>
      <section style={styles.hero}>
        <h1 style={styles.title}>{APP_NAME}</h1>
        <p style={styles.tagline}>
          Your job search in one place: leads from your job-alert emails and the boards you
          browse, scored against your profile, with every application tracked to the end.
        </p>
        <a href={LOGIN_PATH} className="btn-primary" style={{ textDecoration: "none", padding: "10px 22px" }}>
          Sign in
        </a>
      </section>

      <div style={styles.beta}>
        <strong>Invite-only beta.</strong> {APP_NAME} is open to invited testers. If you've been
        invited, sign in and create your account with the address your invitation went to.
      </div>

      <h2 style={styles.h2}>What it does</h2>
      <div style={styles.features}>
        {FEATURES.map((feature) => (
          <div key={feature.title} style={styles.card}>
            <div style={styles.cardTitle}>{feature.title}</div>
            <p style={styles.cardText}>{feature.text}</p>
          </div>
        ))}
      </div>

      <h2 style={styles.h2}>How it uses your Gmail</h2>
      <p style={styles.p}>
        Connecting Gmail is optional. If you connect it, {APP_NAME} asks Google for permission to
        read and modify your mail, and uses it only to:
      </p>
      <ul style={styles.list}>
        <li>read messages from the job-alert senders you choose, and no others;</li>
        <li>turn those messages into job leads in your queue;</li>
        <li>
          mark alerts as read once they've been handled, and, only if you turn it on, move
          processed alerts out of your inbox into a label you name.
        </li>
      </ul>
      <p style={styles.p}>
        It never sends or deletes your email. Your Gmail data is never sold, never used for
        advertising, and never used to train AI models. You can disconnect at any time, which
        revokes the app's access. The <a href={PRIVACY_PATH}>privacy policy</a> has the details.
      </p>
    </PublicPageShell>
  );
}
