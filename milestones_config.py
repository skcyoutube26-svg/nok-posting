"""Switch for the scheduled Reel milestone reads. Stays False until the Nok master turns it on.
To turn on: set MILESTONES_ENABLED = True here AND uncomment the schedule lines in .github/workflows/stats.yml.
The test suite checks that the two agree, so flipping one without the other is caught.
"""
MILESTONES_ENABLED = True
