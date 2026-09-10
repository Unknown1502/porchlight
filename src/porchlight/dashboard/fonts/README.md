# Vendored typefaces

Both are self-hosted rather than loaded from a CDN. Two reasons: the container
has no guaranteed outbound network, and a coordinator's screen should not make a
third-party request every time it loads a page about fraud reports.

| File | Family | Licence | Source |
|---|---|---|---|
| `public-sans-latin.woff2` | Public Sans (variable, latin subset) | SIL Open Font License 1.1 | <https://github.com/uswds/public-sans> |
| `newsreader-latin.woff2` | Newsreader (variable, latin subset) | SIL Open Font License 1.1 | <https://github.com/productiontype/Newsreader> |

Both licences permit redistribution and embedding, including in a bundled
application, provided the licence travels with the font and the fonts are not
sold on their own. Full text: <https://openfontlicense.org/>.

Latin subsets only, which is what keeps them to 18 KB and 87 KB. If Porchlight
is ever run for a community that does not read Latin script, these need
replacing — that is a real limitation, not an oversight, and it belongs with the
jurisdiction pack work.

Every rule that uses them also names a system fallback stack, so the interface
stays legible if a font fails to load.
