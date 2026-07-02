"use client";

import { Download, FileText } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

/** A single exportable row; cells are stringified as-is. */
export type ExportRow = (string | number)[];

/**
 * Build a CSV document from `rows` (first row is conventionally the header).
 * Pure function — no DOM access — so it's trivial to unit test independent of
 * the browser's download machinery.
 */
export function toCsv(rows: ExportRow[]): string {
  return rows
    .map((row) =>
      row
        .map((cell) => {
          const s = String(cell);
          return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        })
        .join(","),
    )
    .join("\r\n");
}

/**
 * Trigger a client-side CSV download — no server round-trip, no dependency
 * (no papaparse/csv-writer). Guarded: some environments (tests, very old
 * browsers) don't implement Blob URLs; fail silently rather than throw, since
 * `toCsv` above already did the part that matters for correctness.
 */
function downloadCsv(filename: string, csv: string): void {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  try {
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  } catch {
    // Blob URL downloads aren't available in this environment — nothing else
    // to do client-side without a dependency.
  }
}

/**
 * Default PDF hook: the zero-dependency fallback is the browser's native
 * print dialog (every browser's print dialog offers "Save as PDF"). Callers
 * that want a real generated PDF pass `onExportPdf` to wire in a future
 * renderer without touching this component.
 */
function defaultExportPdf(): void {
  if (typeof window === "undefined" || typeof window.print !== "function") {
    return;
  }
  window.print();
}

export type ExportButtonProps = {
  /** Download filename, without extension. */
  filename: string;
  /** Rows to serialize as CSV (first row is conventionally the header). */
  rows: ExportRow[];
  /** PDF export hook — defaults to the browser print dialog. */
  onExportPdf?: () => void;
  className?: string;
};

/**
 * The exec/board export control (ui-spec §3.6): CSV is fully client-side and
 * dependency-free; PDF is a hook so a real renderer can be wired in later
 * without changing this component's contract.
 */
export function ExportButton({
  filename,
  rows,
  onExportPdf,
  className,
}: ExportButtonProps) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => downloadCsv(`${filename}.csv`, toCsv(rows))}
      >
        <Download className="h-4 w-4" aria-hidden="true" />
        Export CSV
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onExportPdf ?? defaultExportPdf}
      >
        <FileText className="h-4 w-4" aria-hidden="true" />
        Export PDF
      </Button>
    </div>
  );
}
