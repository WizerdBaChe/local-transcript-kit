# Third-party notices

Licences checked 2026-11-22 against the installed package metadata (faster-whisper and its dependencies, in the
author's runtime) and the Hugging Face model page.

## Shipped in this repository

| Material | Where | Licence | Notes |
|---|---|---|---|
| pypinyin 0.55.0 pronunciation data (derived) | `reference-impl/src/ltm/detect/data/syllables.tsv` | MIT (Copyright (c) 2016 mozillazg, 闲耘), as shipped in the 0.55.0 package the table was built from on 2026-10-30 | The full notice ships beside it: `reference-impl/src/ltm/detect/data/NOTICE-pypinyin.txt`. Built by `reference-impl/tools/build_syllables.py` from that package's `pinyin_dict.json`. pypinyin draws its readings from the pinyin-data project; whether every upstream source of that file carries the same terms was not checked. |
| OpenCC dictionaries STCharacters and TWVariants (derived) | `reference-impl/src/ltm/detect/data/simplified_variants.tsv` | Apache-2.0 (OpenCC by BYVoid), as shipped in opencc-python-reimplemented 0.1.7 (Copyright 2016), checked 2026-11-23 against that package's metadata (License: Apache License; its LICENSE.txt is the standard text and names no copyright holder, so none is stated here beyond the package's own "Copyright 2016" line; the dictionaries originate from the OpenCC project by BYVoid). Whether the dictionaries inside it carry any further terms than the package licence was not checked | The notice with the full licence text ships beside it: `reference-impl/src/ltm/detect/data/NOTICE-opencc.txt`. Built by `reference-impl/tools/build_simplified_variants.py`: only single-character rows kept, rows whose traditional forms include the character itself dropped, Taiwan variants appended. The product never imports OpenCC and never converts the displayed text. |

Nothing else here is third-party: the product (`reference-impl/src/ltm`) uses the Python standard library only.

## Downloaded by `setup-runtime.ps1` (not in this repository)

| Material | Licence | Source |
|---|---|---|
| faster-whisper 1.2.1 | MIT | PyPI / github.com/SYSTRAN/faster-whisper |
| CTranslate2 (dependency) | MIT | PyPI |
| PyAV (dependency) | BSD-3-Clause; its wheels bundle FFmpeg libraries under their own (LGPL) terms | PyPI |
| tokenizers, huggingface_hub (dependencies) | Apache-2.0 | PyPI |
| onnxruntime (dependency, used by the VAD) | MIT | PyPI |
| Systran/faster-whisper-large-v3 weights | MIT (the Hugging Face repository's licence tag); a CTranslate2 conversion of OpenAI's whisper-large-v3 | huggingface.co/Systran/faster-whisper-large-v3 |

These are installed into `reference-impl/runtime/`, which is git-ignored. You accept their terms when you run
the script; it states the download sizes and asks first.

## Not shipped, not needed

- `ffprobe` (part of FFmpeg) must be on PATH for `decode`; install it yourself.
- The author's lexicons, frequency tables and measured recordings are not shipped (they hold other people's
  speech or a research field's vocabulary). The one example lexicon here,
  `docs/assets/demo/lexicon-campus-systems.json`, is the author's own.
