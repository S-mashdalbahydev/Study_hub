import type { Metadata } from "next";
import { AuthPage } from "@/components/auth-page";

export const metadata: Metadata = { title: "Log in · StudyHub" };

export default function Page() {
  return <AuthPage mode="login" />;
}
