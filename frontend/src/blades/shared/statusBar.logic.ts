/** SES-70, session blade only: "Turn N selected". A session with no numbered turn has nothing selected. */
export function sessionStatus(turnIx: number | null): string | null {
  return turnIx === null ? null : `Turn ${turnIx} selected`
}
