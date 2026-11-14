import { describe, expect, it } from 'vitest';
import { speakable, speechChunks } from './provider';

describe('speechChunks', () => {
  it('starts with one sentence, then packs whole sentences up to the limit', () => {
    const text = 'Welcome home. The living room is 3.5 m wide! It gets sun all afternoon. The kitchen faces north?';
    expect(speechChunks(text, 60)).toEqual(['Welcome home.', 'The living room is 3.5 m wide! It gets sun all afternoon.', 'The kitchen faces north?']);
  });

  it('keeps every part under the limit, splitting a long sentence at spaces', () => {
    const long = Array.from({ length: 50 }, (_, i) => `word${i}`).join(' ');
    const parts = speechChunks(`${long}.`, 40);
    expect(parts.every((p) => p.length <= 40)).toBe(true);
    expect(parts.join(' ')).toBe(`${long}.`);
    expect(speechChunks('x'.repeat(25), 10)).toEqual(['xxxxxxxxxx', 'xxxxxxxxxx', 'xxxxx']);
  });

  it('has nothing to say for empty text', () => {
    expect(speechChunks('   ')).toEqual([]);
  });
});

describe('speakable', () => {
  it('drops markdown that reads badly aloud', () => {
    expect(speakable('**Sunny** room, see [the plan](/plan).\n\n```json\n{}\n```')).toBe('Sunny room, see the plan.');
  });
});
