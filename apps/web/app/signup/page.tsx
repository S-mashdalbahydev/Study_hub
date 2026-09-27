import type { Metadata } from "next";
import { AuthPage } from "@/components/auth-page";

export const metadata: Metadata = { title: "Create your account · StudyHub" };

export default function Page() {
  return <AuthPage mode="signup" />;
}
