import DOMPurify from "dompurify";
import { marked } from "marked";


export function renderMarkdown(text) {
  if (!text) return "";
  return DOMPurify.sanitize(marked.parse(text, { breaks: true }));
}
