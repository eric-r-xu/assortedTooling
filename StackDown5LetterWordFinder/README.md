green. It is the quickest way to tune or diagnose a layout whose tile styling
differs from the expected rectangular StackDown tiles. Detection checks several
brightness and color channels, so colored, rounded, highlighted, or shadowed
tile borders do not need to form a strong grayscale edge.

The detector also segments StackDown's lighter tile-face background as a
second signal. Repeated shapes at the normal tile size are preferred over
large accidental polygons spanning several overlapping tiles.
The especially light cream face used for currently playable tiles is detected
with a separate stricter mask, matching the game's exposed-tile highlight.
Small repeated contours from the printed letter glyphs are rejected before
tile-size clustering.
Shape fallbacks handle the game font's common `Z`→`A`, missing-`P`, round `O`,
narrow `I`, diagonal-stem `N`, and two-bowl `B` OCR errors after a tile has
been identified.
