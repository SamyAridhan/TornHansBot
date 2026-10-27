"""Background jobs. Each run(ctx) does one pass and returns seconds until it
wants to run again; the app loop schedules them."""

from . import bars, depart, invest, landing, oc, pin, schedule

# order matters a little: bars first (fresh snapshot for everything else)
ALL = [("bars", bars.run), ("landing", landing.run), ("depart", depart.run),
       ("oc", oc.run), ("schedule", schedule.run), ("pin", pin.run),
       ("invest", invest.run)]
