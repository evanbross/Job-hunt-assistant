/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// The rich-text notes editor (TipTap) for recruiter lead tracking. What it emits is
// sanitized by the server on save (backend/utils/rich_text.py); initial content is
// sanitized here before it is loaded. Display is RichTextView.tsx.
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { SAFE_LINK_RE, sanitizeRichText } from "../../utils/richText.ts";

interface RichTextEditorProps {
  /** Initial content. The editor owns its state afterwards; remount (key) to reset it. */
  initialHtml: string | null | undefined;
  onChange: (html: string) => void;
  placeholder?: string;
  ariaLabel: string;
}

interface ToolbarButton {
  label: string;
  title: string;
  isActive: () => boolean;
  run: () => void;
}

export default function RichTextEditor({ initialHtml, onChange, placeholder, ariaLabel }: RichTextEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3] },
        link: { openOnClick: false, autolink: true, defaultProtocol: "https" },
      }),
    ],
    content: sanitizeRichText(initialHtml || ""),
    editorProps: {
      attributes: {
        class: "rich-text rich-text-editor-surface",
        "aria-label": ariaLabel,
        role: "textbox",
        "aria-multiline": "true",
        ...(placeholder ? { "data-placeholder": placeholder } : {}),
      },
    },
    shouldRerenderOnTransaction: true,
    onUpdate: ({ editor: current }) => onChange(current.getHTML()),
  });

  if (!editor) return null;

  const setLink = () => {
    const previous = editor.getAttributes("link").href as string | undefined;
    const url = window.prompt("Link address (leave empty to remove the link)", previous || "https://");
    if (url === null) return;
    if (url.trim() === "") {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
      return;
    }
    if (!SAFE_LINK_RE.test(url.trim())) {
      window.alert("Links must start with http://, https:// or mailto:");
      return;
    }
    editor.chain().focus().extendMarkRange("link").setLink({ href: url.trim() }).run();
  };

  const buttons: ToolbarButton[] = [
    { label: "B", title: "Bold", isActive: () => editor.isActive("bold"), run: () => editor.chain().focus().toggleBold().run() },
    { label: "I", title: "Italic", isActive: () => editor.isActive("italic"), run: () => editor.chain().focus().toggleItalic().run() },
    { label: "H", title: "Heading", isActive: () => editor.isActive("heading", { level: 3 }), run: () => editor.chain().focus().toggleHeading({ level: 3 }).run() },
    { label: "•", title: "Bulleted list", isActive: () => editor.isActive("bulletList"), run: () => editor.chain().focus().toggleBulletList().run() },
    { label: "1.", title: "Numbered list", isActive: () => editor.isActive("orderedList"), run: () => editor.chain().focus().toggleOrderedList().run() },
    { label: "❝", title: "Quote", isActive: () => editor.isActive("blockquote"), run: () => editor.chain().focus().toggleBlockquote().run() },
    { label: "Link", title: "Add or remove a link", isActive: () => editor.isActive("link"), run: setLink },
  ];

  return (
    <div className="rich-text-editor">
      <div className="rich-text-toolbar" role="toolbar" aria-label={`${ariaLabel} formatting`}>
        {buttons.map((button) => (
          <button
            key={button.title}
            type="button"
            title={button.title}
            aria-label={button.title}
            aria-pressed={button.isActive()}
            className={button.isActive() ? "rich-text-tool active" : "rich-text-tool"}
            onMouseDown={(event) => event.preventDefault()}
            onClick={button.run}
          >
            {button.label}
          </button>
        ))}
      </div>
      <EditorContent editor={editor} />
    </div>
  );
}
