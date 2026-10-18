from datetime import date

# --- Day count ---
appt = date(2026, 6, 11)
today = date(2026, 9, 17)
elapsed = (today - appt).days
day_n = elapsed + 1
day100 = date.fromordinal(appt.toordinal() + 99)
print("DAY COUNT: appointment 2026-06-11 -> today Day", day_n, "of 100; Day 100 =", day100.strftime("%A %d %b %Y"))

# --- Discount at today's tape ---
price = 0.040
nta = 0.1759
disc = 1 - price / nta
print("Discount @ 0.040 vs NTA 0.1759 = {:.1%}".format(disc))

# --- Bid overhang in trading days ---
bid = 48_913_200
prev_bid = 47_673_400
avg4w = 2_098_000
print("Bid overhang 48.91M sh = {:.1f} days of 4-wk avg volume (2.098M sh); delta vs 09-16: {:+.2f}M sh".format(bid / avg4w, (bid - prev_bid) / 1e6))

# --- NTA decay race (exact) ---
nta_2q_ago = 0.2030   # 4QFY26 interim (Dec-25 quarter NTA 0.2030)
q_decay = (nta / nta_2q_ago) ** 0.5 - 1
print("NTA decay: 0.2030 -> 0.1759 over 2 qtrs = {:.2%}/qtr compounded = {:.1%}/yr".format(q_decay, (1 + q_decay) ** 4 - 1))

proj_4q27 = nta * (1 + q_decay) ** 4
proj_4q28 = nta * (1 + q_decay) ** 8
print("NTA proj: 4QFY27 = {:.4f}, 4QFY28 = {:.4f}".format(proj_4q27, proj_4q28))

# --- Price at constant discount ---
print("At constant {:.1%} discount: price 4QFY28 = {:.4f}".format(disc, proj_4q28 * (1 - disc)))

# --- Discount required to hold today's price through 4QFY28 ---
req_disc_040 = 1 - price / proj_4q28
req_disc_045 = 1 - 0.045 / proj_4q28
print("To HOLD RM0.040 by 4QFY28: discount must reach <= {:.1%}".format(req_disc_040))
print("To HOLD RM0.045 by 4QFY28: discount must reach <= {:.1%}".format(req_disc_045))

# --- Sessions elapsed since question first tabled (2026-09-09) ---
print("Sessions re-issued since 2026-09-09 through today: 7 (09-09,10,11,12,13,15,16) -> today = 8th escalation; FINAL before default")