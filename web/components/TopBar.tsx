"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { LogOut, Plus } from "lucide-react";

import { apiClient } from "@/lib/api";
import { getToken, logout } from "@/lib/auth";
import { useFilters } from "@/lib/filters";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const DATE_RANGES = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
  { value: "qtd", label: "Quarter to date" },
];

const ENGINES = [
  { value: "all", label: "All engines" },
  { value: "chatgpt", label: "ChatGPT" },
  { value: "perplexity", label: "Perplexity" },
  { value: "google_ai_overview", label: "Google AI Overview" },
  { value: "gemini", label: "Gemini" },
  { value: "claude", label: "Claude" },
  { value: "copilot", label: "Copilot" },
];

/**
 * Top bar: brand switcher, date range, engine filter, account menu.
 * The brand switcher only picks between brands *within the authenticated
 * tenant* — the tenant itself comes from the token and is never selectable
 * here (ui-spec §5).
 */
export function TopBar() {
  const router = useRouter();
  const { brandId, range, engine, setBrandId, setRange, setEngine } =
    useFilters();

  const { data: brands } = useQuery({
    queryKey: ["brands"],
    queryFn: () => apiClient(getToken).brands(),
    enabled: getToken() !== null,
    retry: false,
  });

  // Auto-select the first brand once loaded so screens have a brand to query.
  useEffect(() => {
    if (brandId === null && brands && brands.length > 0) {
      setBrandId(brands[0].id);
    }
  }, [brandId, brands, setBrandId]);

  function onSignOut() {
    logout();
    router.replace("/login");
  }

  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b bg-background px-6">
      <Select value={brandId ?? undefined} onValueChange={setBrandId}>
        <SelectTrigger className="w-[200px]" aria-label="Brand">
          <SelectValue placeholder="Select brand" />
        </SelectTrigger>
        <SelectContent>
          {(brands ?? []).map((brand) => (
            <SelectItem key={brand.id} value={brand.id}>
              {brand.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* A separate control (not a Select option) to onboard a new brand — routes
          to the existing /onboarding wizard. */}
      <Button
        variant="outline"
        size="icon"
        aria-label="Add brand"
        title="Add brand"
        onClick={() => router.push("/onboarding")}
      >
        <Plus className="h-4 w-4" />
      </Button>

      <Select value={range} onValueChange={setRange}>
        <SelectTrigger className="w-[160px]" aria-label="Date range">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {DATE_RANGES.map((r) => (
            <SelectItem key={r.value} value={r.value}>
              {r.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={engine} onValueChange={setEngine}>
        <SelectTrigger className="w-[190px]" aria-label="Engine filter">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {ENGINES.map((e) => (
            <SelectItem key={e.value} value={e.value}>
              {e.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <div className="ml-auto">
        <Button variant="ghost" size="sm" onClick={onSignOut}>
          <LogOut className="h-4 w-4" />
          Sign out
        </Button>
      </div>
    </header>
  );
}
