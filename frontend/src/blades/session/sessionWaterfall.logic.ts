/** SES-40: name column 300 px, 200 px under an 860 px blade width. */
export function waterfallTemplate(width: number): string {
  const name = width < 860 ? 200 : 300
  return `56px 20px minmax(0,${name}px) 72px 64px minmax(0,1fr) 16px`
}
