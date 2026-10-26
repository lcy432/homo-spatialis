# Release and archival checklist

English · [简体中文](RELEASE.md)

The goal is to let readers find, reproduce, and cite the same version while retaining attribution and license notices. Checkboxes state completion criteria, not claims that a release or DOI already exists.

## 1. Fix a citable version

- [ ] Select the release commit and version. Metadata version `0.1.0` is a candidate until the corresponding tag and Release exist.
- [ ] Include `LICENSE`, `NOTICE`, `AUTHORS.md`, `CITATION.cff`, the [citation guide](../CITATION.en.md), [methods](METHODS.en.md), and [reproduction](REPRODUCING.en.md), without unfinished changes.
- [ ] Match citation authors, name, version, and license to the release; supply the actual release date. Omit unknown affiliations, ORCID, papers, and a DOI that has not been issued.
- [ ] In a clean checkout of the selected commit, run the offline success/rejection examples and required checks. Record commit, environment, commands, results, and checks not run.
- [ ] Confirm the public distribution contains no credentials, private user data, or unauthorized material, and retains third-party provenance and notices.

Validate citation format with:

~~~text
uvx --from cffconvert==2.0.0 cffconvert --validate -i CITATION.cff
~~~

Only record validation after the command succeeds. It checks format, not identity, originality, or findings. See [Zenodo's CFF guidance](https://help.zenodo.org/docs/github/describe-software/citation-file/).

## 2. Connect Zenodo, then create the GitHub Release

1. The repository owner signs into [Zenodo](https://zenodo.org/), connects GitHub, and enables archiving for `lcy432/homo-spatialis` in [GitHub integration settings](https://zenodo.org/account/settings/github/). The integration requires a public repository.
2. Confirm archiving is enabled, then publish the chosen commit as a tagged Release such as `v0.1.0`. Include scope, reproduction, checks, and limitations.
3. Inspect the actual archive: software type, authors, version, license, files, and DOI must match. A Git tag alone is insufficient.

See [GitHub's archival instructions](https://docs.github.com/en/repositories/archiving-a-github-repository/referencing-and-citing-content). Once enabled, new Releases trigger new archive records and DOIs.

Reuse `CITATION.cff` without creating a duplicate `.zenodo.json`. If the latter is later added, Zenodo prioritizes it, so metadata must be kept aligned. See [metadata precedence](https://help.zenodo.org/docs/github/describe-software/).

## 3. Associate the DOI with the version

- [ ] Open the DOI and verify the version record and downloadable files. Prefer the version DOI for reproducible experiments; the all-versions DOI can describe the project generally. See [versioning](https://zenodo.org/help/versioning).
- [ ] Add the issued DOI to `CITATION.cff` on the default branch with matching version, authors, and date; add it to the Release description.
- [ ] Do not move the published tag or replace archived source to add the DOI. The first automatically archived CFF can legitimately lack its own DOI; no identical-content rerelease is needed just to add it.
- [ ] For later releases, update metadata and remove the previous version's DOI before archiving. Add the new DOI after issuance.

## 4. Minimum release description

| Field | Record |
|---|---|
| Software | Name in the release citation metadata |
| Version/tag | Actual version and tag |
| Source | Full commit |
| Authors | Release `CITATION.cff` authors |
| Scope | Implemented entry points and capabilities |
| Reproduction | Guides, locked dependencies, and examples at the release tag |
| Checks | Platform, Python version, commands, results, omitted checks |
| Methods and limits | Versioned methods; distinguish engineering checks from real-world validation |
| Citation | “Archive pending” initially; replace with the issued version DOI |

Leave incomplete items explicit. The citation should lead to fixed source, the source to runnable instructions, and reports to contributions and limitations. Archiving preserves a record; it does not replace peer review or guarantee originality.
