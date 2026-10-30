import { dateOf, timeOf, todayPlus, workDays } from "./format";

describe("format", () => {
  it("shows Bengaluru wall-clock time exactly as sent, regardless of browser zone", () => {
    expect(timeOf("2026-11-02T10:30:00+05:30")).toBe("10:30 am");
    expect(timeOf("2026-11-02T00:05:00+05:30")).toBe("12:05 am");
    expect(timeOf("2026-11-02T17:45:00+05:30")).toBe("5:45 pm");
    expect(timeOf(null)).toBe("");
  });
  it("formats dates with weekday", () => {
    expect(dateOf("2026-11-02T10:30:00+05:30")).toBe("Mon 2 Nov 2026");
  });
  it("adds days from the server clock, not the browser clock", () => {
    expect(todayPlus(1, "2026-10-30T09:00:00+05:30")).toBe("2026-10-31");
    expect(todayPlus(3, "2026-10-30T09:00:00+05:30")).toBe("2026-11-02");
  });
  it("lists working days", () => expect(workDays([0, 2, 6])).toBe("Mon, Wed, Sun"));
});
