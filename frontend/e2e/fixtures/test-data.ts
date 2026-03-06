export const TEST_PROJECT_ID = process.env.PLAYWRIGHT_TEST_PROJECT_ID || ''
export const TEST_SEQUENCE_ID = process.env.PLAYWRIGHT_TEST_SEQUENCE_ID || ''

export const EDITOR_URL =
  TEST_PROJECT_ID && TEST_SEQUENCE_ID
    ? `/project/${TEST_PROJECT_ID}/sequence/${TEST_SEQUENCE_ID}`
    : ''
