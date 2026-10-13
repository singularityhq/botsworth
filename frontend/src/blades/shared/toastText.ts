/**
 * A toast line with bold runs (SES-71: figures 500). Strings are plain text; `{ b }` is a bold figure (mono, tabular);
 * `{ b, plain: true }` is bold prose, such as a row name, set in the text face.
 */
export type Rich = (string | { b: string; plain?: true })[]
