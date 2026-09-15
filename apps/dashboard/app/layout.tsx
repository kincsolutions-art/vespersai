import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Vespers",
  description: "Your Telegram assistant, connected to your day.",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
