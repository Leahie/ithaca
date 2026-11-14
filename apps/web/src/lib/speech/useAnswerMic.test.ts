import { expect, test } from 'vitest';
import { asksSomething } from './useAnswerMic';

test('a reply asks something when its last sentence is a question', () => {
  expect(asksSomething('The island seats three. Do you cook most nights?')).toBe(true);
  expect(asksSomething('Is it bright? Yes, all afternoon.')).toBe(false);
  expect(asksSomething('Which do you prefer?" ')).toBe(true);
});
