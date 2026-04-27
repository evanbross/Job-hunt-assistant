import React, { useEffect, useMemo, useState } from "react";

const STATUS_FALLBACKS = {
  applied: { background: "#d9ead3", color: "#274e13" },
  rejected: { background: "#cc0000", color: "#ffffff" },
  waiting_call: { background: "#cfe8f3", color: "#134f5c" },
  waiting_second: { background: "#f4c7b3", color: "#783f04" },
  eval_test: { background: "#16875b", color: "#ffffff" },
  job_filled: { background: "#cc0000", color: "#ffffff" },
  ghosted: { background: "#e8710a", color: "#ffffff" },
  skipped: { background: "#f3f4f6", color: "#6b7280" },
};

const STATUS_OPTIONS = [
  "Applied",
  "Rejected",
  "Waiting for Call",
  "Waiting for 2nd",
  "Need to do Eval",
  "Job was filled",
  "Ghosted",
];

const EMPTY_FORM = {
  company: "",
  status: "Applied",
  source: "",
  date: new Date().toISOString().slice(0, 10),
  job_title: "",
  url: "",
  notes: "",
};

function normalizeStatus(value) {
  return String(value || "").trim().toLowerCase();
}

function getStatusStyle(value) {
  const normalizedValue = normalizeStatus(value);
  if (STATUS_FALLBACKS[normalizedValue]) return STATUS_FALLBACKS[normalizedValue];
  if (normalizedValue.includes("2nd") || normalizedValue.includes("second")) {
    return STATUS_FALLBACKS.waiting_second;
  }
  if (normalizedValue.includes("waiting")) return STATUS_FALLBACKS.waiting_call;
  if (normalizedValue.includes("eval")) return STATUS_FALLBACKS.eval_test;
  if (normalizedValue.includes("filled")) return STATUS_FALLBACKS.job_filled;
  if (normalizedValue.includes("reject")) return STATUS_FALLBACKS.rejected;
  if (normalizedValue.includes("applied")) return STATUS_FALLBACKS.applied;
  if (normalizedValue.includes("ghost")) return STATUS_FALLBACKS.ghosted;
  return null;
}

function getCell(row, column) {
  if (!column) return { value: "" };
  return row.cells[column.index] || { value: "" };
}

function formForRow(row, trackingColumns) {
  return {
    company: getCell(row, trackingColumns.company).value || "",
    status: getCell(row, trackingColumns.status).value || "",
    source: getCell(row, trackingColumns.source).value || "",
    date: getCell(row, trackingColumns.date).value || "",
    job_title: getCell(row, trackingColumns.jobTitle).value || "",
    url: getCell(row, trackingColumns.url).value || "",
    notes: getCell(row, trackingColumns.notes).value || "",
  };
}

function findColumn(columns, labels) {
  return columns.find((column) => {
    const normalizedLabel = String(column.label || "").trim().toLowerCase();
    return labels.some((label) => normalizedLabel === label);
  });
}

function findExactColumn(columns, label) {
  return columns.find(
    (column) => String(column.label || "").trim().toLowerCase() === label,
  );
}

function isDefaultBackground(color) {
  const normalizedColor = String(color || "").trim().toLowerCase();
  return !normalizedColor || normalizedColor === "#ffffff";
}

function statusColor(cell) {
  const fallbackStyle = getStatusStyle(cell.value);
  return {
    background: isDefaultBackground(cell.background_color)
      ? fallbackStyle?.background || "var(--surface2)"
      : cell.background_color,
    color: cell.text_color || fallbackStyle?.color || "var(--text)",
  };
}

function StatusIndicator({ cell }) {
  const colors = statusColor(cell);
  const value = String(cell.value || "").trim() || "-";
  return (
    <span
      style={{
        ...statusBadgeStyle,
        background: colors.background,
        color: colors.color,
      }}
    >
      {value}
    </span>
  );
}

function cellStyle(isHeader = false) {
  return {
    ...baseCellStyle,
    background: isHeader ? "var(--surface2)" : "transparent",
    color: isHeader ? "var(--text-muted)" : "var(--text)",
    fontWeight: isHeader ? 700 : 400,
  };
}

function textMatches(row, query) {
  if (!query) return true;
  const normalizedQuery = query.trim().toLowerCase();
  return row.cells.some((cell) => String(cell.value || "").toLowerCase().includes(normalizedQuery));
}

export default function ApplicationSheetPanel() {
  const [sheet, setSheet] = useState({ sheet_name: "", columns: [], rows: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expandedRows, setExpandedRows] = useState(new Set());
  const [search, setSearch] = useState("");
  const [newRow, setNewRow] = useState(EMPTY_FORM);
  const [showAddApplication, setShowAddApplication] = useState(false);
  const [editingRows, setEditingRows] = useState({});
  const [stageRows, setStageRows] = useState({});
  const [savingKey, setSavingKey] = useState("");

  const loadSheet = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/sheet/applications");
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || `Sheet load failed (${res.status})`);
        return;
      }
      setSheet({
        sheet_name: data.sheet_name || "",
        columns: Array.isArray(data.columns) ? data.columns.filter((column) => !column.hidden) : [],
        rows: Array.isArray(data.rows) ? data.rows : [],
      });
    } catch (loadError) {
      setError(loadError.message || "Sheet load failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadSheet(); }, []);

  const visibleRows = useMemo(
    () => sheet.rows.filter((row) => textMatches(row, search)),
    [sheet.rows, search],
  );
  const trackingColumns = useMemo(() => {
    const company = findColumn(sheet.columns, ["company"]);
    const status = findColumn(sheet.columns, ["status"]);
    const source = findColumn(sheet.columns, ["source"]);
    const date = findColumn(sheet.columns, ["date", "date of application"]);
    const notes = findExactColumn(sheet.columns, "notes")
      || findColumn(sheet.columns, ["contact/notes", "contact"]);
    const jobTitle = sheet.columns[4] || findColumn(sheet.columns, ["job title", "title"]);
    const url = findColumn(sheet.columns, ["url", "link"]);
    return { company, status, source, date, notes, jobTitle, url };
  }, [sheet.columns]);

  const parentColumns = useMemo(
    () => [
      { key: "company", label: "Company", column: trackingColumns.company },
      { key: "job_title", label: "Title", column: trackingColumns.jobTitle },
      { key: "status", label: "Status", column: trackingColumns.status },
      { key: "source", label: "Source", column: trackingColumns.source },
      { key: "date", label: "Date", column: trackingColumns.date },
    ].filter((entry) => entry.column),
    [trackingColumns],
  );

  const toggleRow = (rowNumber) => {
    setExpandedRows((previousRows) => {
      const nextRows = new Set(previousRows);
      if (nextRows.has(rowNumber)) {
        nextRows.delete(rowNumber);
      } else {
        nextRows.add(rowNumber);
      }
      return nextRows;
    });
  };

  const updateNewRow = (field, value) => {
    setNewRow((previousRow) => ({ ...previousRow, [field]: value }));
  };

  const updateEditRow = (rowNumber, field, value) => {
    setEditingRows((previousRows) => ({
      ...previousRows,
      [rowNumber]: { ...previousRows[rowNumber], [field]: value },
    }));
  };

  const updateStageRow = (rowNumber, field, value) => {
    setStageRows((previousRows) => ({
      ...previousRows,
      [rowNumber]: { ...previousRows[rowNumber], [field]: value },
    }));
  };

  const addRow = async () => {
    setSavingKey("new");
    try {
      const res = await fetch("/api/sheet/applications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newRow),
      });
      if (!res.ok) {
        const data = await res.json();
        window.alert(data.error || `Save failed (${res.status})`);
        return;
      }
      setNewRow(EMPTY_FORM);
      setShowAddApplication(false);
      await loadSheet();
    } finally {
      setSavingKey("");
    }
  };

  const startEditing = (row) => {
    setEditingRows((previousRows) => ({
      ...previousRows,
      [row.row_number]: formForRow(row, trackingColumns),
    }));
  };

  const saveRow = async (rowNumber) => {
    setSavingKey(`edit:${rowNumber}`);
    try {
      const res = await fetch(`/api/sheet/applications/${rowNumber}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(editingRows[rowNumber]),
      });
      if (!res.ok) {
        const data = await res.json();
        window.alert(data.error || `Save failed (${res.status})`);
        return;
      }
      setEditingRows((previousRows) => {
        const nextRows = { ...previousRows };
        delete nextRows[rowNumber];
        return nextRows;
      });
      await loadSheet();
    } finally {
      setSavingKey("");
    }
  };

  const addStage = async (rowNumber) => {
    const stage = stageRows[rowNumber] || {
      status: "Waiting for Call",
      date: new Date().toISOString().slice(0, 10),
      notes: "",
    };
    setSavingKey(`stage:${rowNumber}`);
    try {
      const res = await fetch(`/api/sheet/applications/${rowNumber}/stages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(stage),
      });
      if (!res.ok) {
        const data = await res.json();
        window.alert(data.error || `Save failed (${res.status})`);
        return;
      }
      setStageRows((previousRows) => ({
        ...previousRows,
        [rowNumber]: {
          status: "Waiting for Call",
          date: new Date().toISOString().slice(0, 10),
          notes: "",
        },
      }));
      await loadSheet();
    } finally {
      setSavingKey("");
    }
  };

  if (loading) {
    return <p style={{ color: "var(--text-muted)" }}>Loading sheet...</p>;
  }

  if (error) {
    return (
      <div className="card">
        <p style={{ color: "var(--red)", marginBottom: "12px" }}>{error}</p>
        <button className="btn-secondary" onClick={loadSheet}>Retry</button>
      </div>
    );
  }

  if (sheet.columns.length === 0 || sheet.rows.length === 0) {
    return (
      <div className="empty-state">
        <p>No rows were found in the configured sheet.</p>
      </div>
    );
  }

  return (
    <div>
      <div style={toolbarStyle}>
        <div>
          <h2 style={titleStyle}>Application Tracking</h2>
          <p style={summaryStyle}>{sheet.rows.length} row{sheet.rows.length === 1 ? "" : "s"}</p>
        </div>
        <div style={toolbarActionsStyle}>
          <input
            type="search"
            placeholder="Search sheet"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            style={searchStyle}
          />
          <button
            className="btn-primary"
            onClick={() => setShowAddApplication(true)}
            disabled={showAddApplication}
          >
            Add Application
          </button>
          <button className="btn-secondary" onClick={loadSheet}>Refresh</button>
        </div>
      </div>

      {showAddApplication && (
        <div className="card" style={addCardStyle}>
          <input
            value={newRow.company}
            onChange={(event) => updateNewRow("company", event.target.value)}
            placeholder="Company"
            style={compactInputStyle}
          />
          <input
            value={newRow.job_title}
            onChange={(event) => updateNewRow("job_title", event.target.value)}
            placeholder="Job title"
            style={compactInputStyle}
          />
          <select
            value={newRow.status}
            onChange={(event) => updateNewRow("status", event.target.value)}
            style={compactInputStyle}
          >
            {STATUS_OPTIONS.map((status) => (
              <option key={status} value={status}>{status}</option>
            ))}
          </select>
          <input
            value={newRow.source}
            onChange={(event) => updateNewRow("source", event.target.value)}
            placeholder="Source"
            style={compactInputStyle}
          />
          <input
            type="date"
            value={newRow.date}
            onChange={(event) => updateNewRow("date", event.target.value)}
            style={compactInputStyle}
          />
          <input
            value={newRow.url}
            onChange={(event) => updateNewRow("url", event.target.value)}
            placeholder="URL"
            style={compactInputStyle}
          />
          <input
            value={newRow.notes}
            onChange={(event) => updateNewRow("notes", event.target.value)}
            placeholder="Notes"
            style={compactInputStyle}
          />
          <div style={addButtonRowStyle}>
            <button className="btn-primary" onClick={addRow} disabled={savingKey === "new"}>
              Save
            </button>
            <button
              className="btn-secondary"
              onClick={() => {
                setNewRow(EMPTY_FORM);
                setShowAddApplication(false);
              }}
              disabled={savingKey === "new"}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div style={tableWrapStyle}>
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={{ ...headerCellStyle, minWidth: "42px", width: "42px" }} />
              {parentColumns.map((entry) => (
                <th key={entry.key} style={cellStyle(true)}>
                  <span style={oneLineStyle}>{entry.label}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.length === 0 ? (
              <tr>
                <td colSpan={parentColumns.length + 1} style={emptyCellStyle}>
                  No rows match "{search}".
                </td>
              </tr>
            ) : visibleRows.map((row, rowIndex) => {
              const isOpen = expandedRows.has(row.row_number);
              const rowShade = rowIndex % 2 === 0 ? "var(--surface)" : "#f7f8fd";
              return (
                <React.Fragment key={row.row_number}>
                  <tr style={{ ...bodyRowStyle, background: rowShade }} onClick={() => toggleRow(row.row_number)}>
                    <td style={{ ...rowNumberCellStyle, background: rowShade }}>{isOpen ? "v" : ">"}</td>
                    {parentColumns.map((entry) => {
                      const cell = getCell(row, entry.column);
                      return (
                        <td key={`${row.row_number}-${entry.key}`} style={cellStyle()}>
                          {entry.key === "status" ? (
                            <StatusIndicator cell={cell} />
                          ) : (
                            <span style={oneLineStyle}>{cell.value || ""}</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                  {isOpen && (
                    <tr>
                      <td />
                      <td colSpan={parentColumns.length} style={detailCellStyle}>
                        <div style={detailRowStyle}>
                          <div style={detailStatusStyle}>
                            <span style={detailLabelStyle}>Status</span>
                            <StatusIndicator cell={getCell(row, trackingColumns.status)} />
                          </div>
                          <div style={detailItemStyle}>
                            <span style={detailLabelStyle}>Date</span>
                            <span style={detailValueStyle}>{getCell(row, trackingColumns.date).value || "-"}</span>
                          </div>
                          <div style={{ ...detailItemStyle, flex: 1 }}>
                            <span style={detailLabelStyle}>Notes</span>
                            <span style={detailValueStyle}>{getCell(row, trackingColumns.notes).value || "-"}</span>
                          </div>
                        </div>
                        {editingRows[row.row_number] ? (
                          <div style={editGridStyle}>
                            <input value={editingRows[row.row_number].company} onChange={(event) => updateEditRow(row.row_number, "company", event.target.value)} placeholder="Company" />
                            <input value={editingRows[row.row_number].job_title} onChange={(event) => updateEditRow(row.row_number, "job_title", event.target.value)} placeholder="Job title" />
                            <select value={editingRows[row.row_number].status} onChange={(event) => updateEditRow(row.row_number, "status", event.target.value)}>
                              {STATUS_OPTIONS.map((status) => (
                                <option key={status} value={status}>{status}</option>
                              ))}
                            </select>
                            <input value={editingRows[row.row_number].source} onChange={(event) => updateEditRow(row.row_number, "source", event.target.value)} placeholder="Source" />
                            <input type="date" value={editingRows[row.row_number].date} onChange={(event) => updateEditRow(row.row_number, "date", event.target.value)} />
                            <input value={editingRows[row.row_number].url} onChange={(event) => updateEditRow(row.row_number, "url", event.target.value)} placeholder="URL" />
                            <textarea value={editingRows[row.row_number].notes} onChange={(event) => updateEditRow(row.row_number, "notes", event.target.value)} placeholder="Notes" rows={2} style={{ gridColumn: "1 / -1" }} />
                            <div style={buttonRowStyle}>
                              <button className="btn-primary" onClick={() => saveRow(row.row_number)} disabled={savingKey === `edit:${row.row_number}`}>
                                Save Row
                              </button>
                              <button
                                className="btn-secondary"
                                onClick={() => setEditingRows((previousRows) => {
                                  const nextRows = { ...previousRows };
                                  delete nextRows[row.row_number];
                                  return nextRows;
                                })}
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div style={buttonRowStyle}>
                            <button className="btn-secondary" onClick={() => startEditing(row)}>
                              Edit Row
                            </button>
                          </div>
                        )}
                        <div style={stageFormStyle}>
                          <select
                            value={(stageRows[row.row_number]?.status) || "Waiting for Call"}
                            onChange={(event) => updateStageRow(row.row_number, "status", event.target.value)}
                          >
                            {STATUS_OPTIONS.map((status) => (
                              <option key={status} value={status}>{status}</option>
                            ))}
                          </select>
                          <input
                            type="date"
                            value={(stageRows[row.row_number]?.date) || new Date().toISOString().slice(0, 10)}
                            onChange={(event) => updateStageRow(row.row_number, "date", event.target.value)}
                          />
                          <input
                            value={(stageRows[row.row_number]?.notes) || ""}
                            onChange={(event) => updateStageRow(row.row_number, "notes", event.target.value)}
                            placeholder="Stage notes"
                          />
                          <button className="btn-primary" onClick={() => addStage(row.row_number)} disabled={savingKey === `stage:${row.row_number}`}>
                            Add Stage
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const toolbarStyle = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "16px",
  marginBottom: "12px",
};

const toolbarActionsStyle = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  flex: "1",
  justifyContent: "flex-end",
};

const titleStyle = {
  fontSize: "18px",
  fontWeight: 700,
  lineHeight: 1.2,
};

const summaryStyle = {
  color: "var(--text-muted)",
  fontSize: "12px",
  marginTop: "2px",
};

const searchStyle = {
  maxWidth: "320px",
};

const addCardStyle = {
  display: "grid",
  gridTemplateColumns: "minmax(130px, 1fr) minmax(170px, 1.3fr) minmax(150px, 1fr) minmax(120px, 1fr) 140px minmax(180px, 1.2fr) minmax(160px, 1.2fr)",
  gap: "8px",
  alignItems: "center",
  padding: "12px",
};

const compactInputStyle = {
  minWidth: 0,
};

const addButtonRowStyle = {
  display: "flex",
  justifyContent: "flex-end",
  gap: "8px",
  gridColumn: "1 / -1",
};

const tableWrapStyle = {
  overflow: "auto",
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  boxShadow: "var(--shadow-sm)",
  maxHeight: "calc(100vh - 180px)",
};

const tableStyle = {
  width: "100%",
  borderCollapse: "separate",
  borderSpacing: 0,
  tableLayout: "fixed",
};

const headerCellStyle = {
  position: "sticky",
  top: 0,
  zIndex: 2,
  background: "var(--surface2)",
  borderBottom: "1px solid var(--border)",
};

const baseCellStyle = {
  borderRight: "1px solid var(--border)",
  borderBottom: "1px solid var(--border)",
  fontSize: "13px",
  height: "42px",
  padding: "9px 12px",
  verticalAlign: "middle",
};

const oneLineStyle = {
  display: "block",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const bodyRowStyle = {
  cursor: "pointer",
};

const rowNumberCellStyle = {
  ...baseCellStyle,
  width: "42px",
  minWidth: "42px",
  maxWidth: "42px",
  textAlign: "center",
  color: "var(--text-muted)",
  fontWeight: 700,
};

const detailCellStyle = {
  padding: "14px 16px",
  background: "var(--surface2)",
  borderBottom: "1px solid var(--border)",
};

const detailRowStyle = {
  display: "flex",
  alignItems: "flex-start",
  gap: "22px",
  minWidth: 0,
};

const editGridStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
  gap: "8px",
  marginTop: "14px",
  paddingTop: "12px",
  borderTop: "1px solid var(--border)",
};

const buttonRowStyle = {
  display: "flex",
  gap: "8px",
  justifyContent: "flex-end",
  gridColumn: "1 / -1",
};

const stageFormStyle = {
  display: "grid",
  gridTemplateColumns: "minmax(160px, 1fr) 150px minmax(220px, 2fr) auto",
  gap: "8px",
  alignItems: "center",
  marginTop: "12px",
  paddingTop: "12px",
  borderTop: "1px solid var(--border)",
};

const detailItemStyle = {
  minWidth: 0,
  display: "flex",
  flexDirection: "column",
  gap: "4px",
};

const detailStatusStyle = {
  ...detailItemStyle,
  minWidth: "150px",
};

const detailLabelStyle = {
  color: "var(--text-muted)",
  fontSize: "11px",
  fontWeight: 700,
  textTransform: "uppercase",
};

const detailValueStyle = {
  color: "var(--text)",
  fontSize: "13px",
  overflowWrap: "anywhere",
};

const statusBadgeStyle = {
  borderRadius: "999px",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  maxWidth: "100%",
  minHeight: "24px",
  padding: "3px 10px",
  fontSize: "12px",
  fontWeight: 700,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const emptyCellStyle = {
  padding: "28px",
  textAlign: "center",
  color: "var(--text-muted)",
  background: "var(--surface)",
};
