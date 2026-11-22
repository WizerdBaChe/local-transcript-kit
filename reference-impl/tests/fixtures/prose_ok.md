# The known-false control: prose that discusses the boundary

This file exists so the vocabulary check can be shown NOT to fire on text that
merely talks about the separation. It is the other half of
`layering_bad.py_txt`: one fixture proves the check can see a violation, this
one proves it is a check rather than a `return False`.

The separation R8 draws is between this product and `media-fetch-pipeline`. That
product keeps its analyses under a folder it calls 分析, its subtitle files under
字幕檔 and its plain text under 文字檔; its intake is `_inbox`, the material it
considers worth keeping is under `_reference`, its run marker is `_source.json`,
and its output root is `D:/output/MediaGrabbed`. Older transcripts of the kind it
produced are sometimes called 逐字稿, and its caption folder has been called
`_captions` in places.

None of those names may appear in a file that executes or ships here — as a path
component or as a string literal. **This file is prose, and prose is exempt on
purpose**: a boundary nobody may describe cannot be reviewed, and the first draft
of the rule said "no file in this repository", which the rulings file itself
violated in the commit that introduced it.

The reason the check matches path components and string literals rather than free
substrings is in this paragraph too: 分析 and 字幕檔 are ordinary Chinese words.
A substring scan fires on any sentence that discusses them — it did, on the PIM's
own §9.4, the first time the check was run — and a gate that cries wolf on
correct text is one readers learn to skip.
