"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";

import { getToken } from "@/lib/auth";
import { FiltersProvider } from "@/lib/filters";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";

const PUBLIC_ROUTES = ["/ai-surface"];

/**
 * Layout for all authenticated dashboard screens. Guards the route: if there is
 * no session token, redirect to /login before rendering anything.
 * Public routes (like /ai-surface for demo) bypass the auth check.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [authed, setAuthed] = useState(false);

  const isPublic = PUBLIC_ROUTES.some(
    (r) => pathname === r || pathname?.startsWith(`${r}/`),
  );

  useEffect(() => {
    if (isPublic) {
      setAuthed(true);
    } else if (getToken() === null) {
      router.replace("/login");
    } else {
      setAuthed(true);
    }
  }, [router, isPublic]);

  if (!authed) return null;

  return (
    <FiltersProvider>
      <div className="flex min-h-screen">
        <Sidebar />
        <div className="flex min-h-screen flex-1 flex-col">
          <TopBar />
          <main className="flex-1 overflow-y-auto p-6">{children}</main>
        </div>
      </div>
    </FiltersProvider>
  );
}
