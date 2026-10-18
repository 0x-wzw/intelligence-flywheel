from datetime import date
appt = date(2026, 6, 11)
today = date(2026, 9, 17)
elapsed = (today - appt).days
print("days since 11 Jun 2026:", elapsed, "-> Day", elapsed + 1, "of 100")
print("Day 100 date =", date.fromordinal(appt.toordinal() + 99))