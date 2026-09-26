# 0001. Sanctions are issued automatically after a one-day hold

- Status: Accepted
- Date: 2026-09-26
- Deciders: Noktah core team
- See also: [spec 009 grill ledger](../../specs/009-hub-otomasi-laporan/grill.md) G-29, G-37, G-38, G-42; Incentive Framework v2.1 §3.2, §4.3, §7.1, §7.2

## Context

Eskala's Incentive Framework v2.1 turns judged Events into points, and points into sanctions
along a fixed ladder (§3.2): teguran lisan, then SP1, SP2 and SP3, a 30-day PIP, and finally the
termination process. Violations on the §4.3 list skip the ladder and get a fixed sanction
straight away. Which rung applies depends on history the points alone don't carry: the SP
active right now, whether its 90 days have run out, and how many teguran lisan with the same
category code were given in the last 90 days.

Worked out by hand, that date arithmetic is exactly what gets missed. The Hub already reads
every judged Event from Jira (spec 009), so it can do it. The owner wanted sanctions issued
automatically, not just suggested.

A sanction is a formal HR act that affects someone's pay and job, and v2.1 puts two limits on
when one may be issued. Sanctions wait for the monthly close (§7.1: log closed at month end,
rekap on working days 1–2, penetapan on working days 2–3). An Event under appeal suspends its
sanction (§7.2).

## Decision

On working day 2 of each month, the Hub works out every Eskala staff member's sanction from
the §3.2 ladder, using the judged Events and the warnings and SPs recorded in the Hub.

- A Brand Manager is notified on Slack that the month's sanctions are ready to review.
- A manager with "Lihat poin insentif" can **hold** any sanction with a written reason until
  working day 3.
- A sanction nobody held is recorded as **issued** on working day 3, and the Hub writes its SP
  letter as a Google Doc in the restricted folder Company > HR > Surat Peringatan. The
  signature is left blank; a manager signs and hands it over.
- A sanction resting on an Event under appeal is held automatically until the appeal is
  decided.
- §4.3 direct sanctions follow the same path immediately instead of at month end, but only
  when the Event names the item ("Direct Sanction Violation" in Jira). Without it, the Hub
  never decides one.
- **"Proses PHK" is never issued by the Hub.** It is only flagged for a person to start.

## Consequences

### Positive

- The ladder is applied the same way to everyone, every month, which is what v2.1 §7.3
  (consistency audit) asks for.
- 90-day expiries, level resets and "two months running" are never missed.
- Every issued sanction traces back to named Events and a recorded hold-or-issue step.

### Negative / costs

- A wrong or missing judgement field in Jira becomes a wrong sanction unless someone holds it
  within one working day. This is why incomplete Events count nothing ("belum lengkap") and
  §4.3 needs its own field.
- Managers have to review every month within a one-day window; silence means consent.
- Warnings and SPs must be recorded in the Hub, or the ladder starts from the wrong rung.

### Neutral

- Points and sanctions count from the date v2.1 takes effect. Earlier Events are shown for
  reference only.

## Alternatives considered

- **Automatic proposal, one-click issue:** safer, but a busy month leaves sanctions unissued.
  The owner wanted them automatic.
- **Fully automatic, the moment a line is crossed:** ignores the monthly close (§7.1) and
  appeals (§7.2), and gives no one a chance to stop a wrong sanction.
- **Suggest point bands only:** v2.1's ladder depends on the active SP and same-code warning
  history, so a band no longer maps to a single sanction.
