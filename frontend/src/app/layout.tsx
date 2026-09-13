import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI News Intelligence Platform",
  description: "Next-generation intelligence pipeline aggregating research, industry breakthroughs, and real-time AI developments.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased selection:bg-indigo-500/30 selection:text-indigo-200">
        {children}
      </body>
    </html>
  );
}
