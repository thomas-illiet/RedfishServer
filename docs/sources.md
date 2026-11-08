# Data provenance and integrity

[Documentation](readme.md) · [Fidelity](fidelity.md) · [Development](development.md)

## rackhands import

| Item | Reference |
|---|---|
| Repository | [Blackman99/rackhands](https://github.com/Blackman99/rackhands) |
| Imported directory | [testdata/mockups at the selected commit](https://github.com/Blackman99/rackhands/tree/a37f5726cd7c942d1214f96eeb3d7a2530abecb3/testdata/mockups) |
| Commit | `a37f5726cd7c942d1214f96eeb3d7a2530abecb3` |
| Import date | November 8, 2026 |
| Local location | `src/redfish_mock/_fixtures/` |
| Tracked files | 268: resource and step JSON files, plus the Python generator |
| License of imported files | MIT, reproduced below |

Imported JSON payloads remain byte-for-byte unchanged. The imported Python
generator has local docstrings added to its previously undocumented definitions;
its executable syntax is unchanged. Corrections and additions are applied in
memory by the engine, so default responses may differ from the fixtures.
`--raw-fixtures` disables these data additions.

Provenance and the license notice are retained here after removal of the
`vendor` directory. Neither the runtime nor the Docker build depends on
that directory.

Values are synthetic and are not validated captures from real hardware.
See [fidelity.md](fidelity.md) for vendor documents,
[inventory.md](inventory.md) for CPU/RAM choices and
[monitoring.md](monitoring.md) for readings and simulation ranges.

## Integrity check

The original 268-file import (267 JSON files plus the generator) was verified
against this canonical manifest digest:

```text
9b5908cf07adaf4cab6464e4c2842155ace1c556d9c90aab0c798b382ff36ad7
```

On November 8, 2026, docstrings were added to 26 generator definitions to apply
the repository-wide documentation requirement. No executable statement or JSON
payload was changed. The tests now check the two parts independently:

- `test_imported_source_checksums` verifies all 267 original JSON files. Each
  canonical manifest line contains the SHA-256, two spaces, its original
  `mockups/`-prefixed path, then a newline, sorted by path and encoded in UTF-8.
- `test_imported_generator_executable_syntax_is_unchanged` verifies the original
  generator AST after removing only module, class and function docstrings.
  It hashes `ast.dump(tree, include_attributes=False)` encoded in UTF-8, under
  the project's pinned Python version. Names, literals, statements and order
  remain covered. The documentation gate separately requires every docstring.

Both reference digests were derived from the verified original import before
adding the new docstrings:

```text
JSON manifest: 9d1708fdcca9c5b09f10500e3928bb917a4a2012f7d6f06e588bb0337fd10582
Generator AST: 43d8d7f8a2142f655b38c5eb04125ed821d3ef3ea6b41a5e1002bba384b33a6e
```

These checks require no network or `vendor/` directory. Changing a JSON payload,
adding or removing a fixture, or modifying generator executable syntax fails.

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
