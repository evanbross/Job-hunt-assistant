import React, { useEffect, useRef, useState } from "react";

export default function ResumeUpload() {
  const [resume, setResume] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef(null);

  useEffect(() => {
    fetch("/api/resume")
      .then((r) => r.json())
      .then((data) => setResume(data))
      .catch(() => {});
  }, []);

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF files are accepted.");
      return;
    }

    setError("");
    setUploading(true);
    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch("/api/resume", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Upload failed");
        return;
      }
      setResume(data);
    } catch (e) {
      setError("Upload failed: " + e.message);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div>
      <h3 style={sectionTitle}>Resume</h3>

      {resume ? (
        <div style={infoBox}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "24px" }}>📄</span>
            <div>
              <div style={{ fontWeight: "600" }}>{resume.filename}</div>
              <div style={{ color: "var(--text-muted)", fontSize: "12px" }}>
                Uploaded {resume.uploaded_at?.slice(0, 19).replace("T", " ")} ·{" "}
                {resume.char_count?.toLocaleString()} characters extracted
              </div>
            </div>
          </div>
        </div>
      ) : (
        <p style={{ color: "var(--text-muted)", fontSize: "13px", marginBottom: "12px" }}>
          No resume uploaded. Fit scoring will work without one but will be less accurate.
        </p>
      )}

      <div style={{ marginTop: "12px", display: "flex", alignItems: "center", gap: "10px" }}>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf"
          onChange={handleFileChange}
          style={{ display: "none" }}
          id="resume-file-input"
        />
        <label
          htmlFor="resume-file-input"
          className="btn-secondary"
          style={{
            cursor: uploading ? "not-allowed" : "pointer",
            opacity: uploading ? 0.5 : 1,
            display: "inline-block",
            padding: "6px 14px",
            borderRadius: "var(--radius)",
            background: "var(--surface2)",
            border: "1px solid var(--border)",
          }}
        >
          {uploading ? "Uploading…" : resume ? "Replace Resume" : "Upload PDF"}
        </label>

        {error && <span style={{ color: "var(--red)", fontSize: "12px" }}>{error}</span>}
      </div>
    </div>
  );
}

const sectionTitle = {
  fontSize: "13px",
  fontWeight: "700",
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  marginBottom: "12px",
};

const infoBox = {
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "12px 14px",
};
