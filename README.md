# CSV Import Doctor

Version **0.5**, development milestone 5: read-only HTTP CSV inspection.

Check a CSV before importing it into another system. The first record must be a
header. The service explains structural and data problems, preserves identifiers
as text, and reports what edge whitespace could be removed. It never edits the
uploaded source, guesses missing values or deletes records.