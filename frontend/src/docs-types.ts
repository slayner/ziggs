import type { Lang } from "./i18n";

export type DocsLocale = Lang;

export type Localized = Record<DocsLocale, string>;

export type DocsExample = {
  input: string;
  result: Localized;
};

export type CommandDoc = {
  id: string;
  command: string;
  category: "general" | "registration" | "economy" | "events";
  permission: Localized;
  prerequisites: Localized;
  description: Localized;
  syntax: string[];
  examples: DocsExample[];
};

export type DocsPage = {
  slug: string;
  title: Localized;
  description: Localized;
};
