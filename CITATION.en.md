# Citation, attribution, and use

English · [简体中文](CITATION.md)

If you use Homo spatialis in research, please cite the software version used, acknowledge its authors, and cite underlying data separately. [CITATION.cff](CITATION.cff) maintains citation metadata.

## How to cite

The software is **Homo spatialis · LLMs Becoming Us**, with **Chengyu Liu** as its current software author. Use the name, authors, and version in the `CITATION.cff` distributed with the version you used. See [authors](AUTHORS.en.md), [methods](docs/METHODS.en.md), and [references](docs/REFERENCES.en.md).

- **Archived release:** copy the citation from its Zenodo record and use that version's DOI. Record the full commit, pack, configuration, planner, and seeds in the methods or experiment record.
- **Unreleased code:** cite the repository with the full commit and access date. A moving `main` does not identify the code used. Preserve an identifiable version or patch if local modifications were involved.
- **Your modified version:** cite the original project and identify your branch/archive, version, and substantive modifications.

Repository-level BibLaTeX without a DOI:

~~~bibtex
@software{liu_homo_spatialis,
  author = {Liu, Chengyu},
  title = {Homo spatialis · LLMs Becoming Us},
  url = {https://github.com/lcy432/homo-spatialis}
}
~~~

Add the actual commit and access date for an experiment. No publication date, release, paper, or DOI is invented here. Traditional BibTeX styles that lack `@software` may use `@misc`.

A version field alone is not evidence of a release. Check the matching GitHub Release, tag, and archive; do not insert a placeholder DOI. GitHub's [citation-file support](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files) provides formatted citations from `CITATION.cff` on the default branch. This guide explains which object to cite rather than maintaining a second version/DOI record.

## What to attribute

[AUTHORS](AUTHORS.en.md) records initiation and core contributions; source, Git history, and [decisions](DECISIONS/README.md) record implementation and evolution. Cite a versioned methods or design document when referring to a specific mechanism.

For city data, distinguish original publishers, preparation/mapping contributors, and data versions. Citing this software does not replace citations to underlying data and research. See [data provenance](docs/DATA.en.md) and the [contribution process](CONTRIBUTING.md).

## License duties and scholarly citation

The operative terms are [LICENSE](LICENSE) and relevant [NOTICE](NOTICE) contents. This explanation does not amend them.

| Use | Duties and requests |
|---|---|
| Copying, modifying, redistributing code | Follow applicable Apache-2.0 terms: license copy, prominent notices of modified files, applicable copyright notices, and Section 4 NOTICE treatment |
| Using software or methods in research | We request citation of the actual version and methods; this adds no license condition |
| Reusing third-party data, dependencies, or assets | Follow their own licenses and provenance requirements |
| Commercial use or modified releases | Apache-2.0 permits commercial use and does not require every modification to be published |

Apache-2.0 does not guarantee scholarly citation or require author display on every execution. See [Section 4](https://www.apache.org/licenses/LICENSE-2.0). Copyright generally protects expressions such as code and prose rather than ideas, methods, or mathematical concepts themselves; see the [WIPO explanation](https://www.wipo.int/en/web/copyright/faq-copyright). These records establish attribution and traceability, not a bar on independent implementations.

## Preserve contribution records

A release fixes source, author statements, methods, reproduction, and limitations together and preserves them through archiving. See the [release checklist](docs/RELEASE.en.md). A DOI is not peer review, an originality certificate, or proof of predictive validity.

For missing attribution or apparently noncompliant copying, preserve the page, version, date, and corresponding code before seeking correction. Scholarly attribution and license compliance are separate questions; actual terms, evidence, and applicable law govern a dispute.
