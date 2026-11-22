"""The human face. It may import everything under `ltm`, and it is the ONLY layer
that may print a sentence.

Every explanation this product gives a person lives here: the cancel refusal
reasons, the "this detector has no controls" line, the "population is empty" line.
The agent face gets codes for all three (INV-12), and the two are not two
implementations of one message -- `core` and `agentapi` hold the code, `ui` holds
the sentence it renders, and neither one derives the other.
"""
