"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api";
import type { Role } from "@/lib/auth";
import { getRole, getToken } from "@/lib/auth";
import { useFilters } from "@/lib/filters";
import type { Brand } from "@/lib/types";
import { IntegrationsPanel } from "@/components/settings/IntegrationsPanel";
import { LlmModelPanel } from "@/components/settings/LlmModelPanel";
import { PromptManager } from "@/components/settings/PromptManager";
import { SnippetInstall } from "@/components/settings/SnippetInstall";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function SettingsSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading settings">
      <Skeleton className="h-8 w-40" />
      {Array.from({ length: 3 }).map((_, i) => (
        <Card key={i}>
          <CardContent className="space-y-3 pt-6">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-10 w-full" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <Card className="mx-auto mt-10 max-w-lg text-center">
      <CardHeader>
        <CardTitle>Set up your first brand</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Once you&apos;ve onboarded a brand, its prompts, integrations, and install snippet are
          managed here.
        </p>
        <Button asChild>
          <Link href="/onboarding">Start onboarding</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function BrandSummary({ brand }: { brand: Brand }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Brand</CardTitle>
        <CardDescription>Your brand and the competitors we track against it.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1 text-sm">
        <p>
          <span className="font-medium">{brand.name}</span>{" "}
          <span className="text-muted-foreground">— {brand.domain}</span>
        </p>
        <p className="text-muted-foreground">
          Competitors:{" "}
          {brand.competitors.length > 0 ? brand.competitors.join(", ") : "None yet"}
        </p>
      </CardContent>
    </Card>
  );
}

const ROLE_CAPABILITIES: { role: Role; label: string; description: string }[] = [
  { role: "owner", label: "Owner", description: "Full control, including billing and integrations." },
  { role: "admin", label: "Admin", description: "Connect integrations; manage prompts and team settings." },
  { role: "editor", label: "Editor", description: "Manage seed prompts and approve/publish content." },
  { role: "viewer", label: "Viewer", description: "Read-only access to every dashboard screen." },
];

function TeamRolesSection({ role }: { role: Role | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Team & roles</CardTitle>
        <CardDescription>Role-based access control for this tenant.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Role</TableHead>
              <TableHead>Access</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {ROLE_CAPABILITIES.map((r) => (
              <TableRow key={r.role}>
                <TableCell className="font-medium">
                  {r.label}
                  {role === r.role ? (
                    <Badge variant="secondary" className="ml-2">
                      You
                    </Badge>
                  ) : null}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">{r.description}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        <div className="flex items-center justify-between rounded-md border border-dashed p-4">
          <div>
            <p className="text-sm font-medium">Single sign-on (SAML / OIDC)</p>
            <p className="text-xs text-muted-foreground">
              Enterprise SSO is coming soon — contact us to join the beta.
            </p>
          </div>
          <Badge variant="outline">Coming soon</Badge>
        </div>
      </CardContent>
    </Card>
  );
}

/** Settings (ui-spec §3.8): brand/competitors, seed prompts, integrations, install snippet, team & SSO. */
export default function SettingsPage() {
  const { brandId } = useFilters();
  const role = getRole();

  const brandsQuery = useQuery({
    queryKey: ["brands"],
    queryFn: () => apiClient(getToken).brands(),
  });

  const activeBrandId = brandId ?? brandsQuery.data?.[0]?.id ?? null;
  const activeBrand: Brand | undefined = brandsQuery.data?.find((b) => b.id === activeBrandId);

  if (brandsQuery.isLoading) return <SettingsSkeleton />;

  if (!brandsQuery.data || brandsQuery.data.length === 0 || activeBrandId === null) {
    return <EmptyState />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Manage your brand, seed prompts, integrations, and team access.
        </p>
      </div>

      {activeBrand ? <BrandSummary brand={activeBrand} /> : null}
      <PromptManager brandId={activeBrandId} role={role} />
      <IntegrationsPanel role={role} />
      <LlmModelPanel role={role} />
      <SnippetInstall brandId={activeBrandId} />
      <TeamRolesSection role={role} />
    </div>
  );
}
