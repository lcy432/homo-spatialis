# References and design foundations

English · [简体中文](REFERENCES.md)

[Documentation](INDEX.en.md) · [Research](RESEARCH.en.md) · [Life-simulation methods](METHODS.en.md)

This guide explains how prior work informs the questions, methods, and comparisons. Current implementation is described in the [index](INDEX.en.md); design choices are recorded in [decisions](../DECISIONS/README.md).

<a id="spatial-economics"></a>
## Comparing cities for particular people

Spatial-equilibrium indifference applies to marginal movers, not every individual. Baskets, skill returns, taxes, amenities, and migration frictions jointly affect a person's choices. These effects can point in different directions; the project asks how they combine for a given person.

| Work | Relevance |
|---|---|
| Roback (1982), *Wages, Rents, and the Quality of Life*; Rosen (1979), *Wage-based Indexes of Urban Quality of Life* | Wages, housing costs, and amenities jointly determine location value: the spatial-equilibrium background. |
| [Gibbons, Overman, Resende (2011), *Real Earnings Disparities in Britain*](https://cep.lse.ac.uk/pubs/download/sercdp0065.pdf) | Real earnings and compensating differentials help distinguish marginal equilibrium from a particular person's circumstances. |
| [Moretti (2013), *Real Wage Inequality*](https://eml.berkeley.edu/~moretti/inequality.pdf) | Local living costs explain at least 22% of the growth in the US college wage premium from 1980 to 2000. Wage gaps need to be read with local costs. |
| [Handbury (2021), *Are Poor Cities Cheap for Everyone? Non-Homotheticity and the Cost of Living Across U.S. Cities*](https://realestate.wharton.upenn.edu/wp-content/uploads/2022/04/w836.pdf) ([author manuscript](https://www.jessiehandbury.com/papers/Handbury_NHP_currentpublic.pdf)) | Product availability and nonhomothetic food demand change cost comparisons across income groups. Personal baskets and future variety adjustments draw on this; three broad price indices do not capture the full product assortment. |
| [Albouy (2009), *The Unequal Geographic Burden of Federal Taxation*](https://www.journals.uchicago.edu/doi/10.1086/605309) | The published estimate puts identical workers' federal taxes about 27% higher in above-average-wage cities than below-average-wage cities. Taxes change the real value of nominal earnings. |
| [Diamond (2016), *The Determinants and Welfare Implications of US Workers’ Diverging Location Choices by Skill: 1980–2000*](https://www.aeaweb.org/articles?id=10.1257/aer.20131706) | Wages, rents, amenities, and sorting by skill jointly affect welfare differences, motivating individual skill/preference comparisons. |
| [Diamond, Moretti (August 2024 draft), *Where is Standard of Living the Highest? Local Prices and the Geography of Consumption*](https://web.stanford.edu/~diamondr/DiamondMoretti.pdf) | The August 2024 draft compares prices and consumption across income and education groups. Consumption outcomes remain distinct from utility including nonmarket amenities. |
| Kaplan, Schulhofer-Wohl (2017), *Inflation at the Household Level* | Household-specific inflation provides background for preserving personal consumption patterns. |

<a id="ai-preferences"></a>
## Can LLMs preserve individual differences?

These papers motivate controlled conditions and group-level evaluation. Their tasks are not the project's three-year city life; city choices and long-term actions still require task-specific tests.

| Work | Relevance |
|---|---|
| [*Homo Silicus*](https://arxiv.org/html/2301.07543v2) | Assigned information and preferences allow controlled experiments on model economic choices. |
| [Kim et al., *Can an LLM Learn Preferences from Choice Data?* (v3)](https://arxiv.org/html/2401.07345v3) | Known-preference choice generation, learning, and independent evaluation motivate measuring errors and losses by preference component and group. |
| [*Synthetic Personalities*](https://arxiv.org/html/2606.04592v1) | Input material and information depth affect personal simulation, motivating comparisons of profile collection and content. |
| [*Distribution-First Population Simulation*](https://arxiv.org/html/2607.18310v1) | Population distributions and individual accuracy are different goals; averages can hide heterogeneity. |
| [Zhao et al. (2025), *Do LLMs Recognize Your Preferences?* (PrefEval)](https://arxiv.org/abs/2502.09597) | Preference extraction, memory, and actual adherence should be evaluated separately. |

<a id="agent-simulation"></a>
## Agents living in environments

These systems inform architecture, not a claim that their rules or ours are ground truth. Calibration of environment rules is itself a [research question](RESEARCH.en.md#research-questions).

| Work | Relevance |
|---|---|
| [Park et al. (2023), *Generative Agents: Interactive Simulacra of Human Behavior*](https://arxiv.org/abs/2304.03442) | Memory and ongoing action provide a design reference for life agents. |
| [*AgentSociety*, §3.6, §4.1, §4.4](https://arxiv.org/html/2502.08691v2) ([code](https://github.com/tsinghua-fib-lab/AgentSociety)) | Actions, feedback, and state updates are separated; the environment handles accounts, costs, and constraints. Here the LLM plans and the economic model executes. |
| [*Agentopia: Long-Term Life Simulation and Learning in Agent Societies*, §4.1](https://arxiv.org/html/2606.07513v1) | Ongoing life, periodic settlement, and multidimensional outcomes inform the workflow. This project defines its own scores and clone aggregation rather than adopting the paper's life reward. |
| [Feng et al., *CityBench* (2025 version)](https://arxiv.org/abs/2406.13945v3) | Real-city evidence becomes environment objects before being exposed through action interfaces. |
| [Xia et al. (2026), *Empirical Grounding Improves the Realism of LLM Agents Simulating Human Behavior During Disruptions*](https://arxiv.org/abs/2607.17437) | Evidence enters structured attributes and constraints; data fitting and behavior validation remain separate. |
| [Santos et al. (2026), *When Plausible Is Not Realistic*](https://arxiv.org/abs/2606.13835) | Spatial, temporal, and behavioral outcomes should be checked separately; a plausible narrative is not necessarily realistic. |
| [Koaik et al. (2026), *LLM Powered Social Digital Twins*](https://arxiv.org/abs/2601.06111v2) | Validate environment evidence, behavior, and evaluation separately, and separate calibration from validation data. |
| [Xu et al. (2026), *Towards Fully Automated City Operations: Integrating Agentic AI with Urban Digital Twins*](https://discovery.ucl.ac.uk/id/eprint/10225208/1/Batty_ssrn-5596992.pdf) | Workflow organization, data processing, and specialist computation remain distinct architectural responsibilities. |
| [National Academies et al. (2024), *Foundational Research Gaps and Future Directions for Digital Twins*](https://www.nationalacademies.org/read/26894/chapter/2) | Background on data updates, model validation, and decision feedback. This project currently describes itself as data-supported life simulation. |
| [Zhang et al., *Agentic Economic Modeling* (2026 revision)](https://arxiv.org/abs/2510.25743v4) | A direction for future calibration against human behavior and independent validation. |

<a id="interviews"></a>
## User input and interviews

Interview input is planned for V3. Preserve original statements and distinguish explicit answers, model inferences, and confirmed profiles. Compare interviews with current input on the same task and evaluation rules.

| Work | Relevance |
|---|---|
| [Radlinski et al. (2019), *Coached Conversational Preference Elicitation: A Case Study in Understanding Movie Preferences*](https://aclanthology.org/W19-5941/) | Concrete experiences and reasons for liking or disliking something can clarify preferences. |
| [Handa et al. (2024), *Bayesian Preference Elicitation with Language Models* (OPEN)](https://arxiv.org/abs/2403.05534v1) | Questions can reduce uncertainty about trade-offs. Inferences must remain distinct from explicit answers; active questioning and Bayesian elicitation are future directions. |
| [Park et al., *LLM Agents Grounded in Self-Reports Enable General-Purpose Simulation of Individuals* (2026 revision)](https://arxiv.org/abs/2411.10109v3) | Previously titled Generative Agent Simulations of 1,000 People. Surveys and interviews are comparable, complementary information sources; profile meaning and collection method are separate design choices. |
| [Jain et al. (2026), *Decisive*](https://aclanthology.org/2026.acl-long.1465/) | Separate option facts from personal trade-offs. Pairwise choices provide evidence without uniquely determining weights. |
| [Yang et al. (2022), *Eliciting risk preferences that predict risky health behavior*](https://pmc.ncbi.nlm.nih.gov/articles/PMC9305924/) | Question format and wording affect risk responses. Current health answers inform planning rather than directly estimating illness probabilities or risk-aversion parameters. |

<a id="data-mapping"></a>
## From statistics to opportunities

Two approaches guide opportunity generation: cover plausible combinations and calibrate locally, or generate from data-supported conditional relationships. Past residents' choices are distinct from a new person's opportunities. See [intake](design/intake-02-real-data.en.md) and [mapping design](design/e3b-mapping-and-daily-support.en.md).

| Work | Relevance |
|---|---|
| [Xu et al. (2025), *Developing an Automated Microscopic Traffic Simulation Scenario Generation Tool* (RealTwin)](https://doi.org/10.1177/03611981251349433) | Separate raw sources, common facts, scenario mappings, and simulator adapters while preserving links between objects. |
| [Sun, Erath (2015), *A Bayesian network approach for population synthesis*](https://doi.org/10.1016/j.trc.2015.10.010) | Conditional distributions represent attribute dependence and inform opportunity generation. |
| [Ye et al. (2017), *Population Synthesis Based on Joint Distribution Inference Without Disaggregate Samples*](https://www.jasss.org/20/4/16.html) | Cross-tabulations and constraints support reconstruction and calibration; marginals alone do not identify joint dependence. |
| [Jutras-Dubé et al. (2024 version), copula-based transfer generation](https://arxiv.org/abs/2302.09193v3) | A candidate for sparse-data cities and international expansion; transferred dependence requires independent validation. |
| [Kukic, Bierlaire (2025), *Adaptive synthetic generation using one-step Gibbs Sampler*](https://transp-or.epfl.ch/documents/technicalReports/KukicBier2025.pdf) | A reference for later data updates and synthetic generation. |
| [Rosen (1974), *Hedonic Prices and Implicit Markets: Product Differentiation in Pure Competition*](https://doi.org/10.1086/260169) | Housing prices reflect multiple attributes. Housing itself, local amenities, and transport should remain separate; rent is one source of evidence about quality. |
| [Albouy (2016), *What Are Cities Worth? Land Rents, Local Productivity, and the Total Value of Amenities*](https://www.nber.org/papers/w14981) | Wages and housing costs also reflect productivity, location, and taxes. Quality inference must account for these; the link is the corresponding working-paper page. |
| [Sen et al., *MoveOD: Synthesizing Origin-Destination Commute Distribution from U.S. Census Data* (v2)](https://arxiv.org/html/2510.18858v2) | LODES residence–work links, ACS commute distributions, and networks inform commuting. The current implementation starts with area-pair relations. |
| [Fay, Herriot (1979), *Estimates of Income for Small Places: An Application of James-Stein Procedures to Census Data*](https://math.umd.edu/~plahiri/SAE/Papers/FayHerriot1979.pdf) | Sparse local estimates can borrow broader samples while retaining uncertainty and missingness. |
| [del Rio-Chanona et al. (2021), *Occupational mobility and automation: a data-driven network model*](https://pmc.ncbi.nlm.nih.gov/articles/PMC7879770/) | Occupations have different transition paths and barriers, represented here by existing abilities, training outputs, and job requirements. |
| [Waddell (2002), *UrbanSim: Modeling Urban Development for Land Use, Transportation, and Environmental Planning*](https://doi.org/10.1080/01944360208976274) | Explicit spatial objects connect housing, employment, and transport. |

<a id="data-sources"></a>
## Data and methods sources

Acquired sources, fields, transformations, and scope are established by build records. This reading list does not replace provenance or publisher licenses.

| Work | Relevance |
|---|---|
| [O*NET database](https://www.onetcenter.org/database.html) | Occupations and skill requirements. |
| [BEA regional price parities](https://www.bea.gov/data/prices-inflation/regional-price-parities-state-and-metro-area/) | Regional price levels and adjustments to sourced base prices; indices are not item prices. |
| [EPA Smart Location](https://www.epa.gov/smartgrowth/smart-location-mapping) | Transport and accessibility source selection. |
| [FBI 2024 statistical release](https://www.fbi.gov/news/press-releases/fbi-releases-2024-reported-crimes-in-the-nation-statistics) | Crime coverage and statistical conventions; nonreporting is distinct from zero events. |
| [NIST reliability handbook](https://www.itl.nist.gov/div898/handbook/apr/section1/apr161.htm) | Separate service life and random failure. Constant hazard can approximate failure, but product parameters need independent evidence. |

<a id="measurement"></a>
## Choice models and measurement

The mixed logit, recovery, and simulated integration in [research methods](RESEARCH.en.md) primarily draw on [Train, Discrete Choice Methods with Simulation](https://eml.berkeley.edu/books/train1201.pdf).

Further background includes McFadden (1978), *Modelling the Choice of Residential Location*; Bayer, Ferreira, and McMillan (2007); Kennan and Walker (2011); Lenk, DeSarbo, Green, and Young (1996), *Hierarchical Bayes conjoint analysis: recovery of partworth heterogeneity from reduced experimental designs*; Louviere, Flynn, and Marley, *Best-Worst Scaling*; Schwartz's Portrait Values Questionnaire; WHOQOL-BREF; and OECD Better Life Index and Regional Well-being.

Project weights and scores are its own definitions, not direct implementations of those scales' complete measurement models. Population-synthesis methods do not turn wages into hiring probabilities, rents into quality, or historical occupational transitions into causal training effects. See the [parameter register](design/parameter-assumption-register.en.md).

## Reading leads still to verify

Earlier notes mention *Polypersona: Persona-Grounded LLM for Synthetic Survey Responses* and *Do large language models shop like people? Comparing GPT and human food choices using discrete choice experiments*. These remain reading leads, not evidence supporting current design claims.

See the [design index](design/README.md), [decisions](../DECISIONS/README.md), and [history (Chinese)](history/README.md).
