import { expect, test } from 'vitest';
import { sentenceEnd } from './useSpeakGuide';

test('a sentence ends at its closing mark, not inside a number', () => {
  expect(sentenceEnd('Here is the kitchen. The island')).toBe(21);
  expect(sentenceEnd('It is 3. 5 m wide and')).toBe(0);
  expect(sentenceEnd('Still writing this one')).toBe(0);
  expect(sentenceEnd('"Really?" Yes. And')).toBe(15);
});
