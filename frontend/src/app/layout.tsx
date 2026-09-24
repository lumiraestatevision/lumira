import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lumira",
  description: "Vom Grundriss zum begehbaren 3D-Modell",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <body>{children}</body>
    </html>
  );
}
