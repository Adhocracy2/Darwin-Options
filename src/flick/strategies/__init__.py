"""Strategy registry. Each name matches a config section with an `enabled` flag."""
from .calendar import Calendar
from .credit_spread import CreditSpread
from .iron_butterfly import IronButterfly
from .iron_condor import IronCondor
from .leaps_call import LeapsCall

STRATEGIES = {s.name: s for s in (CreditSpread, IronCondor, IronButterfly, Calendar, LeapsCall)}
