"""Detection: say where the transcript should be distrusted, and why.

The module split is not tidiness. `candidates.py` nominates and cannot reach
`signals.py`; `signals.py` imports the two structure functions from
`candidates.py` rather than re-implementing them beside the gates; `score.py` is
the only place S4 votes. The import direction IS the contract (PSM §6.25): a
comment saying "do not use S4 to nominate" is as good as the next reader's
attention, while an import that does not exist is a guarantee.

The measured reason for that guarantee: S4(a) holds for any cue within 30 s of a
badly-scored one, and the baseline has 29 such cues with roughly 40 neighbours
each -- a population of 205 against repetition's 3 candidates. A nominating S4
would emit hundreds of candidates whose only evidence is "somebody nearby decoded
badly".
"""
