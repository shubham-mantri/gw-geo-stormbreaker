"use client";

import { useMutation } from "@tanstack/react-query";
import { Play } from "lucide-react";

import { apiClient } from "@/lib/api";
import type { Role } from "@/lib/auth";
import { getToken } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Roles that may trigger a run (backend: `POST /brands/{id}/measure` requires `role >= editor`). */
const CAN_MEASURE_ROLES: Role[] = ["owner", "admin", "editor"];

export type RunMeasurementButtonProps = {
  /** Brand to measure. `null` (no brand selected/added yet) disables the button. */
  brandId: string | null;
  role: Role | null;
  /** Extra classes for the wrapper (e.g. alignment when placed in a header row). */
  className?: string;
};

/**
 * "Run measurement" — kicks off a fresh measurement pass for `brandId` via
 * `POST /brands/{id}/measure` (202; the run itself is async on the backend).
 *
 * Gated to editor+ (disabled + hint below editor, never hidden — mirrors
 * `IntegrationsPanel` / `PromptManager` / the Content approve+publish gate). On
 * success it shows a non-blocking inline confirmation echoing the engines the
 * backend scheduled; the data lands a few minutes later, so it tells the user to
 * refresh. Shared by the Overview header and the Overview/Visibility empty states.
 */
export function RunMeasurementButton({
  brandId,
  role,
  className,
}: RunMeasurementButtonProps) {
  const canMeasure = role !== null && CAN_MEASURE_ROLES.includes(role);

  const mutation = useMutation({
    mutationFn: (id: string) => apiClient(getToken).measureBrand(id),
  });

  const disabled = !canMeasure || brandId === null || mutation.isPending;

  return (
    <div className={cn("space-y-2", className)}>
      <Button
        type="button"
        onClick={() => {
          if (brandId !== null) mutation.mutate(brandId);
        }}
        disabled={disabled}
        title={
          !canMeasure
            ? "You need editor access to run a measurement"
            : brandId === null
              ? "Add or select a brand first"
              : undefined
        }
      >
        <Play aria-hidden="true" />
        {mutation.isPending ? "Starting…" : "Run measurement"}
      </Button>

      {mutation.isSuccess ? (
        <p role="status" className="text-sm text-success">
          Measurement started for{" "}
          {mutation.data.engines.length > 0
            ? mutation.data.engines.join(", ")
            : "the default engines"}{" "}
          — data will appear in a few minutes; refresh to see it.
        </p>
      ) : null}
      {mutation.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {mutation.error instanceof Error
            ? mutation.error.message
            : "Failed to start measurement"}
        </p>
      ) : null}
      {!canMeasure ? (
        <p className="text-xs text-muted-foreground">
          You need editor access to run a measurement.
        </p>
      ) : null}
    </div>
  );
}
