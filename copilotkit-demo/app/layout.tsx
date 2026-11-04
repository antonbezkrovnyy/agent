import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "LogiCore Agent — CopilotKit/AG-UI sandbox",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
