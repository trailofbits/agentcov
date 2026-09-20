# Homebrew formula (proposal)

`agentcov.rb` is a proposed formula for distributing agentcov through a
Homebrew tap, so that:

```sh
brew install trailofbits/tools/agentcov
```

works for users who standardize on Homebrew. It is a proposal only: whether to
host a tap, and under which organization and repository name, is a maintainer
decision. Nothing in this repository consumes this file.

Notes for whoever adopts it:

- The formula installs the pure-Python wheel from PyPI rather than the sdist,
  because building the sdist requires the `uv_build` backend, which Homebrew's
  isolated builds do not provide. agentcov has no runtime dependencies, so the
  wheel needs no `resource` blocks.
- On each release, the `url` and `sha256` need to be bumped to the new wheel.
  `https://pypi.org/pypi/agentcov/json` carries both values, so the release
  workflow could open an automated bump PR against the tap, mirroring what
  `brew bump-formula-pr` does.
- homebrew-core is a separate, later question: it has notability requirements
  a young project does not meet yet, and a tap serves the same users in the
  meantime.
