# Third-party sticker assets

The root MIT license covers LearnGraph code. It does not replace the licenses of
the third-party images listed below. Original image bytes are redistributed
without modification. Chinese semantic annotations are maintained by LearnGraph
and are not official descriptions from the upstream projects.

## Microsoft Fluent Emoji

- Copyright (c) Microsoft Corporation.
- License: MIT; full text: `backend/app/assets/stickers/LICENSE`.
- Source: https://github.com/microsoft/fluentui-emoji
- Pinned revision: `1ffb34c752ecf5d402f04cfb4b392c77f57c54bc`.
- Files: `reaction-*.png` and `market-*.png`.

## Google Noto Emoji image assets

- Copyright 2013 Google, Inc. All Rights Reserved.
- License: Apache-2.0; full text: `backend/app/assets/stickers/NOTO-LICENSE.txt`.
- Original upstream image license notice:
  `backend/app/assets/stickers/NOTO-NOTICE.txt`.
- Source: https://github.com/googlefonts/noto-emoji
- Pinned revision: `e20cbc2bbec1926686be9f9bee7d1d2cfa1fea0e`.
- Files: `noto-*.png`, from the upstream `2D/png/128/` image directory.
- No Noto fonts are included. The upstream README distinguishes Apache-2.0 image
  resources from SIL Open Font License fonts. No flag assets are included.

## OpenMoji image assets

- Attribution: OpenMoji, https://openmoji.org, and the individual contributors
  recorded in `backend/app/assets/stickers/market.json`.
- License: CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/.
- Full text: `backend/app/assets/stickers/OPENMOJI-LICENSE.txt`.
- Source: https://github.com/hfg-gmuend/openmoji
- Pinned revision: `aeb8bb3a59e2de39c754ac79180c8131c906acea`.
- Files: `openmoji-*.png`, from the upstream `color/618x618/` directory.
- Original PNG files are unchanged. Posters are resized for display. Adaptations
  of these images must comply with CC BY-SA 4.0, including attribution, indication
  of changes, and ShareAlike where applicable. This asset license is not a
  declaration that LearnGraph application code is CC BY-SA.

## Distribution and private imports

`market.json` records each original path, image SHA-256, source revision, license,
and attribution. Downloaded packs include the full applicable license and a
NOTICE with source and per-image credits. Import/export preserves supplied
LICENSE and NOTICE documents. User-supplied notices are provenance claims, not
verification of redistribution rights. Private imports are never automatically
published into the market.

These sources require compliance with their published licenses, not an API key
or a vendor's per-application API approval. No WeChat scraping or unapproved
GIPHY/Tenor redistribution is part of this integration.
