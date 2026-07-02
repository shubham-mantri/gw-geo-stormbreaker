"use client";

import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { LogOut } from "lucide-react";

import { apiClient } from "@/lib/api";
import { getToken, logout } from "@/lib/auth";
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

  const { data: brands } = useQuery({
    queryKey: ["brands"],
    queryFn: () => apiClient(getToken).brands(),
    enabled: getToken() !== null,
    retry: false,
  });

  function onSignOut() {
    logout();
    router.replace("/login");
  }

  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b bg-background px-6">
      <Select>
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

      <Select defaultValue="30d">
        <SelectTrigger className="w-[160px]" aria-label="Date range">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {DATE_RANGES.map((range) => (
            <SelectItem key={range.value} value={range.value}>
              {range.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select defaultValue="all">
        <SelectTrigger className="w-[190px]" aria-label="Engine filter">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {ENGINES.map((engine) => (
            <SelectItem key={engine.value} value={engine.value}>
              {engine.label}
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
