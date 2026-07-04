import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Chronos — Distributed Job Scheduler",
  description:
    "Production-inspired distributed job scheduler: atomic claiming, leases, retries, and a dead letter queue.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
