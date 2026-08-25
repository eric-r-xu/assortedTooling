# Lettergrams solver

`lettergrams_solver.py` searches for connected 5×5 crossword layouts, uses every
tile exactly once, validates every horizontal and vertical word, and applies DL,
TL, DW, and TW bonuses. Supply the rack letters on the command line with
`--letters`.

Use a plain-text Scrabble lexicon with one word per line. NWL is appropriate for
North American play; CSW is appropriate for international play.

```bash
python3 lettergrams_solver.py --letters QUIDNOGYHSEC \
  --dictionary /path/to/NWL.txt
```

The default search is bounded so it finishes in a practical amount of time. For
a more thorough search, increase its time and beam width:

```bash
python3 lettergrams_solver.py --dictionary /path/to/NWL.txt \
  --letters QUIDNOGYHSEC \
  --time-limit 120 --beam-width 20000 --results 10
```

For another puzzle, give the rack and repeat `--bonus` for every premium square.
Rows and columns are 1-based:

```bash
python3 lettergrams_solver.py --dictionary /path/to/CSW.txt \
  --letters ABCDEFGHIJKL \
  --bonus DW@1,2 --bonus TL@3,2 --bonus DL@3,5 --bonus TW@5,4
```

`--exhaustive --time-limit 0` disables heuristic pruning. It proves optimality
when it finishes, but can take a very long time for a large dictionary.

Run the tests with:

```bash
python3 -m unittest -v
```
