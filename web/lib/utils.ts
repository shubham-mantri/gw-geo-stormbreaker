import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge conditional class names and de-dupe conflicting Tailwind utilities. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Format a 0..1 rate as a whole-number percentage, e.g. 0.42 -> "42%". */
export function formatPct(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}
