/**
 * US stock market hours (NYSE/Nasdaq, regular session) in New York time: 9:30-16:00 on weekdays,
 * 13:00 close on early-close days, shut on exchange holidays. Holidays and early closes are listed
 * for 2026-2027 (from the published NYSE calendar); extend the lists for later years.
 */
const HOLIDAYS = new Set([
  "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
  "2026-11-26", "2026-12-25",
  "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
  "2027-11-25", "2027-12-24",
]);
const EARLY_CLOSE = new Set(["2026-11-27", "2026-12-24", "2027-11-26"]);

/** New York wall-clock parts for an instant. */
function ny(d: Date) {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", weekday: "short",
  }).formatToParts(d).map((x) => [x.type, x.value]));
  return { day: `${p.year}-${p.month}-${p.day}`, weekday: p.weekday as string, mins: +p.hour * 60 + +p.minute + +p.second / 60 };
}

const isTradingDay = (day: string, weekday: string) => !["Sat", "Sun"].includes(weekday) && !HOLIDAYS.has(day);

export type MarketClock = { open: boolean; label: string; inMs: number; early: boolean };

/** Whether the market is open now, and how long until it closes (if open) or next opens. */
export function marketClock(now = new Date()): MarketClock {
  const OPEN = 9 * 60 + 30;
  const t = ny(now);
  const close = EARLY_CLOSE.has(t.day) ? 13 * 60 : 16 * 60;
  if (isTradingDay(t.day, t.weekday) && t.mins >= OPEN && t.mins < close) {
    return { open: true, label: "closes", inMs: (close - t.mins) * 60_000, early: close < 16 * 60 };
  }
  // step forward minute by minute in 30-minute strides until a trading day's 9:30 (fast enough: < 4 days)
  for (let m = 1; m <= 6 * 24 * 60; m += 1) {
    const d = new Date(now.getTime() + m * 60_000);
    const x = ny(d);
    if (isTradingDay(x.day, x.weekday) && x.mins >= OPEN && x.mins < OPEN + 1) {
      return { open: false, label: "opens", inMs: (m - (x.mins - OPEN)) * 60_000, early: EARLY_CLOSE.has(x.day) };
    }
    if (!(isTradingDay(x.day, x.weekday) && x.mins < OPEN)) m += 29; // nothing can open in the next 30 minutes
  }
  return { open: false, label: "opens", inMs: 0, early: false };
}

export function formatCountdown(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return d ? `${d}d ${h}h ${pad(m)}m` : `${h}:${pad(m)}:${pad(sec)}`;
}
