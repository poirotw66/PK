import type { Metadata } from "next";
import "./globals.css";
import "./mobile-schedule.css";
import "./readability.css";
import "./court-theme.css";

export const metadata: Metadata = {
  title: "PK 賽程管理台",
  description: "安全調整賽程、檢查衝突並保留版本紀錄。",
  other: { "page-type": "software", "site-kind": "software" },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-Hant" data-type="software">
      <body className="pk-console antialiased"><a className="skip-link" href="#main-content">跳至主要內容</a>{children}</body>
    </html>
  );
}
