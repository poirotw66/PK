import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PK 賽程管理台",
  description: "安全調整賽程、檢查衝突並保留版本紀錄。",
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
    <html lang="zh-Hant">
      <body className="antialiased">{children}</body>
    </html>
  );
}
