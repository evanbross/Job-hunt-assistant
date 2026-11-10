/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// The hosted service's privacy policy, public at /privacy. Every statement here is a
// claim about what the code does; Google's OAuth review checks the Gmail section
// against the app's behaviour. Where it depends on code, the place is noted beside it.
// If that code changes, change this page and PRIVACY_POLICY_UPDATED with it.
import type { CSSProperties, ReactNode } from "react";
import PublicPageShell from "./PublicPageShell.tsx";
import {
  APP_NAME,
  COMPANY_NAME,
  GOOGLE_PERMISSIONS_URL,
  GOOGLE_USER_DATA_POLICY_URL,
  PRIVACY_CONTACT_EMAIL,
  PRIVACY_POLICY_UPDATED,
} from "./publicSite.ts";

const styles: Record<string, CSSProperties> = {
  h1: { fontSize: "26px", fontWeight: 800, margin: "0 0 6px" },
  updated: { fontSize: "13px", color: "var(--text-muted)", margin: "0 0 24px" },
  h2: { fontSize: "18px", fontWeight: 700, margin: "30px 0 10px" },
  h3: { fontSize: "15px", fontWeight: 700, margin: "18px 0 8px" },
  p: { fontSize: "14px", lineHeight: 1.65, margin: "0 0 12px" },
  list: { fontSize: "14px", lineHeight: 1.65, margin: "0 0 12px", paddingLeft: "22px" },
  callout: {
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderLeft: "4px solid var(--accent)",
    borderRadius: "var(--radius)",
    padding: "12px 16px",
    margin: "0 0 14px",
  },
};

function H2({ children }: { children: ReactNode }) {
  return <h2 style={styles.h2}>{children}</h2>;
}
function H3({ children }: { children: ReactNode }) {
  return <h3 style={styles.h3}>{children}</h3>;
}
function P({ children }: { children: ReactNode }) {
  return <p style={styles.p}>{children}</p>;
}
function UL({ children }: { children: ReactNode }) {
  return <ul style={styles.list}>{children}</ul>;
}

const contact = <a href={`mailto:${PRIVACY_CONTACT_EMAIL}`}>{PRIVACY_CONTACT_EMAIL}</a>;

export default function PrivacyPolicyPage() {
  return (
    <PublicPageShell>
      <h1 style={styles.h1}>Privacy policy</h1>
      <p style={styles.updated}>Last updated {PRIVACY_POLICY_UPDATED}</p>

      <P>
        This policy explains what {APP_NAME} (“the service”, “we”) collects, why, who it is
        shared with, how long it is kept, and the choices you have. The service is operated by{" "}
        {COMPANY_NAME}. Questions and requests: {contact}.
      </P>

      <H2>The short version</H2>
      <UL>
        <li>We collect what you put into the service and what it needs to run, nothing more.</li>
        <li>
          If you connect Gmail, the service reads only messages from the job-alert senders you
          choose. It never sends or deletes email.
        </li>
        <li>
          We do not sell your data, show you ads, or use your data, including your Gmail data, to
          train AI models.
        </li>
        <li>AI features run on the AI provider you choose, with your own API key.</li>
        <li>You can download all your data, or delete your account, from Settings at any time.</li>
      </UL>

      <H2>What we collect</H2>
      <H3>Your account</H3>
      <P>
        Your email address, an optional name, and your password, which is stored only as a salted
        hash that cannot be turned back into the password.
      </P>

      <H3>What you add to the service</H3>
      <UL>
        <li>job listings, the applications you track and their stages, notes and history;</li>
        <li>your résumé, your candidate profile, cover letters and other documents you create;</li>
        <li>
          contacts you import (for example from a LinkedIn connections export) and outreach
          messages you draft;
        </li>
        <li>
          your application profile, used to fill in job applications for you: contact details,
          work authorization, links and the answers you save. It can also hold the voluntary
          self-identification answers employers ask for (gender, race or ethnicity, veteran
          status, disability). Those fields are optional: fill them in only if you want the
          service to enter them on applications for you;
        </li>
        <li>your settings, including the API keys for the AI provider you use.</li>
      </UL>

      <H3>The browser extension</H3>
      <P>
        When you capture a job with the extension, or it opens a job page the service has queued
        for you, it sends that page's job posting (title, company, description, location, link).
        When you use it to fill in an application, it reads your application profile from the
        service and enters it in the form; what you submit goes to that employer, not to us. It
        does not collect your browsing history.
      </P>

      <H3>Technical data</H3>
      <UL>
        <li>
          a security log of account events (sign-ins, password and email changes, connecting or
          disconnecting Google), with the IP address each came from;
        </li>
        <li>
          server logs, which record requests and errors and include IP addresses and, in some
          error messages, email addresses;
        </li>
        <li>a record of failed background tasks, so that problems can be diagnosed.</li>
      </UL>

      <H2>Gmail and your Google data</H2>
      <div style={styles.callout}>
        <p style={{ ...styles.p, margin: 0 }}>
          {APP_NAME}'s use and transfer to any other app of information received from Google APIs
          will adhere to the{" "}
          <a href={GOOGLE_USER_DATA_POLICY_URL}>Google API Services User Data Policy</a>, including
          the Limited Use requirements.
        </p>
      </div>
      <P>
        Connecting Gmail is optional. If you connect it, the service asks Google for one
        permission, to read, compose and modify your email (the <code>gmail.modify</code> scope),
        and uses it only for the job-alert feature you set up:
      </P>
      <UL>
        <li>
          <strong>What it reads:</strong> messages in your inbox from the senders you list as job
          alert sources, within the time window you set. If you have no sources set up, it reads
          nothing.
        </li>
        <li>
          <strong>What it keeps:</strong> for each of those messages, the sender, subject, date and
          text, plus the job listings found in it, so you can review them and so the same alert
          is not processed twice.
        </li>
        <li>
          <strong>What it changes:</strong> it marks an alert as read once it has been handled,
          and, only if you turn on that setting, moves processed alerts out of your inbox into a
          label you name.
        </li>
        <li>
          <strong>What it never does:</strong> send email from your account, delete email, or read
          messages from anyone not on your list of sources.
        </li>
      </UL>
      <P>Your Google data:</P>
      <UL>
        <li>is used only to provide the job-alert features you can see and use in the service;</li>
        <li>is not sold, and is not used or transferred for advertising;</li>
        <li>is not used to develop, improve or train generalized AI or machine-learning models;</li>
        <li>
          is not read by people, except with your permission (for example when you ask for help
          with a specific message), when needed for security or to investigate abuse, to comply
          with the law, or after it has been aggregated and anonymized;
        </li>
        <li>
          is shared with one third party only: the AI provider you choose, as described below, so
          that it can extract the job listings from your alert emails.
        </li>
      </UL>
      <P>
        The access token Google issues is stored encrypted. Disconnecting Google in the service
        revokes its access with Google and deletes the token; you can also remove access at any
        time in your <a href={GOOGLE_PERMISSIONS_URL}>Google account's permissions</a>. Jobs
        already created from your alerts stay in your account until you delete them or the
        account.
      </P>

      <H2>AI providers</H2>
      <P>
        AI features (extracting jobs from alert emails, scoring how well a job fits you, drafting
        cover letters and outreach messages, and answering questions about your data) send the
        relevant content to the AI provider you choose in Settings: Anthropic, OpenAI, or an
        Ollama server you run yourself. That content can include alert emails, job descriptions,
        your résumé and candidate profile, and contacts' names and job titles. It is sent with
        your own API key, under your own agreement with that provider, whose terms and privacy
        policy govern what happens to it there. We do not use a shared key, and nothing is sent to
        an AI provider until you set one up.
      </P>

      <H2>How we use your data</H2>
      <P>
        To provide the service's features to you; to send the emails the service needs to (account
        verification, password resets and, if you turn them on, job-scan reports to your own
        address); to keep the service secure and prevent abuse; and to find and fix problems. We
        do not use your data for advertising, sell it, or build profiles of you for anyone else.
      </P>

      <H2>Who else handles your data</H2>
      <P>We use these service providers, only to run the service:</P>
      <UL>
        <li>a cloud hosting provider, for the servers that run the service;</li>
        <li>Neon, for the database, hosted in the United States;</li>
        <li>Cloudflare, for storing encrypted database backups;</li>
        <li>Forward Email, to deliver the service's account emails;</li>
        <li>
          Sentry, to report errors. Before an error report leaves the server, email addresses and
          access tokens are removed and long text is cut short;
        </li>
        <li>Google, when you connect Gmail, and the AI provider you choose, as described above.</li>
      </UL>
      <P>
        We may also disclose data if the law requires it, or to protect the rights and safety of
        users or the service.
      </P>

      <H2>Security</H2>
      <UL>
        <li>All connections to the service use HTTPS.</li>
        <li>
          Passwords are stored as salted hashes. Google tokens, AI provider keys and uploaded
          résumés are encrypted in the database.
        </li>
        <li>
          Each account's data is kept separate by the database itself, not only by the
          application's code.
        </li>
        <li>Backups are encrypted before they leave the server.</li>
      </UL>
      <P>
        No system is perfectly secure. If a breach affects your data, we will tell you without
        undue delay.
      </P>

      <H2>How long we keep it</H2>
      <UL>
        <li>Your account data: until you delete it or your account.</li>
        <li>
          After you ask to delete your account: 30 days, during which you can cancel. Then
          everything in the account is permanently deleted, and the service's Google access is
          revoked.
        </li>
        <li>Encrypted backups: up to 35 days, after which deleted data is gone from them too.</li>
        <li>The account security log: 1 year.</li>
        <li>Records of failed background tasks, scans and health checks: 90 to 180 days.</li>
        <li>Server logs: overwritten as new logs are written, normally within days.</li>
      </UL>

      <H2>Your choices and rights</H2>
      <UL>
        <li>
          <strong>Access and download:</strong> Settings, then Account, then <em>Download my
          data</em> gives you everything stored for your account as one file.
        </li>
        <li>
          <strong>Correct:</strong> you can edit or delete what you've added at any time.
        </li>
        <li>
          <strong>Delete:</strong> Settings, then Account, then <em>Delete my account</em>.
        </li>
        <li>
          <strong>Disconnect Google:</strong> from the Google menu in the app, or from your{" "}
          <a href={GOOGLE_PERMISSIONS_URL}>Google account</a>.
        </li>
        <li>
          <strong>Anything else:</strong> email {contact}. Depending on where you live, you may
          have further rights, such as to object to or restrict processing; we will honor those
          requests as the law requires.
        </li>
      </UL>

      <H2>Cookies and browser storage</H2>
      <P>
        The service sets one cookie, which keeps you signed in. It also saves a few display
        preferences, such as how a list is sorted, in your browser. There are no advertising or
        analytics cookies, and no third-party trackers.
      </P>

      <H2>Children</H2>
      <P>
        The service is for people looking for work and is not directed at children under 16. We
        do not knowingly collect data from them; if you believe a child has given us data, contact{" "}
        {contact} and we will delete it.
      </P>

      <H2>Where your data is stored</H2>
      <P>
        Your data is stored in the United States. If you use the service from elsewhere, your data
        is transferred to and processed there.
      </P>

      <H2>Changes to this policy</H2>
      <P>
        We will post any change here and update the date at the top. If a change materially
        affects how your data is used, we will email you before it takes effect.
      </P>

      <H2>Contact</H2>
      <P>
        {COMPANY_NAME}, {contact}.
      </P>
    </PublicPageShell>
  );
}
