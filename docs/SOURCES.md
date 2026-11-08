# Data provenance and integrity

[Documentation](README.md) · [Fidelity](FIDELITY.md) · [Development](DEVELOPMENT.md)

## rackhands import

| Item | Reference |
|---|---|
| Repository | [Blackman99/rackhands](https://github.com/Blackman99/rackhands) |
| Imported directory | [testdata/mockups at the selected commit](https://github.com/Blackman99/rackhands/tree/a37f5726cd7c942d1214f96eeb3d7a2530abecb3/testdata/mockups) |
| Commit | `a37f5726cd7c942d1214f96eeb3d7a2530abecb3` |
| Import date | November 8, 2026 |
| Local location | `mockups/` |
| Tracked files | 268: resource and step JSON files, plus the Python generator |
| License of imported files | MIT, reproduced below |

Imported files remain unchanged. Corrections and additions are applied in
memory by the engine, so default responses may differ from the fixtures.
`--raw-fixtures` disables these data additions.

Provenance and the license notice are retained here after removal of the
`vendor` directory. Neither the runtime nor the Docker build depends on
that directory.

Values are synthetic and are not validated captures from real hardware.
See [FIDELITY.md](FIDELITY.md) for vendor documents,
[INVENTORY.md](INVENTORY.md) for CPU/RAM choices and
[MONITORING.md](MONITORING.md) for readings and simulation ranges.

## Integrity check

The `test_imported_source_checksums` test reconstructs a canonical manifest
of `.json` and `.py` files under `mockups/`, sorted by path. Each line contains
the file's SHA-256, two spaces, its project-relative path using `/`, then
a newline. The manifest is encoded in UTF-8.

The test checks the file count and the SHA-256 of this manifest:

```text
9b5908cf07adaf4cab6464e4c2842155ace1c556d9c90aab0c798b382ff36ad7
```

This reference was frozen after comparing the 268 files with the original
import checksums. It does not depend on a file in `vendor/` or network access
during tests. Changing content or adding or removing a tracked file causes
the check to fail.

Do not update this reference simply to make a test pass. A new import must
have an explicit source and commit, a review of differences and an appropriate
license notice. The upstream generator removes and recreates its directories;
to customize profiles, work on a copy passed to `--mockups-dir`.

## License of imported files

```text
MIT License

Copyright (c) 2026 Blackman99

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
