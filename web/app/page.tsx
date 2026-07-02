import { redirect } from "next/navigation";

/** Entry route → send to login; the app guard bounces authed users onward. */
export default function RootPage(): never {
  redirect("/login");
}
