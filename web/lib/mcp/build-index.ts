import fs from "fs";
import path from "path";

import type { AiIndex, AiIndexPage } from "./types";

function readJsonDir(dir: string): Record<string, unknown>[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8")));
}

function stripHtml(str: string | undefined | null): string {
  return (str || "").replace(/<[^>]*>/g, "").trim();
}

function buildPages(baseUrl: string, contentDir: string): AiIndexPage[] {
  const pages: AiIndexPage[] = [];

  pages.push({
    path: "",
    type: "page",
    url: baseUrl,
    title: "Homepage",
    description: "",
    last_modified_at: null,
    markdown: "",
  });

  // Services
  for (const data of readJsonDir(path.join(contentDir, "clusters", "service"))) {
    const pi = (data as Record<string, any>).page_info || {};
    const slug = (data as Record<string, any>).slug;
    if (!slug) continue;
    const fd = pi.fold_data || {};
    const desc = stripHtml(pi.description);
    const title = stripHtml(pi.title || slug);

    const mdParts = [`# ${title}`, desc ? `\n${desc}` : ""];
    if (fd.service_description?.description) {
      mdParts.push(`\n## Overview\n${stripHtml(fd.service_description.description)}`);
    }
    if (fd.why_us?.content && Array.isArray(fd.why_us.content)) {
      mdParts.push("\n## Why Choose Us");
      for (const item of fd.why_us.content) {
        if (item.title)
          mdParts.push(`- **${stripHtml(item.title)}**: ${stripHtml(item.description || "")}`);
      }
    }
    if (fd.faq?.content && Array.isArray(fd.faq.content)) {
      mdParts.push("\n## FAQ");
      for (const item of fd.faq.content) {
        if (item.question)
          mdParts.push(`**Q: ${stripHtml(item.question)}**\nA: ${stripHtml(item.answer || "")}\n`);
      }
    }

    pages.push({
      path: `service/${slug}`,
      type: "service",
      url: `${baseUrl}/service/${slug}`,
      title,
      description: desc,
      last_modified_at: pi.last_modified_at || null,
      markdown: mdParts.join("\n"),
    });
  }

  // Categories
  for (const data of readJsonDir(path.join(contentDir, "clusters", "category"))) {
    const pi = (data as Record<string, any>).page_info || {};
    const slug = (data as Record<string, any>).slug;
    if (!slug) continue;
    const title = stripHtml(pi.title || slug);
    const desc = stripHtml(pi.description);
    const fd = pi.fold_data || {};

    const mdParts = [`# ${title}`, desc ? `\n${desc}` : ""];
    if (fd.products?.content && Array.isArray(fd.products.content)) {
      mdParts.push("\n## Products");
      for (const item of fd.products.content) {
        const name = item.product_name || item.title || "";
        if (name) mdParts.push(`- ${stripHtml(name)}`);
      }
    }

    pages.push({
      path: `category/${slug}`,
      type: "category",
      url: `${baseUrl}/category/${slug}`,
      title,
      description: desc,
      last_modified_at: pi.last_modified_at || null,
      markdown: mdParts.join("\n"),
    });
  }

  // Blog posts
  for (const data of readJsonDir(path.join(contentDir, "clusters", "blog", "data"))) {
    const pi = (data as Record<string, any>).page_info || {};
    const slug = (data as Record<string, any>).slug;
    if (!slug) continue;
    const title = stripHtml(pi.title || slug);
    const desc = stripHtml(pi.description);

    pages.push({
      path: `blog/${slug}`,
      type: "blog",
      url: `${baseUrl}/blog/${slug}`,
      title,
      description: desc,
      last_modified_at: pi.last_modified_at || null,
      markdown: `# ${title}\n\n${desc || "Blog article about " + title.toLowerCase() + "."}`,
    });
  }

  pages.sort((a, b) => a.path.localeCompare(b.path));
  return pages;
}

type IndexWithRendered = AiIndex & { _llmsTxt: string; _llmsFullTxt: string };

const SECTION_ORDER: [string, string][] = [
  ["service", "Services"],
  ["category", "Categories"],
  ["blog", "Blog"],
  ["page", "Other Pages"],
];

function renderLlmsTxt(index: AiIndex): string {
  const { site, business, pages, products } = index;
  const lines = [`# ${site.name}`, "", `> ${site.description}`, ""];
  const contact = [
    business.address && `Address: ${business.address}`,
    business.phones?.length && `Phone: ${business.phones[0]}`,
    business.emails?.length && `Email: ${business.emails[0]}`,
  ].filter(Boolean);
  if (contact.length) lines.push(contact.join(" · "), "");
  for (const [type, heading] of SECTION_ORDER) {
    const group = pages.filter((p) => p.type === type);
    if (!group.length) continue;
    lines.push(`## ${heading}`);
    for (const p of group) {
      lines.push(`- [${p.title}](${p.url})${p.description ? `: ${p.description}` : ""}`);
    }
    lines.push("");
  }
  if (products?.length) {
    lines.push("## Products");
    for (const p of products) lines.push(`- [${p.name}](${p.url})`);
    lines.push("");
  }
  return lines.join("\n");
}

function renderLlmsFullTxt(index: AiIndex): string {
  const { site, pages } = index;
  const parts = [`# ${site.name}\n\n> ${site.description}`];
  for (const p of pages) {
    parts.push(`---\n\n## ${p.title}\nURL: ${p.url}\n\n${p.markdown}`);
  }
  return parts.join("\n\n") + "\n";
}

export function buildAiIndex(contentDir: string): IndexWithRendered {
  const projectJsonPath = path.join(contentDir, "project.json");
  const projectJson = fs.existsSync(projectJsonPath)
    ? JSON.parse(fs.readFileSync(projectJsonPath, "utf-8"))
    : {};

  const baseUrl =
    projectJson.canonical_url || projectJson.url || "https://example.com";
  const domain = baseUrl
    .replace(/^https?:\/\//, "")
    .replace(/\/.*$/, "")
    .replace(/^(www|feeds)\./, "");

  const pages = buildPages(baseUrl, contentDir);

  // Update homepage with real data
  const companyInfo = projectJson.company_info || {};
  const businessName =
    companyInfo?.name?.company_name || companyInfo?.name?.dba_name || projectJson.name || domain;
  const homepage = pages.find((p) => p.path === "");
  if (homepage) {
    homepage.title = `${businessName} — ${domain}`;
    homepage.description =
      companyInfo?.company_story?.company_history?.slice(0, 200) || "";
    homepage.markdown = `# ${businessName}\n\n${homepage.description}`;
  }

  const business = {
    name: businessName,
    legal_name: companyInfo?.name?.legal_name ?? "",
    address: companyInfo?.locations?.headquarters_address ?? "",
    phones: companyInfo?.phone_numbers ?? [],
    emails: companyInfo?.email_addresses ?? [],
    hours: companyInfo?.working_hours ?? {},
    service_areas: companyInfo?.service_areas ?? [],
    certifications: companyInfo?.credentials?.certifications ?? [],
    about:
      companyInfo?.company_story?.company_history ??
      companyInfo?.company_story?.founding_story ??
      "",
  };

  const products = readJsonDir(path.join(contentDir, "resources", "products"))
    .map((p: any) => ({
      name: p?.details?.product_name ?? "",
      url: p?.details?.product_key ?? "",
      attributes: p?.details?.filter_data ?? {},
    }))
    .filter((p) => p.name);

  const services = readJsonDir(path.join(contentDir, "resources", "sub-services"))
    .map((s: any) => ({
      name: s?.details?.service_name ?? "",
      description: s?.details?.service_description ?? "",
    }))
    .filter((s) => s.name);

  const reviewsPath = path.join(contentDir, "reviews.json");
  const reviewsRaw = fs.existsSync(reviewsPath)
    ? JSON.parse(fs.readFileSync(reviewsPath, "utf-8"))
    : {};
  const reviews = Object.values(reviewsRaw)
    .map((r: any) => ({
      reviewer: r?.review_info?.reviewer_name ?? "",
      content: r?.review_info?.review_content ?? "",
      source: r?.review_info?.source ?? "",
    }))
    .filter((r) => r.content);

  const index: AiIndex = {
    version: 1,
    generated_at: new Date().toISOString(),
    site: {
      name: businessName,
      domain,
      base_url: baseUrl,
      base_path: "",
      description: homepage?.description || business.about || "",
    },
    business,
    pages,
    products,
    services,
    reviews,
  };

  return {
    ...index,
    _llmsTxt: renderLlmsTxt(index),
    _llmsFullTxt: renderLlmsFullTxt(index),
  };
}
