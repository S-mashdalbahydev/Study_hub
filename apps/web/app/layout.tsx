import type { Metadata } from "next";
import { Inter, Noto_Naskh_Arabic, Source_Serif_4 } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/theme";
// Serif for titles and long-form reading, sans for controls, Naskh so Arabic
// text never falls back to a Latin display face.
const serif = Source_Serif_4({
  subsets: ["latin", "latin-ext"],
  variable: "--font-serif",
  display: "swap",
});
const sans = Inter({
  subsets: ["latin", "latin-ext"],
  variable: "--font-sans",
  display: "swap",
});
const arabic = Noto_Naskh_Arabic({
  subsets: ["arabic"],
  variable: "--font-arabic",
  display: "swap",
});
export const metadata: Metadata = {
  title: "StudyHub · A place to understand",
  description: "Your books, your questions, your space to learn.",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${serif.variable} ${sans.variable} ${arabic.variable}`}
    >
      <body>
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
