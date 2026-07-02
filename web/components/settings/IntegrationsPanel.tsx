"use client";

import { useState } from "react";

import { apiClient } from "@/lib/api";
import type { Role } from "@/lib/auth";
import { getToken } from "@/lib/auth";
import type { IntegrationKind } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** Roles that may connect an integration (backend: `POST /integrations/{kind}` requires `role >= admin`). */
const CAN_CONNECT_ROLES: Role[] = ["owner", "admin"];

const INTEGRATIONS: { kind: IntegrationKind; label: string }[] = [
  { kind: "hubspot", label: "HubSpot" },
  { kind: "salesforce", label: "Salesforce" },
  { kind: "ga4", label: "GA4" },
];

export type IntegrationsPanelProps = {
  role: Role | null;
};

/**
 * Integrations (ui-spec §3.8): connect HubSpot/Salesforce (CRM) or GA4 via `POST
 * /integrations/{kind}`. Connecting requires `role >= admin` server-side — an `editor`/`viewer`
 * token gets a 403, so the connect buttons are disabled client-side for anyone below admin (never
 * hidden, so the requirement is visible rather than silently absent).
 */
export function IntegrationsPanel({ role }: IntegrationsPanelProps) {
  const canConnect = role !== null && CAN_CONNECT_ROLES.includes(role);
  const [connected, setConnected] = useState<Record<string, string>>({});
  const [pendingKind, setPendingKind] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onConnect(kind: IntegrationKind) {
    if (!canConnect) return;
    const key = kind as string;
    setPendingKind(key);
    setError(null);
    try {
      const result = await apiClient(getToken).connectIntegration(kind, {});
      setConnected((c) => ({ ...c, [key]: result.status }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect integration");
    } finally {
      setPendingKind(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Integrations</CardTitle>
        <CardDescription>
          Connect a CRM or analytics source to enrich pipeline attribution.
          {!canConnect ? " Connecting requires admin access." : null}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {INTEGRATIONS.map(({ kind, label }) => {
          const key = kind as string;
          const status = connected[key];
          const isPending = pendingKind === key;
          return (
            <div
              key={key}
              className="flex items-center justify-between rounded-md border p-3"
            >
              <div>
                <p className="text-sm font-medium">{label}</p>
                <p className="text-xs text-muted-foreground">
                  {status ? `Status: ${status}` : "Not connected"}
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!canConnect || isPending || Boolean(status)}
                onClick={() => onConnect(kind)}
                title={!canConnect ? "You need admin access to connect integrations" : undefined}
              >
                {status ? "Connected" : isPending ? "Connecting…" : `Connect ${label}`}
              </Button>
            </div>
          );
        })}
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
