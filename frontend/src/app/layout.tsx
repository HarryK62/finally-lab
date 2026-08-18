import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "FinAlly — AI Trading Workstation",
  description: "Live market data, a simulated portfolio, and an AI trading copilot.",
};

export const viewport: Viewport = {
  themeColor: "#0d1117",
  colorScheme: "dark",
};

// Typed explicitly rather than with Next's generated `LayoutProps<"/">` global,
// which only exists in `.next/types` after a build — `npm run typecheck` has to
// pass on a fresh clone too.
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="h-full bg-terminal text-ink">{children}</body>
    </html>
  );
}
