/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// The hosted service's Terms of Service, public at /terms. New accounts accept them when
// they register, and existing accounts when the version changes
// (backend/security/terms_of_service.py records which version and when). Changing
// anything of substance here means bumping TERMS_VERSION in publicSite.ts and the backend.
// AcceptTermsPage renders the same text through TermsOfServiceText.
import type { CSSProperties, ReactNode } from "react";
import PublicPageShell from "./PublicPageShell.tsx";
import {
  APP_NAME,
  COMPANY_NAME,
  GOVERNING_LAW_STATE,
  PRIVACY_CONTACT_EMAIL,
  PRIVACY_PATH,
  formatTermsDate,
} from "./publicSite.ts";

const styles: Record<string, CSSProperties> = {
  h1: { fontSize: "26px", fontWeight: 800, margin: "0 0 6px" },
  updated: { fontSize: "13px", color: "var(--text-muted)", margin: "0 0 24px" },
  h2: { fontSize: "18px", fontWeight: 700, margin: "30px 0 10px" },
  p: { fontSize: "14px", lineHeight: 1.65, margin: "0 0 12px" },
  list: { fontSize: "14px", lineHeight: 1.65, margin: "0 0 12px", paddingLeft: "22px" },
  caps: { fontSize: "13px", lineHeight: 1.6, margin: "0 0 12px", textTransform: "uppercase" },
};

function H2({ children }: { children: ReactNode }) {
  return <h2 style={styles.h2}>{children}</h2>;
}
function P({ children }: { children: ReactNode }) {
  return <p style={styles.p}>{children}</p>;
}
function UL({ children }: { children: ReactNode }) {
  return <ul style={styles.list}>{children}</ul>;
}

const contact = <a href={`mailto:${PRIVACY_CONTACT_EMAIL}`}>{PRIVACY_CONTACT_EMAIL}</a>;
const privacyPolicy = <a href={PRIVACY_PATH}>Privacy Policy</a>;

/** The terms' heading and body, without the page around them. */
export function TermsOfServiceText() {
  return (
    <>
      <h1 style={styles.h1}>Terms of Service</h1>
      <p style={styles.updated}>Last updated {formatTermsDate()}</p>

      <P>
        These terms are an agreement between you and {COMPANY_NAME} (“we”, “us”) for your use of{" "}
        {APP_NAME} (“the service”): the website, the app, and its browser extension. By creating
        an account or using the service you agree to them. Our {privacyPolicy} explains how we
        handle your data, and is part of these terms. If you do not agree, do not use the service.
      </P>

      <H2>1. Who can use it</H2>
      <P>
        You must be at least 16 years old. If you are under the age of majority where you live,
        your parent or guardian must agree to these terms for you. The service is currently
        available by invitation only.
      </P>

      <H2>2. Your account</H2>
      <UL>
        <li>Give accurate information when you register, and keep your email address current.</li>
        <li>
          Keep your password, and the tokens that connect the browser extension, private. You
          are responsible for what happens under your account.
        </li>
        <li>An account is for one person. Do not share it or sell it.</li>
        <li>
          Tell us at {contact} if you think someone else has gotten into your account.
        </li>
      </UL>

      <H2>3. The service</H2>
      <P>
        {APP_NAME} helps you run a job search: it collects job listings from alert emails, web
        pages you capture and links you give it; tracks your applications; and, with an AI
        provider you set up, scores listings and drafts cover letters and messages. The service
        is in an early stage. Features will change, some may be removed, and it may sometimes be
        unavailable. We do not promise that it will be available at any particular time, or that
        it will be free of errors.
      </P>
      <P>
        The service is free at the moment. If we start charging, we will tell you at least 30
        days beforehand, and you will not be charged unless you agree to pay.
      </P>

      <H2>4. Your content</H2>
      <P>
        You keep ownership of everything you put into the service: your résumé, profile,
        documents, notes, contacts and the rest. You give us permission to store, copy, process
        and display it only as needed to run the service for you, including sending it to the
        AI provider you choose when you use an AI feature. That permission ends when the content
        is deleted, apart from copies in backups, which expire as the {privacyPolicy} describes.
      </P>
      <P>
        You are responsible for your content, and for having the right to use it. For example,
        only import contacts you are allowed to share with a service like this one.
      </P>

      <H2>5. Other services you connect</H2>
      <UL>
        <li>
          <strong>Google.</strong> Connecting Gmail is optional and governed by the{" "}
          {privacyPolicy}. You can disconnect at any time.
        </li>
        <li>
          <strong>AI providers.</strong> AI features use your own account and API key with the
          provider you choose (for example Anthropic or OpenAI). Your agreement with that
          provider governs its use of your content, and you are responsible for any charges on
          your key.
        </li>
        <li>
          <strong>Job sites and employers.</strong> The browser extension works in your own
          browser, on pages you open and are signed in to, and the service fetches job postings
          at links you give it. You are responsible for using those sites within their own
          terms. We are not affiliated with any job board or employer, and are not responsible
          for their sites, their listings or their hiring decisions.
        </li>
      </UL>

      <H2>6. AI output and job applications</H2>
      <P>
        AI-generated text, such as cover letters, messages, fit scores and the job details
        extracted from emails, can be wrong or incomplete. Read and check it before you rely on
        it or send it to anyone. When the extension fills in an application form, check what it
        entered before you submit: what you send to an employer is your responsibility.
      </P>
      <P>
        Job listings come from third parties, and we do not verify them. The service cannot
        promise you interviews, offers or any other result.
      </P>

      <H2>7. Acceptable use</H2>
      <P>Do not use the service to:</P>
      <UL>
        <li>break the law, or infringe anyone else’s rights;</li>
        <li>send spam, or harass or mislead the people you contact;</li>
        <li>
          get into accounts or data that are not yours, probe or break the service’s security,
          or get around its usage limits;
        </li>
        <li>
          overload the service, or reach it by automated means other than the app and its
          extension;
        </li>
        <li>
          copy, resell or rent out the service, or reverse engineer it except where the law
          allows that whatever these terms say;
        </li>
        <li>upload malware, or anything you do not have the right to share.</li>
      </UL>

      <H2>8. Our software</H2>
      <P>
        The service and the browser extension, including their code and design, belong to{" "}
        {COMPANY_NAME}. While you have an account, we give you a personal, non-exclusive,
        non-transferable license to use them under these terms. If you send us ideas or
        feedback, we may use them without owing you anything.
      </P>

      <H2>9. Ending your use</H2>
      <P>
        You can stop using the service at any time, and delete your account from Settings (see
        the {privacyPolicy} for what happens to your data). We may suspend or close your account
        if you break these terms, if your use puts the service or other users at risk, or if
        the law requires it. Unless the situation makes it impossible, we will tell you first
        and give you a chance to download your data. If we shut the service down, we will give
        you at least 30 days’ notice so you can download your data. Sections 4 to 6 and 10 to
        14 continue to apply after your use ends.
      </P>

      <H2>10. Disclaimer</H2>
      <p style={styles.caps}>
        The service is provided “as is” and “as available”. To the extent the law allows, we
        disclaim all warranties, express or implied, including merchantability, fitness for a
        particular purpose, title and non-infringement.
      </p>

      <H2>11. Limit on our liability</H2>
      <p style={styles.caps}>
        To the extent the law allows, we are not liable for any indirect, incidental, special,
        consequential or punitive damages, or for lost profits, lost data or lost opportunities,
        arising from these terms or the service. Our total liability for all claims about the
        service is limited to the greater of the amount you paid us for it in the 12 months
        before the claim, and US$100.
      </p>
      <P>
        Some places do not allow some of these exclusions or limits; where that is so, they
        apply to you only as far as the law there allows.
      </P>

      <H2>12. Your responsibility to us</H2>
      <P>
        If someone makes a claim against us because of your content, or because you broke these
        terms or the law, you will cover our reasonable costs of dealing with it, including
        legal fees.
      </P>

      <H2>13. Law and disputes</H2>
      <P>
        These terms are governed by the laws of the State of {GOVERNING_LAW_STATE}, USA, without
        regard to its conflict-of-laws rules. Any dispute about them or the service will be
        heard only in the state or federal courts located in {GOVERNING_LAW_STATE}, and you and
        we both agree to those courts’ jurisdiction. Either of us may still bring a claim in a
        small-claims court that has jurisdiction. Nothing in this section takes away protections
        that the law where you live gives you and that cannot be waived by contract.
      </P>
      <P>Before going to court, please email {contact} first so we can try to sort it out.</P>

      <H2>14. Changes to these terms</H2>
      <P>
        We may update these terms. We will post the new version here with a new date. If a
        change is material, we will email you at least 30 days before it takes effect, and ask
        you to accept the new terms in the app. If you do not accept them, you can download your
        data and close your account.
      </P>

      <H2>15. General</H2>
      <UL>
        <li>
          These terms and the {privacyPolicy} are the whole agreement between you and us about
          the service.
        </li>
        <li>
          If a court finds part of these terms unenforceable, the rest still applies. If we do
          not enforce a term straight away, we have not given up the right to enforce it later.
        </li>
        <li>
          You may not transfer these terms to anyone else. We may transfer them as part of a
          merger, acquisition or sale of the service, and will tell you if we do.
        </li>
        <li>
          We send notices to your account’s email address. Send notices to us at {contact}.
        </li>
      </UL>

      <H2>Contact</H2>
      <P>
        {COMPANY_NAME}, {contact}.
      </P>
    </>
  );
}

export default function TermsOfServicePage() {
  return (
    <PublicPageShell>
      <TermsOfServiceText />
    </PublicPageShell>
  );
}
