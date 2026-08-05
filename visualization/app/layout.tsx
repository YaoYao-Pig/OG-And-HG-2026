import type { Metadata, Viewport } from "next";
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

const title = "OpenGalaxy × ModelGalaxy · 开源与 AI 生态星图";
const description =
  "探索 GitHub 开源协作与 Hugging Face AI 生态：19,771 个仓库、106,650 条协作关系，以及 24,000 个每日更新的模型、数据集与 Space。";

function resolveSiteUrl(): URL {
  const configuredUrl = process.env.NEXT_PUBLIC_SITE_URL?.trim();

  try {
    const siteUrl = new URL(
      configuredUrl ||
        "https://open-galaxy-collaboration-atlas.s20020515.chatgpt.site/",
    );
    siteUrl.pathname = `${siteUrl.pathname.replace(/\/+$/, "")}/`;
    siteUrl.search = "";
    siteUrl.hash = "";
    return siteUrl;
  } catch {
    return new URL(
      "https://open-galaxy-collaboration-atlas.s20020515.chatgpt.site/",
    );
  }
}

const siteUrl = resolveSiteUrl();
const imageUrl = new URL("og.png", siteUrl).toString();

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#05070d",
};

export const metadata: Metadata = {
  metadataBase: siteUrl,
  title,
  description,
  alternates: {
    canonical: siteUrl,
  },
  icons: {
    icon: imageUrl,
    shortcut: imageUrl,
  },
  openGraph: {
    title,
    description,
    type: "website",
    locale: "zh_CN",
    siteName: "OpenGalaxy × ModelGalaxy",
    url: siteUrl,
    images: [
      {
        url: imageUrl,
        width: 1672,
        height: 941,
        alt: title,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title,
    description,
    images: [imageUrl],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
