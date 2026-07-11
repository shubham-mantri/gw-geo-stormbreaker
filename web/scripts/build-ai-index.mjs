#!/usr/bin/env node
/**
 * Generates ai-index.json, llms.txt, and llms-full.txt from sample-data/.
 * Run: node scripts/build-ai-index.mjs
 * Output: public/ai-index.json, public/llms.txt, public/llms-full.txt
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const SAMPLE = path.join(ROOT, "sample-data");
const PUBLIC = path.join(ROOT, "public");

function readJsonDir(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8")));
}

function stripHtmlTags(str) {
  return (str || "").replace(/<[^>]*>/g, "").trim();
}

// ── Build pages from cluster JSONs (no HTML dist needed) ─────────────────────
function buildPages(baseUrl) {
  const pages = [];

  // Homepage (synthetic)
  pages.push({
    path: "",
    type: "page",
    url: baseUrl,
    title: "Detroit Wastewater Treatment | Jet Aeration & Diffuser Systems",
    description:
      "Industry-leading jet aeration and diffuser systems for municipal and industrial wastewater treatment. 40+ years of engineering excellence.",
    last_modified_at: null,
    markdown:
      "# Detroit Wastewater Treatment\n\nIndustry-leading jet aeration and diffuser systems for municipal and industrial wastewater treatment. Over 40 years of engineering excellence in designing efficient, reliable aeration solutions.\n\n## Our Solutions\n- Jet Aeration Systems\n- Diffuser Systems\n- Industrial Treatment Solutions\n- Municipal Wastewater\n\nContact us for a custom engineering consultation.",
  });

  // Services
  for (const data of readJsonDir(path.join(SAMPLE, "clusters", "service"))) {
    const pi = data.page_info || {};
    const slug = data.slug;
    if (!slug) continue;
    const fd = pi.fold_data || {};
    const desc = stripHtmlTags(pi.description || "");
    const title = stripHtmlTags(pi.title || slug);

    // Build markdown from fold_data sections
    const mdParts = [`# ${title}`, desc ? `\n${desc}` : ""];
    if (fd.service_description?.description) {
      mdParts.push(`\n## Overview\n${stripHtmlTags(fd.service_description.description)}`);
    }
    if (fd.why_us?.content && Array.isArray(fd.why_us.content)) {
      mdParts.push("\n## Why Choose Us");
      for (const item of fd.why_us.content) {
        if (item.title) mdParts.push(`- **${stripHtmlTags(item.title)}**: ${stripHtmlTags(item.description || "")}`);
      }
    }
    if (fd.faq?.content && Array.isArray(fd.faq.content)) {
      mdParts.push("\n## FAQ");
      for (const item of fd.faq.content) {
        if (item.question) mdParts.push(`**Q: ${stripHtmlTags(item.question)}**\nA: ${stripHtmlTags(item.answer || "")}\n`);
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
  for (const data of readJsonDir(path.join(SAMPLE, "clusters", "category"))) {
    const pi = data.page_info || {};
    const slug = data.slug;
    if (!slug) continue;
    const title = stripHtmlTags(pi.title || slug);
    const desc = stripHtmlTags(pi.description || "");
    const fd = pi.fold_data || {};

    const mdParts = [`# ${title}`, desc ? `\n${desc}` : ""];
    if (fd.products?.content && Array.isArray(fd.products.content)) {
      mdParts.push("\n## Products");
      for (const item of fd.products.content) {
        const name = item.product_name || item.title || "";
        if (name) mdParts.push(`- ${stripHtmlTags(name)}`);
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
  for (const data of readJsonDir(path.join(SAMPLE, "clusters", "blog", "data"))) {
    const pi = data.page_info || {};
    const slug = data.slug;
    if (!slug) continue;
    const title = stripHtmlTags(pi.title || slug);
    const desc = stripHtmlTags(pi.description || "");

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

// ── Business info ────────────────────────────────────────────────────────────
function distillBusiness(companyInfo = {}) {
  return {
    name: companyInfo?.name?.company_name ?? companyInfo?.name?.dba_name ?? "",
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
}

function mapProducts(productJsons = []) {
  return productJsons
    .map((p) => ({
      name: p?.details?.product_name ?? "",
      url: p?.details?.product_key ?? "",
      attributes: p?.details?.filter_data ?? {},
    }))
    .filter((p) => p.name);
}

function mapServices(serviceJsons = []) {
  return serviceJsons
    .map((s) => ({
      name: s?.details?.service_name ?? "",
      description: s?.details?.service_description ?? "",
    }))
    .filter((s) => s.name);
}

function mapReviews(reviewsJson = {}) {
  return Object.values(reviewsJson)
    .map((r) => ({
      reviewer: r?.review_info?.reviewer_name ?? "",
      content: r?.review_info?.review_content ?? "",
      source: r?.review_info?.source ?? "",
    }))
    .filter((r) => r.content);
}

// ── Render llms.txt ──────────────────────────────────────────────────────────
const SECTION_ORDER = [
  ["service", "Services"],
  ["category", "Categories"],
  ["blog", "Blog"],
  ["page", "Other Pages"],
];

function renderLlmsTxt(index) {
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

function renderLlmsFullTxt(index) {
  const { site, pages } = index;
  const parts = [`# ${site.name}\n\n> ${site.description}`];
  for (const p of pages) {
    parts.push(`---\n\n## ${p.title}\nURL: ${p.url}\n\n${p.markdown}`);
  }
  return parts.join("\n\n") + "\n";
}

// ── Main ─────────────────────────────────────────────────────────────────────
const projectJson = JSON.parse(fs.readFileSync(path.join(SAMPLE, "project.json"), "utf-8"));
const baseUrl = projectJson.canonical_url || projectJson.url || "https://detroitsealing.com";
const domain = baseUrl.replace(/^https?:\/\//, "").replace(/\/.*$/, "").replace(/^(www|feeds)\./, "");

const pages = buildPages(baseUrl);
const business = distillBusiness(projectJson.company_info);
const products = mapProducts(readJsonDir(path.join(SAMPLE, "resources", "products")));
const services = mapServices(readJsonDir(path.join(SAMPLE, "resources", "sub-services")));
const reviews = mapReviews(
  JSON.parse(fs.readFileSync(path.join(SAMPLE, "reviews.json"), "utf-8")),
);

const homepage = pages.find((p) => p.path === "");

const index = {
  version: 1,
  generated_at: new Date().toISOString(),
  site: {
    name: business.name || projectJson.name || domain,
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

// Ensure public/ exists
if (!fs.existsSync(PUBLIC)) fs.mkdirSync(PUBLIC, { recursive: true });

fs.writeFileSync(path.join(PUBLIC, "ai-index.json"), JSON.stringify(index, null, 2));
fs.writeFileSync(path.join(PUBLIC, "llms.txt"), renderLlmsTxt(index));
fs.writeFileSync(path.join(PUBLIC, "llms-full.txt"), renderLlmsFullTxt(index));

console.log(`✓ ai-index.json (${index.pages.length} pages, ${products.length} products, ${services.length} services, ${reviews.length} reviews)`);
console.log(`✓ llms.txt (${renderLlmsTxt(index).length} chars)`);
console.log(`✓ llms-full.txt (${renderLlmsFullTxt(index).length} chars)`);
