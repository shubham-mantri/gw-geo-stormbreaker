import { redirect } from "next/navigation";

/** Entry route → send to AI Surface demo page. */
export default function RootPage(): never {
  redirect("/ai-surface");
}
