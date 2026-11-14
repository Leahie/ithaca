import type { ReactNode } from 'react';

/** Inline: **bold** and `code`. Everything else is plain text (no HTML is ever injected). */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={i} className="font-semibold text-text">{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`')) return <code key={i} className="rounded bg-raised px-1 font-mono text-[0.9em]">{part.slice(1, -1)}</code>;
    return part;
  });
}

/**
 * A small, safe Markdown subset for agent-written text (area profile, memory files):
 * headings, bullet lists, block quotes, paragraphs, bold and inline code.
 */
export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  let para: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(
        <ul key={blocks.length} className="list-disc space-y-1 pl-5">
          {list.map((item, i) => (
            <li key={i}>{inline(item)}</li>
          ))}
        </ul>,
      );
      list = [];
    }
    if (para.length) {
      blocks.push(<p key={blocks.length}>{inline(para.join(' '))}</p>);
      para = [];
    }
  };
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      const level = heading[1]!.length;
      blocks.push(
        <div key={blocks.length} role="heading" aria-level={level + 1} className={level === 1 ? 'text-base font-semibold text-text' : 'pt-1 text-sm font-semibold text-text'}>
          {inline(heading[2]!)}
        </div>,
      );
    } else if (/^\s*[-*]\s+/.test(line)) {
      if (para.length) flush();
      list.push(line.replace(/^\s*[-*]\s+/, ''));
    } else if (line.startsWith('>')) {
      flush();
      blocks.push(
        <blockquote key={blocks.length} className="border-l-2 border-sun/50 pl-3 text-sun-deep">
          {inline(line.replace(/^>\s?/, ''))}
        </blockquote>,
      );
    } else if (!line.trim()) {
      flush();
    } else {
      if (list.length) flush();
      para.push(line.trim());
    }
  }
  flush();
  return <div className={`space-y-2.5 text-sm leading-6 text-muted ${className ?? ''}`}>{blocks}</div>;
}
