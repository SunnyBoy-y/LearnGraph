import {describe, expect, it} from "vitest";
import cases from "../../../backend/tests/fixtures/illustration-svg.json";
import {parseIllustrationSvg} from "./safe-illustration";

describe("static SVG security contract shared with backend", () => {
  it.each(cases)("$name", ({svg, valid}) => expect(parseIllustrationSvg(svg) !== null).toBe(valid));
  it("rejects oversized illustrations and complexity", () => {
    for (const body of ["<circle r='1'/>".repeat(100), "<g>".repeat(9) + "<circle r='1'/>" + "</g>".repeat(9), `<text>${"字".repeat(9000)}</text>`]) {
      expect(parseIllustrationSvg(`<svg viewBox="0 0 100 100">${body}</svg>`)).toBeNull();
    }
  });
  it("drops IDs and maps safe attributes into React props", () => {
    const tree = parseIllustrationSvg('<svg viewBox="0 0 100 100"><circle id="host" r="10" stroke-width="2"/></svg>');
    expect(tree?.children[0]).toMatchObject({attrs: {r: "10", strokeWidth: "2"}});
    expect(JSON.stringify(tree)).not.toContain('"id"');
  });
});
